import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet

from brsy.app import create_app
from brsy.config import Config
from brsy.schedule import MOSCOW, ScheduleClient, ScheduleError, calendar_bytes, ictis_events, mmcs_events, monday
from brsy.service import Service
from brsy.store import Store
from test_app import auth
from test_security import BOT_TOKEN
from test_store_service import FakeGrade


def raw_mmcs():
    return {'lessons': [
        {'id': 10, 'timeslot': '(0,08:00:00,09:35:00,upper)', 'subcount': 2, 'ctype': False, 'info': ''},
        {'id': 11, 'timeslot': '(0,09:50:00,11:25:00,lower)', 'subcount': 1, 'ctype': True, 'info': ''},
        {'id': 12, 'timeslot': '(1,08:00:00,09:35:00,full)', 'subcount': 1, 'ctype': True, 'info': 'с 01.01.2099'},
    ], 'curricula': [
        {'id': 1, 'lessonid': 10, 'subnum': 1, 'subjectname': 'Программирование', 'roomname': '117', 'teachername': 'Первый'},
        {'id': 2, 'lessonid': 10, 'subnum': 2, 'subjectname': 'Программирование', 'roomname': '102', 'teachername': 'Второй'},
        {'id': 3, 'lessonid': 11, 'subnum': 1, 'subjectname': 'Анализ', 'roomname': '211'},
        {'id': 4, 'lessonid': 12, 'subnum': 1, 'subjectname': 'Будущий предмет'},
    ]}


def test_mmcs_parity_subgroups_and_explicit_start_date():
    start = monday(datetime.now(MOSCOW).date())
    upper = mmcs_events(raw_mmcs(), start, 'upper')
    lower = mmcs_events(raw_mmcs(), start, 'lower')
    assert [(e['room'], e['subgroup']) for e in upper] == [('117', '1'), ('102', '2')]
    assert [e['subject'] for e in lower] == ['Анализ']
    assert all(e['start'].endswith('+03:00') for e in upper + lower)


def test_ictis_relations_timezone_and_ics_escaping():
    start = monday(datetime.now(MOSCOW).date())
    raw = {'_embedded': {
        'events': [{'id': 'e1', 'start': f'{start}T06:50:00Z', 'end': f'{start}T08:25:00Z',
                    '_links': {'course-unit-realization': {'href': '/subjects/s1'}}, 'typeId': 'LECT'}],
        'course-unit-realizations': [{'id': 's1', 'name': 'Анализ, математика; ' + 'Очень длинное название ' * 8}],
        'persons': [{'id': 'p1', 'fullName': 'Преподаватель'}],
        'event-attendees': [{'roleId': 'TEACH', '_links': {'event': {'href': '/events/e1'}, 'person': {'href': '/persons/p1'}}}],
        'rooms': [{'id': 'r1', 'name': 'Д-411'}],
        'event-rooms': [{'_links': {'event': {'href': '/events/e1'}, 'room': {'href': '/rooms/r1'}}}],
        'event-locations': [{'eventId': 'e1', 'customLocation': 'Уточнение\nВторая строка'}],
    }}
    events = ictis_events(raw, start)
    assert events[0]['start'] == f'{start}T09:50:00+03:00'
    assert events[0]['room'] == 'Д-411' and events[0]['teacher'] == 'Преподаватель'
    payload = calendar_bytes({'events': events, 'preference': {'provider': 'ictis', 'group_id': 'group'}, 'source': {'url': 'https://ictis.sfedu.ru/schedule/'}})
    assert payload.startswith(b'BEGIN:VCALENDAR\r\n') and payload.endswith(b'END:VCALENDAR\r\n')
    assert b'\\,' in payload and b'\\;' in payload and b'\\n' in payload
    assert all(len(line) <= 75 for line in payload.split(b'\r\n'))
    assert b'T065000Z' in payload


