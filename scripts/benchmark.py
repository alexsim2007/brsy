"""Repeatable local benchmark; uses synthetic data and never contacts BRS/Telegram."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet

from brsy.service import Service
from brsy.store import Store


class DelayedGrade:
    def __init__(self):
        self.calls = 0

    async def detail(self, token, sid):
        self.calls += 1
        await asyncio.sleep(0.05)
        return {"id": sid, "modules": []}

    async def journal(self, token, sid):
        self.calls += 1
        await asyncio.sleep(0.05)
        return {"lessons": [], "gym": None}


async def main():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "benchmark.sqlite3"
        store = Store(str(path), Fernet.generate_key().decode())
        try:
            store.link(1, "s" * 40, "Тест")
            subjects = [{"id": str(index), "name": f"Предмет {index}", "score": 70,
                         "available": 100, "type": "exam", "teacher": "Преподаватель",
                         "semester_id": "10", "group": "Группа", "faculty": "Факультет"}
                        for index in range(20)]
            store.save(1, "10", subjects, current=True)
            store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            start = time.perf_counter()
            for _ in range(100):
                store.save(1, "10", subjects, current=True)
            save_ms = (time.perf_counter() - start) * 1000
            wal_bytes = Path(str(path) + "-wal").stat().st_size
            grade = DelayedGrade()
            service = Service(store, grade)
            start = time.perf_counter()
            await service.detail(1, "0")
            detail_ms = (time.perf_counter() - start) * 1000
            await service.detail(1, "1")
            await service.detail(1, "0")
            print(json.dumps({"unchanged_syncs": 100, "sync_ms": round(save_ms, 1),
                              "wal_bytes": wal_bytes, "detail_ms_at_50ms_upstream": round(detail_ms, 1),
                              "upstream_calls_for_A_B_A": grade.calls}, indent=2))
        finally:
            store.close()


if __name__ == "__main__":
    asyncio.run(main())
