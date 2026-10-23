import httpx
import pytest

from brsy.grade import GradeClient, GradeError, InvalidToken


async def test_real_contract_routes_params_and_numeric_fields():
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.params['token'] == 'private-token'
        if request.url.path.endswith('semester_list'):
            data = {'20': {'ID': '20', 'Year': '2026', 'Num': '1'}, '19': {'ID': 19, 'Year': 2025, 'Num': 2}}
        elif request.url.path.endswith('discipline/subject'):
            assert request.url.params['id'] == '7'
            data = {'Discipline': {'ID': 7, 'SubjectName': 'Тест', 'Rate': '42', 'MaxCurrentRate': '60'}, 'Teachers': [],
                    'DisciplineMap': {'Modules': {'m1': {'Title': 'Модуль', 'Submodules': [1]}}},
                    'Submodules': {'1': {'Title': 'Работа', 'Rate': 0, 'MaxRate': 10}, '2': {'Title': 'Экзамен', 'Rate': None, 'MaxRate': 40}}}
        elif request.url.path.endswith('discipline/journal'):
            data = {'Journal': [{'LessonDate': '2026-10-01', 'Attended': True}], 'GymAttendanceInfo': None}
        else:
            assert request.url.params['SemesterID'] == '20'
            data = {'Disciplines': [{'ID': 7, 'SubjectName': 'Тест', 'Rate': '0', 'MaxCurrentRate': '60'}],
                    'Teachers': {'7': [{'Name': 'Преподаватель'}]}}
        return httpx.Response(200, json={'response': data})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        grade = GradeClient(client)
        assert (await grade.semesters('private-token'))[0]['title'] == 'Осень 2026'
        assert (await grade.semesters('private-token'))[1]['title'] == 'Весна 2026'
        subjects = await grade.subjects('private-token', '20')
        assert subjects[0]['score'] == 0 and subjects[0]['teacher'] == 'Преподаватель'
        detail = await grade.detail('private-token', '7')
        assert detail['modules'][0]['works'][0]['score'] == 0
        assert detail['modules'][1]['works'][0]['score'] is None
        assert (await grade.journal('private-token', '7'))['lessons'][0]['Attended'] is True
    assert all(r.url.scheme == 'https' and r.url.host == 'grade.sfedu.ru' for r in seen)


@pytest.mark.parametrize('status,payload,error', [(403, {'code':'403','message':'Token is broken.'}, InvalidToken), (200, {'code':'403'}, InvalidToken), (500, {}, GradeError), (200, {'response':None}, GradeError), (200, {'response':{}}, GradeError)])
async def test_upstream_failures_do_not_become_fake_empty_subjects(status, payload, error):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload))) as client:
        with pytest.raises(error):
            await GradeClient(client).subjects('secret')


async def test_html_response_and_timeout_do_not_expose_token():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text='<html>login</html>'))) as client:
        with pytest.raises(GradeError) as exc:
            await GradeClient(client).subjects('supersecret')
        assert 'supersecret' not in str(exc.value)
    def fail(request):
        raise httpx.ReadTimeout(str(request.url), request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(GradeError) as exc:
            await GradeClient(client).subjects('supersecret')
        assert 'supersecret' not in str(exc.value)
