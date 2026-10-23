import importlib.util
import os
import sqlite3
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


pytest.importorskip('pwd')
spec = importlib.util.spec_from_file_location('container_startup', Path(__file__).parents[1] / 'deploy/docker-entrypoint.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def test_preparing_existing_database_keeps_data_and_repairs_file_permissions(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    database = root / 'brsy.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE example (value TEXT)')
        connection.execute("INSERT INTO example VALUES ('preserved')")
    files = [database]
    for suffix in ('-wal', '-shm', '-journal'):
        path = root / ('brsy.sqlite3' + suffix)
        path.touch()
        files.append(path)
    for path in files:
        path.chmod(0o400)
    unrelated = root / 'backup.txt'
    unrelated.write_text('untouched')
    unrelated.chmod(0o640)
    root.chmod(0o555)

    startup.prepare_storage(root, database, os.getuid(), os.getgid())

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert stat.S_IMODE(unrelated.stat().st_mode) == 0o640
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT value FROM example').fetchone()[0] == 'preserved'
        connection.execute("INSERT INTO example VALUES ('writable')")


def test_preparing_new_nested_database_directory(tmp_path):
    root = tmp_path / 'data'
    database = root / 'nested' / 'brsy.sqlite3'
    startup.prepare_storage(root, database, os.getuid(), os.getgid())
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE example (value TEXT)')
    assert stat.S_IMODE(database.parent.stat().st_mode) == 0o700


@pytest.mark.parametrize('target', ['outside.sqlite3', 'data/../outside.sqlite3'])
def test_never_changes_permissions_outside_data_mount(tmp_path, target):
    outside = tmp_path / 'outside.sqlite3'
    outside.write_text('untouched')
    outside.chmod(0o640)
    with pytest.raises(ValueError, match='внутри /app/data'):
        startup.prepare_storage(tmp_path / 'data', tmp_path / target, os.getuid(), os.getgid())
    assert stat.S_IMODE(outside.stat().st_mode) == 0o640


@pytest.mark.parametrize('symlink', ['directory', 'database'])
def test_never_follows_symlinks_when_changing_ownership(tmp_path, symlink):
    root = tmp_path / 'data'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    if symlink == 'directory':
        (root / 'nested').symlink_to(outside, target_is_directory=True)
        database = root / 'nested' / 'brsy.sqlite3'
    else:
        database = root / 'brsy.sqlite3'
        database.symlink_to(outside / 'missing.sqlite3')
    with pytest.raises(ValueError, match='символической ссылкой'):
        startup.prepare_storage(root, database, os.getuid(), os.getgid())
    assert list(outside.iterdir()) == []


def test_root_bootstrap_drops_privileges_before_executing_application(monkeypatch):
    calls = []
    monkeypatch.setattr(startup.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(startup.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=10001, pw_gid=10001))
    monkeypatch.setenv('DATABASE_PATH', '/app/data/brsy.sqlite3')
    monkeypatch.setattr(startup, 'prepare_storage', lambda *args: calls.append(('prepare', args)))
    monkeypatch.setattr(startup.os, 'umask', lambda mask: None)
    monkeypatch.setattr(startup.os, 'setgroups', lambda groups: calls.append(('groups', groups)))
    monkeypatch.setattr(startup.os, 'setgid', lambda gid: calls.append(('gid', gid)))
    monkeypatch.setattr(startup.os, 'setuid', lambda uid: calls.append(('uid', uid)))
    monkeypatch.setattr(startup.os, 'execvp', lambda executable, args: calls.append(('exec', executable, args)))
    monkeypatch.setattr(sys, 'argv', ['entrypoint', 'python', '-m', 'brsy'])
    startup.main()
    assert [call[0] for call in calls] == ['prepare', 'groups', 'gid', 'uid', 'exec']
    assert calls[-1] == ('exec', 'python', ['python', '-m', 'brsy'])
    assert calls[3] == ('uid', 10001)


def test_nonroot_compose_start_does_not_need_ownership_capabilities(monkeypatch):
    calls = []
    monkeypatch.setattr(startup.os, 'geteuid', lambda: 10001)
    monkeypatch.setattr(startup.os, 'umask', lambda mask: None)
    monkeypatch.setattr(startup.os, 'execvp', lambda *args: calls.append(args))
    monkeypatch.setattr(sys, 'argv', ['entrypoint', 'python', '-m', 'brsy'])
    startup.main()
    assert calls == [('python', ['python', '-m', 'brsy'])]
