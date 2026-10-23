import asyncio
import time
from collections import OrderedDict
from weakref import WeakValueDictionary

from .grade import GradeClient, GradeError, InvalidToken
from .security import parse_token
from .store import Store


class NotLinked(GradeError):
    pass


class Service:
    CACHE_USERS = 256
    DETAIL_LIMIT = 8

    def __init__(self, store: Store, grade: GradeClient):
        self.store, self.grade = store, grade
        self.locks = WeakValueDictionary()
        self.network = asyncio.Semaphore(4)
        self.semesters_cache = OrderedDict()
        self.detail_cache = OrderedDict()
        self.link_attempts = OrderedDict()
        self.refresh_attempts = OrderedDict()

    def lock(self, uid):
        lock = self.locks.get(uid)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[uid] = lock
        return lock

    def require(self, uid):
        user = self.store.user(uid)
        if not user:
            raise NotLinked("Сначала подключите токен БРС: /connect")
        return user

    def remember(self, cache, uid, value):
        cache[uid] = value
        cache.move_to_end(uid)
        if len(cache) > self.CACHE_USERS:
            cache.popitem(last=False)

    async def request(self, method, *args):
        # Each upstream request takes a slot, including parallel detail/journal loads.
        async with self.network:
            return await method(*args)

    async def link(self, uid, token, name):
        token = parse_token(token)
        async with self.lock(uid), self.network:
            now = time.time()
            if now - self.link_attempts.get(uid, 0) < 15:
                raise GradeError("Подождите 15 секунд перед повторным подключением.")
            self.link_attempts[uid] = now
            self.link_attempts.move_to_end(uid)
            if len(self.link_attempts) > 4096:
                self.link_attempts.popitem(last=False)
            semesters = await self.grade.semesters(token)
            subjects = await self.grade.subjects(token)
            semester = subjects[0]["semester_id"] if subjects else semesters[0]["id"] if semesters else "current"
            self.store.link(uid, token, name)
            self.store.save(uid, semester, subjects, current=True)
            self.remember(self.semesters_cache, uid, (time.time(), semesters))
            self.detail_cache.pop(uid, None)
            self.refresh_attempts.pop(uid, None)

    async def delete(self, uid):
        async with self.lock(uid):
            self.store.delete(uid)
            self.semesters_cache.pop(uid, None)
            self.detail_cache.pop(uid, None)

    async def semesters(self, uid):
        self.require(uid)
        cached = self.semesters_cache.get(uid)
        if cached and time.time() - cached[0] < 3600:
            self.semesters_cache.move_to_end(uid)
            return cached[1]
        async with self.lock(uid):
            self.require(uid)
            return await self._semesters(uid)

    async def _semesters(self, uid):
        """Load while holding the user's lock; coalesce concurrent cache misses."""
        cached = self.semesters_cache.get(uid)
        if cached and time.time() - cached[0] < 3600:
            self.semesters_cache.move_to_end(uid)
            return cached[1]
        result = await self.request(self.grade.semesters, self.store.token(uid))
        self.remember(self.semesters_cache, uid, (time.time(), result))
        return result

    async def sync(self, uid, semester=None, *, force=False):
        async with self.lock(uid):
            user = self.require(uid)
            current = semester is None or semester == user["semester"]
            key = user["semester"] if current else semester
            cached = self.store.cached(uid, key)
            if cached and not force and time.time() - cached["updated"] < 60:
                return cached
            # Bound manual refreshes, including failures, so buttons cannot hammer BRS.
            attempted = max(self.refresh_attempts.get(uid, 0), (user["last_attempt"] or 0) if current else 0)
            if time.time() - attempted < 15:
                if cached:
                    return cached
                raise GradeError("Подождите 15 секунд перед повторной проверкой.")
            self.refresh_attempts[uid] = time.time()
            self.refresh_attempts.move_to_end(uid)
            if len(self.refresh_attempts) > 4096:
                self.refresh_attempts.popitem(last=False)
            try:
                async with self.network:
                    subjects = await self.grade.subjects(self.store.token(uid), semester)
                if current and subjects:
                    key = subjects[0]["semester_id"] or key
                elif current and semester is None:
                    available = await self._semesters(uid)
                    if available:
                        key = available[0]["id"]
                self.store.save(uid, key, subjects, current=current)
                self.detail_cache.pop(uid, None)
                return self.store.cached(uid, key)
            except GradeError as exc:
                if current:
                    self.store.failure(uid, str(exc), isinstance(exc, InvalidToken))
                if cached and not force:
                    return cached
                raise

    async def dashboard(self, uid, semester=None, *, force=False):
        self.require(uid)
        try:
            semesters = await self.semesters(uid)
        except GradeError:
            semesters = self.semesters_cache.get(uid, (0, []))[1]
        if semester and semester != self.require(uid)["semester"] and semester not in {s["id"] for s in semesters}:
            raise GradeError("Этот семестр недоступен.")
        cached = await self.sync(uid, semester, force=force)
        user = self.require(uid)
        if not semesters:
            semesters = [{"id": user["semester"], "title": "Текущий семестр"}]
        subjects = cached["subjects"]
        scores = [s["score"] for s in subjects if s["score"] is not None]
        return {"demo": False, "linked": True, "name": user["name"], "subjects": subjects,
                "semesters": semesters, "semester": cached["semester"], "updated": cached["updated"],
                "history": self.store.history(uid), "error": user["error"],
                "settings": {"notifications": bool(user["notifications"]), "target": user["target"]},
                "stats": {"average": round(sum(scores) / len(scores), 1) if scores else None,
                          "count": len(subjects), "at_target": sum(s >= user["target"] for s in scores)}}

    async def detail(self, uid, sid, semester=None):
        async with self.lock(uid):
            user = self.require(uid)
            semester = semester or user["semester"]
            cached = self.store.cached(uid, semester)
            if not cached or sid not in {s["id"] for s in cached["subjects"]}:
                raise GradeError("Предмет не найден в выбранном семестре.")
            key = (semester, sid)
            entries = self.detail_cache.get(uid)
            entry = entries.get(key) if entries else None
            if entry and time.time() - entry[0] < 60:
                entries.move_to_end(key)
                self.detail_cache.move_to_end(uid)
                return entry[1]
            token = self.store.token(uid)
            result, journal = await asyncio.gather(
                self.request(self.grade.detail, token, sid),
                self.request(self.grade.journal, token, sid),
                return_exceptions=True,
            )
            if isinstance(result, BaseException):
                raise result
            if isinstance(journal, GradeError):
                result["journal"], result["journal_error"] = None, str(journal)
            elif isinstance(journal, BaseException):
                raise journal
            else:
                result["journal"], result["journal_error"] = journal, None
            # Bound both accounts and subject cards, rather than evicting on every click.
            entries = OrderedDict((k, v) for k, v in (entries or {}).items() if time.time() - v[0] < 60)
            entries[key] = (time.time(), result)
            entries.move_to_end(key)
            while len(entries) > self.DETAIL_LIMIT:
                entries.popitem(last=False)
            self.remember(self.detail_cache, uid, entries)
            return result
