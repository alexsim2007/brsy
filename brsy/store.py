import json
import os
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken as DecryptionError


class Store:
    """Single-process SQLite store. All calls run on the application event loop."""
    def __init__(self, path: str, key: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.cipher = Fernet(key.encode())
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, token BLOB NOT NULL, name TEXT NOT NULL,
                notifications INTEGER NOT NULL DEFAULT 1, target INTEGER NOT NULL DEFAULT 80,
                semester TEXT, last_sync REAL, last_attempt REAL, error TEXT, invalid INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cache (
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                semester TEXT NOT NULL, payload TEXT NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(user_id, semester)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                semester TEXT NOT NULL, subject_id TEXT NOT NULL, subject TEXT NOT NULL,
                old REAL, new REAL, created REAL NOT NULL, notified INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS events_owner ON events(user_id, id DESC);
        """)
        if path != ":memory:":
            os.chmod(path, 0o600)
        existing = self.db.execute("SELECT token FROM users LIMIT 1").fetchone()
        if existing:
            try:
                self.cipher.decrypt(existing[0])
            except DecryptionError:
                self.db.close()
                raise ValueError("ENCRYPTION_KEY не подходит к существующей базе. Восстановите исходный ключ.") from None

    def close(self):
        self.db.close()

    def user(self, uid):
        row = self.db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

    def token(self, uid):
        user = self.user(uid)
        if not user:
            return None
        return self.cipher.decrypt(user["token"]).decode()

    def link(self, uid, token, name):
        with self.db:
            # A different account must never inherit the previous account's grades/history.
            self.db.execute("DELETE FROM users WHERE id=?", (uid,))
            self.db.execute("INSERT INTO users(id, token, name) VALUES(?,?,?)",
                            (uid, self.cipher.encrypt(token.encode()), name[:128]))

    def delete(self, uid):
        with self.db:
            self.db.execute("DELETE FROM users WHERE id=?", (uid,))

    def cached(self, uid, semester):
        row = self.db.execute("SELECT payload,updated FROM cache WHERE user_id=? AND semester=?", (uid, semester)).fetchone()
        return {"subjects": json.loads(row[0]), "updated": row[1], "semester": semester} if row else None

    def save(self, uid, semester, subjects, *, current=False):
        previous = self.cached(uid, semester)
        user = self.user(uid)
        if not user:
            return
        now = time.time()
        with self.db:
            if previous:
                old = {s["id"]: s for s in previous["subjects"]}
                for subject in subjects:
                    before = old.get(subject["id"])
                    if before and before["score"] != subject["score"]:
                        self.db.execute("""INSERT INTO events(user_id,semester,subject_id,subject,old,new,created,notified)
                                         VALUES(?,?,?,?,?,?,?,?)""",
                                        (uid, semester, subject["id"], subject["name"], before["score"], subject["score"], now,
                                         0 if current and user["notifications"] else 1))
            self.db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?)", (uid, semester, json.dumps(subjects, ensure_ascii=False), now))
            if current:
                self.db.execute("UPDATE users SET semester=?,last_sync=?,last_attempt=?,error=NULL,invalid=0 WHERE id=?",
                                (semester, now, now, uid))
            # Keep bounded history on the Pi, including delivered outbox entries.
            self.db.execute("DELETE FROM events WHERE user_id=? AND notified=1 AND id NOT IN (SELECT id FROM events WHERE user_id=? ORDER BY id DESC LIMIT 1000)", (uid, uid))

    def failure(self, uid, message, invalid=False):
        with self.db:
            self.db.execute("UPDATE users SET error=?,invalid=?,last_attempt=? WHERE id=?", (message, int(invalid), time.time(), uid))

    def settings(self, uid, *, notifications=None, target=None):
        with self.db:
            if notifications is not None:
                self.db.execute("UPDATE users SET notifications=? WHERE id=?", (int(notifications), uid))
                if not notifications:
                    self.db.execute("UPDATE events SET notified=1 WHERE user_id=?", (uid,))
            if target is not None:
                self.db.execute("UPDATE users SET target=? WHERE id=?", (target, uid))

    def history(self, uid, limit=50):
        return [dict(row) for row in self.db.execute("SELECT id,semester,subject_id,subject,old,new,created FROM events WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit))]

    def pending(self, uid):
        return [dict(row) for row in self.db.execute("SELECT * FROM events WHERE user_id=? AND notified=0 ORDER BY id LIMIT 15", (uid,))]

    def delivered(self, uid, ids):
        with self.db:
            self.db.executemany("UPDATE events SET notified=1 WHERE user_id=? AND id=?", [(uid, eid) for eid in ids])

    def due(self, interval):
        return [row[0] for row in self.db.execute("SELECT id FROM users WHERE invalid=0 AND (last_attempt IS NULL OR last_attempt<?) ORDER BY last_attempt LIMIT 100", (time.time() - interval,))]

    def notification_users(self):
        return [row[0] for row in self.db.execute("SELECT DISTINCT e.user_id FROM events e JOIN users u ON u.id=e.user_id WHERE e.notified=0 AND u.notifications=1 LIMIT 100")]
