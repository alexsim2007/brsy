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
