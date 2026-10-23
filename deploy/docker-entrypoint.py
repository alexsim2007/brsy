"""Prepare a mounted data directory, then run the application without root."""
import os
import pwd
import sys
from pathlib import Path


def prepare_storage(root: Path, database: Path, uid: int, gid: int):
    # Only change the persistent data mount, never arbitrary application/system paths.
    if root.is_symlink():
        raise ValueError("Каталог данных не должен быть символической ссылкой.")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    database = database.absolute()
    if not database.is_relative_to(root) or ".." in database.relative_to(root).parts:
        raise ValueError("В контейнере DATABASE_PATH должен находиться внутри /app/data.")
    directory = root
    for part in (None, *database.parent.relative_to(root).parts):
        if part is not None:
            directory = directory / part
        if directory.is_symlink():
            raise ValueError("Каталог базы не должен быть символической ссылкой.")
        directory.mkdir(exist_ok=True)
        os.chown(directory, uid, gid)
        os.chmod(directory, 0o700)
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        if path.is_symlink():
            raise ValueError("Файл базы не должен быть символической ссылкой.")
        if path.exists():
            if not path.is_file():
                raise ValueError("DATABASE_PATH должен указывать на файл SQLite.")
            os.chown(path, uid, gid)
            os.chmod(path, 0o600)


def main():
    os.umask(0o077)
    if os.geteuid() == 0:
        user = pwd.getpwnam("brsy")
        path = os.getenv("DATABASE_PATH") or str(Path(os.getenv("DATA_DIR") or "/app/data") / "brsy.sqlite3")
        if path != ":memory:":
            prepare_storage(Path("/app/data"), Path(path), user.pw_uid, user.pw_gid)
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    if len(sys.argv) < 2:
        raise SystemExit("Не задана команда запуска контейнера.")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