async def test_schedule_api_all_groups_independent_from_brs_and_owner_isolation(tmp_path):
    def upstream(request):
        if request.url.path.endswith('/grade/list'):
            return httpx.Response(200, json=[{'id': 1, 'num': 1, 'degree': 'bachelor'}, {'id': 6, 'num': 1, 'degree': 'master'}])
        if '/group/forGrade/' in request.url.path:
            return httpx.Response(200, json=[{'id': 21, 'name': 'Математика', 'num': 2}, {'id': 44, 'name': 'ФИИТ', 'num': 4}])
        if request.url.path.endswith('/week'):
            return httpx.Response(200, json={'week': 0})
        if '/schedule/group/' in request.url.path:
            return httpx.Response(200, json=raw_mmcs())
        raise AssertionError(request.url)

    store = Store(str(tmp_path / 'schedule.sqlite3'), Fernet.generate_key().decode())
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as source:
        schedule = ScheduleClient(source)
        app = create_app(Config(bot_token=BOT_TOKEN), Service(store, FakeGrade()), schedule)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            assert (await client.get('/api/schedule')).status_code == 401
            assert (await client.get('/api/schedule/catalog')).status_code == 401
            assert (await client.get('/api/schedule', headers=auth(1))).json()['configured'] is False
            catalogue = (await client.get('/api/schedule/catalog?course=6', headers=auth(1))).json()
            assert [g['id'] for g in catalogue['groups']] == ['21', '44']
            for uid, group in [(1, '21'), (2, '44')]:
                response = await client.patch('/api/schedule/preference', json={'provider': 'mmcs', 'group_id': group, 'course': 6}, headers=auth(uid))
                assert response.status_code == 200
                assert store.user(uid) is None  # timetable works without a BRS token
            for uid, group in [(1, '21'), (2, '44')]:
                data = (await client.get('/api/schedule', headers=auth(uid))).json()
                assert data['preference']['group_id'] == group and data['week_label'] == 'Верхняя неделя'
            start = monday(datetime.now(MOSCOW).date())
            next_week = (await client.get('/api/schedule?week=' + (start + timedelta(days=7)).isoformat(), headers=auth(1))).json()
            assert next_week['week_label'] == 'Нижняя неделя'
            assert next_week['events'][0]['subject'] == 'Анализ'
            assert (await client.get('/api/schedule?week=2000-01-01', headers=auth(1))).status_code == 422
            bad = await client.patch('/api/schedule/preference', json={'provider': 'mmcs', 'group_id': '9999', 'course': 6}, headers=auth(1))
            assert bad.status_code == 422 and store.schedule_preference(1)['group_id'] == '21'
            response = await client.get('/api/schedule/calendar', headers=auth(1))
            assert response.status_code == 200 and response.headers['content-type'].startswith('text/calendar')
            assert response.headers['cache-control'] == 'no-store'
            assert (await client.delete('/api/account', headers=auth(1))).status_code == 200
            assert store.schedule_preference(1) is None and store.schedule_preference(2)['group_id'] == '44'
    store.close()


async def test_shared_requests_and_stale_cache_are_bounded_and_explicit():
    schedule = ScheduleClient(None)
    calls = 0
    async def load():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return ['saved']
    results = await asyncio.gather(*(schedule.cached('group', 300, load) for _ in range(8)))
    assert calls == 1 and all(r.value == ['saved'] for r in results)
    schedule.cache['group'].checked -= 301
    async def fail():
        raise ScheduleError('offline')
    stale = await schedule.cached('group', 300, fail)
    assert stale.stale and stale.updated == results[0].updated and stale.value == ['saved']
    schedule.cache['group'].checked -= 31
    schedule.cache['group'].updated -= 86401
    with pytest.raises(ScheduleError):
        await schedule.cached('group', 300, fail)


async def test_saved_group_survives_restart_without_linking_brs(tmp_path):
    path, key = str(tmp_path/'db'), Fernet.generate_key().decode()
    store = Store(path, key)
    choice = {'provider': 'mmcs', 'group_id': '44', 'group_name': '4 группа', 'course': 1}
    store.save_schedule_preference(123, choice)
    store.close()
    store = Store(path, key)
    assert store.schedule_preference(123) == choice
    store.close()
