"""Read-only adapter for https://grade.sfedu.ru/api/v1/student/.

Endpoint and field contracts were researched in MihaxXx/grade_app/GradeAPI/API.cs.
No source from that project is bundled. Live authenticated validation is still needed.
"""
import math
from typing import Any

import httpx


class GradeError(Exception):
    pass


class InvalidToken(GradeError):
    pass


def number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(str(value).replace(",", "."))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def values(value):
    return list(value.values()) if isinstance(value, dict) else value if isinstance(value, list) else []


def normalize_subject(item: dict, teachers=None) -> dict:
    name = " ".join(str(item.get(k) or "") for k in ("LastName", "FirstName", "SecondName")).strip()
    people = values(teachers)
    names = [p.get("Name") or " ".join(str(p.get(k) or "") for k in ("LastName", "FirstName", "SecondName")).strip()
             for p in people if isinstance(p, dict)]
    return {"id": str(item["ID"]), "name": str(item.get("SubjectName") or "Без названия"),
            "score": number(item.get("Rate")), "available": number(item.get("MaxCurrentRate")),
            "type": str(item.get("Type") or ""), "teacher": ", ".join(filter(None, names)) or name,
            "semester_id": str(item.get("SemesterID") or ""),
            "group": str(item.get("GroupName") or ""), "faculty": str(item.get("FacultyName") or "")}


class GradeClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def request(self, token: str, path: str = "", **params):
        try:
            response = await self.client.get("https://grade.sfedu.ru/api/v1/student/" + path,
                                             params={"token": token, **params}, follow_redirects=False)
        except httpx.HTTPError:
            raise GradeError("БРС не отвечает. Попробуйте позже; сохранённые баллы доступны.") from None
        if response.status_code in (401, 403):
            raise InvalidToken("БРС отклонила токен. Получите новый: /instruction")
        if response.status_code != 200:
            raise GradeError("БРС временно недоступна. Попробуйте позже.")
        try:
            body = response.json()
        except ValueError:
            raise GradeError("БРС вернула неожиданный ответ. Данные не изменены.") from None
        if not isinstance(body, dict):
            raise GradeError("Неизвестный формат ответа БРС.")
        if str(body.get("code", "200")) in ("401", "403"):
            raise InvalidToken("Токен БРС больше не действует. Подключите новый.")
        if "response" not in body or body.get("error"):
            raise GradeError("Неизвестный формат ответа БРС. Данные не изменены.")
        return body["response"]

    async def semesters(self, token):
        payload = await self.request(token, "semester_list")
        if not isinstance(payload, (dict, list)):
            raise GradeError("Изменился формат семестров БРС.")
        result = []
        for item in values(payload):
            if not isinstance(item, dict) or "ID" not in item:
                raise GradeError("Изменился формат семестров БРС.")
            try:
                year, num = int(item["Year"]), int(item["Num"])
            except (KeyError, ValueError, TypeError):
                raise GradeError("Изменился формат семестров БРС.") from None
            result.append({"id": str(item["ID"]), "title": f"{'Осень' if num == 1 else 'Весна'} {year if num == 1 else year + 1}",
                           "year": year, "num": num})
        return sorted(result, key=lambda s: (s["year"], s["num"]), reverse=True)

    async def subjects(self, token, semester=None):
        payload = await self.request(token, **({"SemesterID": semester} if semester else {}))
        if not isinstance(payload, dict) or not isinstance(payload.get("Disciplines"), (dict, list)):
            raise GradeError("Изменился формат списка предметов БРС.")
        teachers = payload.get("Teachers") or {}
        try:
            return [normalize_subject(d, teachers.get(str(d["ID"]))) for d in values(payload["Disciplines"])]
        except (KeyError, TypeError, AttributeError):
            raise GradeError("Изменился формат предметов БРС.") from None

    async def detail(self, token, subject_id):
        payload = await self.request(token, "discipline/subject", id=subject_id)
        if not isinstance(payload, dict) or not isinstance(payload.get("Discipline"), dict):
            raise GradeError("Изменился формат карточки предмета БРС.")
        result = normalize_subject(payload["Discipline"], payload.get("Teachers"))
        modules, submodules = [], payload.get("Submodules") or {}
        if not isinstance(submodules, dict):
            raise GradeError("Изменился формат работ БРС.")
        mapping = payload.get("DisciplineMap") or {}
        used = set()
        for module in values(mapping.get("Modules")):
            works = []
            for sid in module.get("Submodules") or []:
                sid = str(sid)
                if sid in submodules:
                    used.add(sid)
                    works.append(self.work(sid, submodules[sid]))
            modules.append({"title": module.get("Title") or "Модуль", "works": works})
        other = [self.work(str(sid), data) for sid, data in submodules.items() if str(sid) not in used]
        if other:
            modules.append({"title": "Экзамен и дополнительные баллы", "works": other})
        result.update(modules=modules, extra=number(payload.get("ExtraRate")))
        return result

    @staticmethod
    def work(sid, item):
        return {"id": sid, "title": item.get("Title") or "Работа", "score": number(item.get("Rate")),
                "max": number(item.get("MaxRate")), "date": item.get("Date")}

    async def journal(self, token, subject_id):
        payload = await self.request(token, "discipline/journal", id=subject_id)
        if not isinstance(payload, dict) or "Journal" not in payload:
            raise GradeError("Изменился формат посещаемости БРС.")
        return {"lessons": values(payload["Journal"]), "gym": payload.get("GymAttendanceInfo")}
