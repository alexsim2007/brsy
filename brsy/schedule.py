"""Read-only adapters for the public MMCS and ICTIS timetables."""
import asyncio
import json
import re
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from weakref import WeakValueDictionary

import httpx

MOSCOW = timezone(timedelta(hours=3))
PROVIDERS = [
    {"id": "mmcs", "name": "Мехмат · ИММиКН", "url": "https://schedule.sfedu.ru/"},
    {"id": "ictis", "name": "ИКТИБ", "url": "https://ictis.sfedu.ru/schedule/"},
]
DEGREES = {"bachelor": "Бакалавриат / специалитет", "master": "Магистратура", "postgraduate": "Аспирантура"}


class ScheduleError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def monday(day):
    return day - timedelta(days=day.weekday())


def group_name(group):
    name = group.get("name")
    return f"{name + ' · ' if name and name != 'NULL' else ''}{group['num']} группа"


@dataclass
class Cached:
    value: object
    updated: float
    checked: float
    stale: bool = False


class ScheduleClient:
    def __init__(self, client: httpx.AsyncClient | None, *, demo=False):
        self.client = client
        self.demo = demo
        self.cache = OrderedDict()
        self.locks = WeakValueDictionary()
        self.network = asyncio.Semaphore(3)

    async def cached(self, key, ttl, loader):
        def fresh():
            item = self.cache.get(key)
            if item and time.time() - item.checked < (30 if item.stale else ttl):
                self.cache.move_to_end(key)
                return item
        item = fresh()
        if item:
            return item
        lock = self.locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[key] = lock
        async with lock:
            item = fresh()
            if item:
                return item
            try:
                value = await loader()
                item = Cached(value, time.time(), time.time())
            except ScheduleError:
                old = self.cache.get(key)
                if not old or time.time() - old.updated > 86400:
                    raise
                item = Cached(old.value, old.updated, time.time(), True)
            self.cache[key] = item
            self.cache.move_to_end(key)
            while len(self.cache) > 96:
                self.cache.popitem(last=False)
            return item

    async def request(self, url, *, params=None, body=None):
        try:
            async with self.network, self.client.stream(
                "POST" if body is not None else "GET", url, params=params, json=body,
                timeout=httpx.Timeout(15, connect=5), follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 4_000_000:
                        raise ScheduleError("Сервис расписания вернул слишком большой ответ.")
            result = json.loads(content)
            # ICTIS sometimes returns a JSON string containing the JSON document.
            for _ in range(2):
                if isinstance(result, str):
                    result = json.loads(result)
            if not isinstance(result, (list, dict)):
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ScheduleError("Сервис расписания временно недоступен. Попробуй немного позже.") from None

    async def catalogue(self, provider, course=None, query=""):
        if provider not in ("mmcs", "ictis"):
            raise ScheduleError("Неизвестное подразделение.", 422)
        if self.demo:
            courses = [{"id": 1, "name": "Бакалавриат · 1 курс"}] if provider == "mmcs" else []
            groups = [{"id": "1", "name": "Демо · 1 группа"}, {"id": "2", "name": "Демо · 2 группа"}]
            return {"providers": PROVIDERS, "courses": courses, "groups": groups, "demo": True}
        stale = False
        if provider == "mmcs":
            async def load_courses():
                rows = await self.request("https://schedule.sfedu.ru/APIv1/grade/list")
                try:
                    return [{"id": int(row["id"]), "name": f"{DEGREES.get(row['degree'], row['degree'])} · {row['num']} курс"} for row in rows]
                except (KeyError, TypeError, ValueError):
                    raise ScheduleError("Не удалось прочитать список курсов.") from None
            courses = await self.cached("mmcs:courses", 3600, load_courses)
            stale = courses.stale
            groups = []
            if course is not None:
                if course not in [c["id"] for c in courses.value]:
                    raise ScheduleError("Курс не найден.", 404)
                async def load_groups():
                    rows = await self.request(f"https://schedule.sfedu.ru/APIv1/group/forGrade/{course}")
                    try:
                        return [{"id": str(g["id"]), "name": group_name(g)} for g in rows]
                    except (KeyError, TypeError):
                        raise ScheduleError("Не удалось прочитать список групп.") from None
                group_data = await self.cached(("mmcs:groups", course), 3600, load_groups)
                stale |= group_data.stale
                groups = [g for g in group_data.value if query.casefold() in g["name"].casefold()]
            courses = courses.value
        else:
            courses, groups = [], []
            if len(query.strip()) >= 2:
                async def load_groups():
                    rows = await self.request("https://schedule.rdcenter.ru/api/ictis/groups", params={"query": query.strip(), "size": 50})
                    try:
                        return [{"id": str(g["personId"]), "name": g["name"]} for g in rows]
                    except (KeyError, TypeError):
                        raise ScheduleError("Не удалось прочитать список групп ИКТИБ.") from None
                group_data = await self.cached(("ictis:groups", query.strip().casefold()), 600, load_groups)
                groups, stale = group_data.value, group_data.stale
        return {"providers": PROVIDERS, "courses": courses, "groups": groups, "stale": stale, "demo": False}

    async def validate_group(self, provider, group_id, course=None, name=""):
        catalogue = await self.catalogue(provider, course, name if provider == "ictis" else "")
        group = next((g for g in catalogue["groups"] if g["id"] == group_id), None)
        if group is None:
            raise ScheduleError("Группа не найдена. Выбери её из списка.", 422)
        return {"provider": provider, "group_id": group_id, "group_name": group["name"], "course": course if provider == "mmcs" else None}

    async def week(self, preference, day=None):
        today = datetime.now(MOSCOW).date()
        start = monday(day or today)
        if abs((start - monday(today)).days) > 56:
            raise ScheduleError("Можно посмотреть расписание на восемь недель назад и вперёд.", 422)
        if self.demo:
            events = demo_events(start, preference["group_id"])
            return pack(preference, start, events, time.time(), demo=True)
        provider, group = preference["provider"], preference["group_id"]
        if provider == "mmcs":
            async def load_week_type():
                value = await self.request("https://schedule.sfedu.ru/APIv1/week")
                if not isinstance(value, dict) or value.get("week") not in (0, 1):
                    raise ScheduleError("Не удалось определить верхнюю или нижнюю неделю.")
                return {"week": value["week"], "monday": monday(datetime.now(MOSCOW).date())}
            async def load_group():
                value = await self.request(f"https://schedule.sfedu.ru/APIv1/schedule/group/{group}")
                if not isinstance(value, dict) or not isinstance(value.get("lessons"), list) or not isinstance(value.get("curricula"), list):
                    raise ScheduleError("Не удалось прочитать расписание группы.")
                return value
            raw, parity = await asyncio.gather(
                self.cached(("mmcs:schedule", group), 300, load_group),
                self.cached("mmcs:week", 60, load_week_type),
            )
            week_type = (parity.value["week"] + (start - parity.value["monday"]).days // 7) % 2
            events = mmcs_events(raw.value, start, "upper" if week_type == 0 else "lower")
            # A cached parity from an older week is safe: its reference Monday is retained.
            return pack(preference, start, events, min(raw.updated, parity.updated), raw.stale or parity.stale,
                        "Верхняя неделя" if week_type == 0 else "Нижняя неделя")
        async def load_ictis():
            first = datetime.combine(start, datetime.min.time(), MOSCOW)
            last = first + timedelta(days=7) - timedelta(microseconds=1)
            events = []
            for page in range(5):
                value = await self.request("https://schedule.rdcenter.ru/api/ictis", body={
                    "size": 1000, "page": page, "timeMin": first.isoformat(), "timeMax": last.isoformat(), "attendeePersonId": [group],
                })
                if not isinstance(value, dict) or not isinstance(value.get("_embedded"), dict):
                    raise ScheduleError("Не удалось прочитать расписание ИКТИБ.")
                events.extend(ictis_events(value, start))
                if value.get("page", {}).get("totalPages", 1) <= page + 1:
                    return events
            raise ScheduleError("В расписании слишком много занятий. Открой официальный источник.")
        result = await self.cached(("ictis:schedule", group, start.isoformat()), 300, load_ictis)
        return pack(preference, start, result.value, result.updated, result.stale)


def pack(preference, start, events, updated, stale=False, week_label="", demo=False):
    now = datetime.now(MOSCOW)
    events = sorted(events, key=lambda e: (e["start"], e["end"], e["id"]))
    source = next(p for p in PROVIDERS if p["id"] == preference["provider"])
    next_event = next((e for e in events if datetime.fromisoformat(e["end"]) > now), None)
    return {"configured": True, "preference": preference, "week_start": start.isoformat(), "today": now.date().isoformat(),
            "week_label": week_label, "events": events, "next": next_event, "updated": updated,
            "stale": stale, "source": source, "demo": demo, "timezone": "Europe/Moscow"}


def mmcs_events(raw, start, week_type):
    curricula = defaultdict(list)
    for item in raw["curricula"]:
        curricula[item["lessonid"]].append(item)
    events = []
    for lesson in raw["lessons"]:
        slot = re.fullmatch(r"\(([0-6]),(\d{2}:\d{2}:\d{2}),(\d{2}:\d{2}:\d{2}),(full|upper|lower)\)", lesson.get("timeslot", ""))
        if not slot:
            raise ScheduleError("Формат времени в расписании изменился. Открой официальный источник.")
        weekday, begin, end, parity = slot.groups()
        if parity not in ("full", week_type):
            continue
        day = start + timedelta(days=int(weekday))
        note = lesson.get("info") or ""
        if not within_note_dates(note, day):
            continue
        for item in curricula[lesson["id"]]:
            events.append({"id": f"{lesson['id']}-{item['id']}", "start": f"{day}T{begin}+03:00", "end": f"{day}T{end}+03:00",
                           "subject": item.get("subjectname") or "Занятие", "teacher": item.get("teachername") or "",
                           "room": item.get("roomname") or "", "kind": "Лекция" if lesson.get("ctype") else "Занятие",
                           "subgroup": str(item["subnum"]) if lesson.get("subcount", 1) > 1 else "", "note": note})
    return events


def within_note_dates(note, day):
    # Only explicit dates with a year are unambiguous. All other notes remain visible.
    for marker, value in re.findall(r"\b(с|до)\s+(\d{2}\.\d{2}\.\d{4})", note.casefold()):
        try:
            boundary = datetime.strptime(value, "%d.%m.%Y").date()
        except ValueError:
            continue
        if (marker == "с" and day < boundary) or (marker == "до" and day > boundary):
            return False
    return True


def link_id(item, key):
    value = item.get("_links", {}).get(key, {}).get("href", "")
    return value.rstrip("/").split("/")[-1].split("?")[0]


def ictis_events(raw, start):
    data = raw["_embedded"]
    lookup = {key: {str(row.get("id")): row for row in data.get(key, [])} for key in ("persons", "rooms", "course-unit-realizations")}
    teachers, rooms, notes = defaultdict(list), defaultdict(list), {}
    for item in data.get("event-attendees", []):
        if item.get("roleId") == "TEACH":
            name = lookup["persons"].get(link_id(item, "person"), {}).get("fullName", "")
            if name:
                teachers[link_id(item, "event")].append(name)
    for item in data.get("event-rooms", []):
        name = lookup["rooms"].get(link_id(item, "room"), {}).get("name", "")
        if name:
            rooms[link_id(item, "event")].append(name)
    for item in data.get("event-locations", []):
        notes[str(item.get("eventId") or link_id(item, "event"))] = item.get("customLocation") or ""
    events = []
    if not isinstance(data.get("events"), list):
        raise ScheduleError("Сервис ИКТИБ не вернул список занятий.")
    for item in data["events"]:
        try:
            begin = datetime.fromisoformat(item["start"])
            end = datetime.fromisoformat(item["end"])
            if begin.tzinfo is None or end.tzinfo is None:
                raise ValueError()
            begin, end = begin.astimezone(MOSCOW), end.astimezone(MOSCOW)
            if not start <= begin.date() < start + timedelta(days=7):
                continue
            if (item.get("holdingStatus") or {}).get("id") in ("CANCELLED", "CANCELED"):
                continue
            eid = str(item["id"])
        except (KeyError, TypeError, ValueError):
            raise ScheduleError("В расписании ИКТИБ изменился формат занятия.") from None
        subject = lookup["course-unit-realizations"].get(link_id(item, "course-unit-realization"), {}).get("name") or item.get("name") or "Занятие"
        events.append({"id": eid, "start": begin.isoformat(), "end": end.isoformat(), "subject": subject,
                       "teacher": ", ".join(dict.fromkeys(teachers[eid])), "room": ", ".join(dict.fromkeys(rooms[eid])),
                       "kind": {"LECT": "Лекция", "SEMI": "Практика", "LAB": "Лабораторная", "EXAM": "Экзамен"}.get(item.get("typeId"), "Занятие"),
                       "subgroup": "", "note": notes.get(eid, "")})
    return events


def demo_events(start, group):
    rows = []
    for day, title, begin, end, room in [(0, "Математический анализ", "09:50", "11:25", "211"), (1, "Программирование", "11:55", "13:30", "117"),
                                       (2, "Алгебра и геометрия", "08:00", "09:35", "310"), (3, "Дискретная математика", "13:45", "15:20", "212"),
                                       (4, "Теория вероятностей", "09:50", "11:25", "120"), (4, "Программирование", "11:55", "13:30", "117"),
                                       (5, "Английский язык", "09:50", "11:25", "206")]:
        date_str = (start + timedelta(days=day)).isoformat()
        rows.append({"id": str(day) + begin + group, "subject": title, "start": f"{date_str}T{begin}:00+03:00", "end": f"{date_str}T{end}:00+03:00",
                     "teacher": "Преподаватель · демо", "room": room, "kind": "Лекция" if day % 2 == 0 else "Практика", "note": "", "subgroup": ""})
    return rows


def calendar_bytes(data):
    def escape(value):
        return str(value).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    def stamp(value):
        return datetime.fromisoformat(value).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//brsy//SFedU Schedule//RU", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for event in data["events"]:
        uid = sha256(f"{data['preference']['provider']}:{data['preference']['group_id']}:{event['id']}:{event['start']}".encode()).hexdigest()
        description = "\n".join(filter(None, [event["teacher"], event["kind"], f"Подгруппа {event['subgroup']}" if event["subgroup"] else "", event["note"], data["source"]["url"]]))
        lines.extend(["BEGIN:VEVENT", f"UID:{uid}@brsy", f"DTSTAMP:{now}", f"DTSTART:{stamp(event['start'])}", f"DTEND:{stamp(event['end'])}",
                      "SUMMARY:" + escape(event["subject"]), "LOCATION:" + escape(event["room"]), "DESCRIPTION:" + escape(description), "END:VEVENT"])
    lines.append("END:VCALENDAR")
    folded = []
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode()) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return ("\r\n".join(folded) + "\r\n").encode()
