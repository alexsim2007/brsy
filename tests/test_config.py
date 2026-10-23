from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from brsy.config import Config


@pytest.fixture
def environment(monkeypatch):
    # Never read the developer's local credentials in configuration tests.
    monkeypatch.setattr('brsy.config.load_dotenv', lambda: None)
    for name in ('DATABASE_PATH', 'DATA_DIR', 'HOST', 'PORT', 'MINI_APP_URL', 'POLL_INTERVAL', 'DEMO_MODE'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('BOT_TOKEN', '123456789:' + 'x' * 35)
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    return monkeypatch


@pytest.mark.parametrize('directory, explicit_path, expected', [
    (None, None, 'data/brsy.sqlite3'),
    ('/app/data', None, '/app/data/brsy.sqlite3'),
    ('/app/data', '/backups/custom.sqlite3', '/backups/custom.sqlite3'),
    ('/app/data', '', '/app/data/brsy.sqlite3'),
])
def test_persistent_data_directory_and_explicit_path_priority(environment, directory, explicit_path, expected):
    if directory is not None:
        environment.setenv('DATA_DIR', directory)
    if explicit_path is not None:
        environment.setenv('DATABASE_PATH', explicit_path)
    assert Path(Config.load().database_path) == Path(expected)


def test_hosting_uses_the_configured_interface_and_port(environment):
    environment.setenv('HOST', '0.0.0.0')
    environment.setenv('PORT', '8080')
    environment.setenv('MINI_APP_URL', 'https://example.bothost.tech/')
    config = Config.load()
    assert (config.host, config.port) == ('0.0.0.0', 8080)
    assert config.mini_app_url == 'https://example.bothost.tech'


@pytest.mark.parametrize('address', ['http://example.bothost.tech', 'https://example.bothost.tech?token=secret'])
def test_hosting_url_requires_https_without_query_secrets(environment, address):
    environment.setenv('MINI_APP_URL', address)
    with pytest.raises(ValueError, match='HTTPS URL'):
        Config.load()
