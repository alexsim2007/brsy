import re
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from brsy.app import create_app
from brsy.bot import csv_bytes
from brsy.config import Config
from brsy.service import Service
from brsy.store import Store
from test_security import BOT_TOKEN, signed
from test_store_service import FakeGrade


@pytest.fixture
def service(tmp_path):
    store = Store(str(tmp_path/'db'), Fernet.generate_key().decode())
    service = Service(store, FakeGrade())
    yield service
    store.close()


def auth(uid=1):
    return {'Authorization':'tma '+signed(uid, now=int(time.time()))}


async def test_demo_is_explicit_and_cannot_accept_real_credentials():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(Config(demo=True))), base_url='http://test') as client:
        assert (await client.get('/api/dashboard')).json()['demo'] is True
        assert (await client.post('/api/connect', json={'token':'s'*40})).status_code == 403
        assert (await client.delete('/api/account')).status_code == 403
        assert (await client.get('/api/subjects/404')).status_code == 404
        assert (await client.get('/')).status_code == 200


async def test_hosting_health_paths_are_public_but_api_still_requires_identity(service):
    app = create_app(Config(bot_token=BOT_TOKEN), service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for path in ('/health', '/healthz'):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.json() == {'status': 'ok', 'mode': 'live'}
        assert (await client.get('/api/dashboard')).status_code == 401


async def test_static_cache_versions_compression_and_private_responses():
    app = create_app(Config(demo=True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        page = await client.get('/')
        assert page.headers['cache-control'] == 'no-store'
        assets = re.findall(r'/static/[\w.-]+\?v=[0-9a-f]{16}', page.text)
        assert len(set(assets)) == 4
        css_url = next(url for url in assets if 'style.css' in url)
        css = await client.get(css_url, headers={'Accept-Encoding': 'gzip'})
        assert 'immutable' in css.headers['cache-control']
        assert css.headers['content-encoding'] == 'gzip'
        assert 'accept-encoding' in css.headers['vary'].lower()
        assert int(css.headers['content-length']) < len(css.content)
        cached = await client.get(css_url, headers={'If-None-Match': css.headers['etag']})
        assert cached.status_code == 304 and cached.content == b''
        assert 'immutable' in cached.headers['cache-control']
        # Stale/unversioned addresses must revalidate rather than get stuck for a year.
        for url in ('/static/style.css', '/static/style.css?v=old'):
            assert (await client.get(url)).headers['cache-control'] == 'public, max-age=0, must-revalidate'
        for url in ('/api/dashboard', '/api/export', '/static/missing.css'):
            assert (await client.get(url)).headers['cache-control'] == 'no-store'
        for url in set(assets):
            assert (await client.get(url)).status_code == 200


async def test_production_requires_signed_identity_and_never_returns_token(service):
    app = create_app(Config(bot_token=BOT_TOKEN), service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/dashboard')).status_code == 401
        assert (await client.get('/api/dashboard', headers={'X-Telegram-User-Id':'1'})).status_code == 401
        assert (await client.get('/api/dashboard', headers=auth())).json()['linked'] is False
        secret='s'*40
        assert (await client.post('/api/connect', headers=auth(), json={'token':secret})).status_code == 200
        response=await client.get('/api/dashboard', headers=auth())
        assert response.status_code == 200 and secret not in response.text and '"token"' not in response.text
        assert response.json()['demo'] is False
        assert response.headers['cache-control'] == 'no-store'
        assert 'default-src' in response.headers['content-security-policy']
        assert (await client.get('/api/dashboard', headers=auth(2))).json()['linked'] is False
        assert (await client.get('/api/subjects/1', headers=auth(2))).status_code == 409
        assert (await client.patch('/api/settings', headers=auth(2), json={'target':50})).status_code == 409
        assert (await client.patch('/api/settings', headers=auth(), json={'target':101})).status_code == 422
        assert (await client.patch('/api/settings', headers=auth(), json={'target':50.5})).status_code == 422
        assert (await client.patch('/api/settings', headers=auth(), json={'target':50,'notifications':False})).status_code == 200
        assert (await client.get('/api/dashboard', headers=auth())).json()['settings']['target'] == 50
        exported=await client.get('/api/export', headers=auth())
        assert exported.status_code == 200 and exported.content.startswith(b'\xef\xbb\xbf')
        assert (await client.delete('/api/account', headers=auth())).status_code == 200
        assert (await client.get('/api/dashboard', headers=auth())).json()['linked'] is False


def test_csv_does_not_inject_formulas():
    content=csv_bytes([{'name':'=HYPERLINK("evil")','score':None,'available':100,'type':'exam','teacher':'@evil'}]).decode('utf-8-sig')
    assert "'=HYPERLINK" in content and "'@evil" in content
