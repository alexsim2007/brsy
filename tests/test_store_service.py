import asyncio
import copy
import time

import pytest
from cryptography.fernet import Fernet

from brsy.grade import GradeError, InvalidToken
from brsy.service import NotLinked, Service
from brsy.store import Store


def subject(score=70, sid='1', sem='10'):
    return {'id': sid, 'name': 'Анализ', 'score': score, 'available': 100, 'type': 'exam', 'teacher': 'Тест', 'semester_id': sem, 'group': '', 'faculty': ''}


@pytest.fixture
def store(tmp_path):
    db = Store(str(tmp_path / 'db.sqlite3'), Fernet.generate_key().decode())
    yield db
    db.close()


class FakeGrade:
    def __init__(self):
        self.items = [subject()]
        self.calls = 0
        self.error = None

    async def semesters(self, token):
        return [{'id': '10', 'title': 'Осень 2026'}, {'id': '9', 'title': 'Весна 2026'}]

    async def subjects(self, token, semester=None):
        self.calls += 1
        if self.error:
            raise self.error
        result = copy.deepcopy(self.items)
        for s in result:
            s['semester_id'] = semester or '10'
        return result

    async def detail(self, token, sid):
        return {'id': sid, 'modules': []}

    async def journal(self, token, sid):
        return {'lessons': [], 'gym': None}


def age_cache(store, uid=1):
    with store.db:
        store.db.execute('UPDATE users SET last_attempt=? WHERE id=?', (time.time()-1000, uid))
        store.db.execute('UPDATE cache SET updated=? WHERE user_id=?', (time.time()-1000, uid))


def test_encryption_initial_baseline_diff_and_user_isolation(store, tmp_path):
    token = 's' * 40
    store.link(1, token, 'Имя')
    store.link(2, 'other' * 8, 'Другое')
    assert store.token(1) == token
    assert token.encode() not in store.user(1)['token']
    store.save(1, '10', [subject()], current=True)
    assert store.history(1) == []
    store.save(1, '10', [subject(76)], current=True)
    store.save(1, '10', [subject(76)], current=True)
    assert len(store.pending(1)) == 1
    assert store.history(1)[0]['old'] == 70
    assert store.history(2) == []
    assert store.cached(2, '10') is None
    # An event remains pending across store close/reopen (outbox durability).
    key = store.cipher
    assert store.db.execute('SELECT COUNT(*) FROM events WHERE notified=0').fetchone()[0] == 1
    store.delivered(1, [store.pending(1)[0]['id']])
    assert store.pending(1) == []
    store.save(1, '10', [subject(None)], current=True)
    assert store.history(1)[0]['new'] is None


def test_muting_and_historical_changes_do_not_send_notifications(store):
    store.link(1, 's'*40, 'Имя')
    store.save(1, '10', [subject()], current=True)
    store.settings(1, notifications=False)
    store.save(1, '10', [subject(80)], current=True)
    assert len(store.history(1)) == 1 and store.pending(1) == []
    store.settings(1, notifications=True)
    store.save(1, '9', [subject(60)], current=False)
    store.save(1, '9', [subject(75)], current=False)
    assert store.pending(1) == []


def test_delete_and_relink_remove_all_previous_data(store):
    store.link(1, 's'*40, 'Имя')
    store.save(1, '10', [subject()], current=True)
    store.save(1, '10', [subject(80)], current=True)
    store.link(1, 'n'*40, 'Новое имя')
    assert store.history(1) == [] and store.cached(1, '10') is None
    store.delete(1)
    assert store.user(1) is None and store.token(1) is None
    assert store.db.execute('SELECT COUNT(*) FROM cache').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


async def test_service_cache_refresh_stats_and_failed_refresh_preserve_baseline(store):
    grade = FakeGrade()
    grade.items = [subject(70), subject(None, sid='2')]
    service = Service(store, grade)
    await service.link(1, 's'*40, 'Имя')
    data = await service.dashboard(1)
    assert data['stats']['average'] == 70  # ungraded is not zero
    assert grade.calls == 1
    age_cache(store)
    grade.error = GradeError('Недоступно')
    data = await service.dashboard(1)
    assert data['subjects'][0]['score'] == 70
    assert data['error'] == 'Недоступно'
    assert store.history(1) == []
    age_cache(store)
    service.refresh_attempts[1] = time.time() - 1000
    with pytest.raises(GradeError):
        await service.sync(1, force=True)
    grade.error = None
    grade.items[0]['score'] = 75
    age_cache(store)
    service.refresh_attempts[1] = time.time() - 1000
    await service.sync(1, force=True)
    assert len(store.pending(1)) == 1
    assert store.history(1)[0]['old'] == 70


async def test_invalid_token_stops_background_polling_and_notifies_ui(store):
    grade = FakeGrade()
    service = Service(store, grade)
    await service.link(1, 's'*40, 'Имя')
    grade.error = InvalidToken('Нужен новый токен')
    age_cache(store)
    data = await service.dashboard(1)
    assert data['error'] == 'Нужен новый токен'
    assert store.due(60) == []


async def test_concurrent_refresh_only_fetches_once(store):
    grade = FakeGrade()
    service = Service(store, grade)
    await service.link(1, 's'*40, 'Имя')
    age_cache(store)
    grade.items = [subject(90)]
    await asyncio.gather(service.sync(1, force=True), service.sync(1, force=True))
    assert grade.calls == 2  # baseline + one refresh
    assert len(store.pending(1)) == 1


async def test_detail_does_not_fetch_another_users_subject(store):
    grade = FakeGrade()
    service = Service(store, grade)
    await service.link(1, 's'*40, 'Имя')
    with pytest.raises(NotLinked):
        await service.detail(2, '1')
    with pytest.raises(GradeError):
        await service.detail(1, '999')
    await service.delete(1)
    with pytest.raises(NotLinked):
        await service.sync(1)


def test_outbox_survives_restart(tmp_path):
    path=str(tmp_path/'durable.sqlite3')
    key=Fernet.generate_key().decode()
    db=Store(path,key)
    db.link(1,'s'*40,'Имя')
    db.save(1,'10',[subject(10)],current=True)
    db.save(1,'10',[subject(20)],current=True)
    event_id=db.pending(1)[0]['id']
    db.close()
    db=Store(path,key)
    assert db.pending(1)[0]['id']==event_id
    assert db.token(1)=='s'*40
    db.delivered(1,[event_id])
    db.close()
    db=Store(path,key)
    assert db.pending(1)==[]
    db.close()


def test_wrong_encryption_key_fails_before_serving_accounts(tmp_path):
    path=str(tmp_path/'encrypted-db')
    db=Store(path,Fernet.generate_key().decode())
    db.link(1,'s'*40,'Тест')
    db.close()
    with pytest.raises(ValueError,match='исходный ключ'):
        Store(path,Fernet.generate_key().decode())


async def test_empty_new_semester_does_not_overwrite_archived_grades(store):
    grade=FakeGrade()
    service=Service(store,grade)
    await service.link(1,'s'*40,'Тест')
    age_cache(store)
    service.semesters_cache[1]=(time.time(),[{'id':'11','title':'Весна 2027'},{'id':'10','title':'Осень 2026'}])
    grade.items=[]
    result=await service.sync(1,force=True)
    assert result['semester']=='11'
    assert store.cached(1,'10')['subjects'][0]['score']==70
    assert store.cached(1,'11')['subjects']==[]
