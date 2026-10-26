"""Strategy report job, API and export in app.py (badminton_analysis/web/strategy_routes.py)."""
import json
import threading

import pytest

import app as webapp
from badminton_analysis.strategy import report as strategy_report
from badminton_analysis.web.job_slots import JobSlots


class _Proc:
    """A subprocess that ends with *code* as soon as it is polled or waited on."""

    def __init__(self, code=0):
        self.returncode = code
        self.pid = 1

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


@pytest.fixture
def env(tmp_path, monkeypatch):
    outputs = tmp_path / 'outputs'
    out = outputs / 'clip'
    out.mkdir(parents=True)
    (out / 'rally_segments.json').write_text('{}', encoding='utf-8')
    videos = tmp_path / 'videos'
    videos.mkdir()
    monkeypatch.setattr(webapp, 'VIDEOS', videos)
    monkeypatch.setattr(webapp, 'OUTPUTS', outputs)
    monkeypatch.setattr(webapp, 'ATHLETES_PATH', tmp_path / 'data' / 'athletes.json')
    monkeypatch.delenv('GB_STRATEGY_REPORT', raising=False)
    trackers = []
    real_thread = threading.Thread

    def thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        trackers.append(t)
        return t

    monkeypatch.setattr(webapp.threading, 'Thread', thread)
    calls = []
    behaviour = {'code': 0, 'report': None}

    def fake_popen(cmd, *args, **kwargs):
        calls.append(cmd)
        if behaviour['report'] is not None:
            (outputs / 'clip' / strategy_report.REPORT_FILE).write_text(
                json.dumps(behaviour['report']), encoding='utf-8')
        return _Proc(behaviour['code'])

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    webapp.app.config['TESTING'] = True
    yield webapp.app.test_client(), tmp_path, outputs, calls, behaviour
    for t in trackers:
        t.join(5)
    assert not any(t.is_alive() for t in trackers)


def _wait(job_id, statuses=('completed', 'error')):
    for _ in range(300):
        job = webapp.jobs.get(job_id)
        if job and job.get('status') in statuses and job.get('ended'):
            return job
        threading.Event().wait(0.01)
    raise AssertionError(f'{job_id} never reached {statuses}: {webapp.jobs.get(job_id)}')


def _write_report(out, **extra):
    doc = {'schema': 1, 'format': 'singles', 'status': 'ok', 'reason': None, 'model': 'm',
           'endpoint': 'http://x/v1', 'generated_at': '2026-10-27T00:00:00Z',
           'duration_sec': 1.0, 'inputs': {}, 'focus': None, 'facts': [],
           'by_lang': {'en': {}, 'zh': {}}}
    doc.update(extra)
    (out / strategy_report.REPORT_FILE).write_text(json.dumps(doc), encoding='utf-8')
    return doc


def test_completed_match_queues_one_strategy_job(env):
    _, _, outputs, calls, _ = env
    webapp.jobs['clip'] = {'status': 'completed', 'video_name': 'clip'}
    webapp._after_match_completed('clip')
    job = _wait('strategy_clip')
    assert job['kind'] == 'strategy' and job['status'] == 'completed'
    assert job['message'] == '策略报告已生成 / strategy report ready'
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[1] == 'main_strategy.py' and cmd[2] == str(outputs / 'clip')
    assert cmd[cmd.index('--athletes') + 1] == str(webapp.ATHLETES_PATH)
    assert cmd[cmd.index('--settings') + 1].replace('\\', '/').endswith('data/settings.json')
    assert '--focus' not in cmd
    assert webapp.jobs['clip'] == {'status': 'completed', 'video_name': 'clip'}


def test_no_second_report_job_while_one_is_queued(env, monkeypatch):
    _, _, _, calls, _ = env
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(1, on_queue_change=webapp._queue_changed))
    webapp._job_slots.submit('other', lambda release: None)  # holds the only slot
    webapp._after_match_completed('clip')
    webapp._after_match_completed('clip')
    assert webapp._job_slots.snapshot()['queued'] == ['strategy_clip']
    assert webapp.queue_strategy_job('clip') == (False, 'strategy_clip', False)
    assert calls == []
    webapp._job_slots.cancel('strategy_clip')
    webapp.jobs['strategy_clip']['status'] = 'cancelled'


def test_focus_is_passed_to_the_cli(env):
    _, _, _, calls, _ = env
    assert webapp.queue_strategy_job('clip', 'lower')[0] is True
    _wait('strategy_clip')
    assert calls[0][-2:] == ['--focus', 'lower']


def test_disabled_queues_nothing(env, monkeypatch):
    _, _, _, calls, _ = env
    monkeypatch.setenv('GB_STRATEGY_REPORT', 'off')
    webapp._after_match_completed('clip')
    assert 'strategy_clip' not in webapp.jobs and calls == []


def test_after_match_completed_never_raises(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('x')

    monkeypatch.setattr(webapp, 'queue_strategy_job', boom)
    webapp._after_match_completed('clip')


def test_report_job_failure_keeps_match_green(env):
    _, _, _, _, behaviour = env
    behaviour['code'] = 2
    behaviour['report'] = {'schema': 1, 'status': 'failed', 'by_lang': {},
                           'reason': {'code': 'timeout', 'params': {'sec': 300}}}
    webapp.jobs['clip'] = {'status': 'completed', 'video_name': 'clip'}
    webapp._after_match_completed('clip')
    job = _wait('strategy_clip')
    assert job['status'] == 'error' and job['message'] == 'LLM 超时 / LLM timed out'
    assert webapp.jobs['clip']['status'] == 'completed'
    assert webapp._job_slots.snapshot()['running'] == []


@pytest.mark.parametrize('reason,expected', [
    ({'code': 'llm_unreachable', 'params': {}}, '无法连接 LLM 服务 / LLM endpoint unreachable'),
    ({'code': 'model_missing', 'params': {'model': 'qwen3.8:27b'}},
     '模型未安装 / model not installed: qwen3.8:27b'),
    ({'code': 'bad_response', 'params': {}}, '策略报告失败 / strategy report failed'),
    (None, '策略报告失败 / strategy report failed'),
])
def test_failure_messages(env, reason, expected):
    _, _, _, _, behaviour = env
    behaviour['code'] = 2 if reason else 1
    behaviour['report'] = ({'schema': 1, 'status': 'failed', 'by_lang': {}, 'reason': reason}
                           if reason else None)
    webapp.queue_strategy_job('clip')
    assert _wait('strategy_clip')['message'] == expected


def test_launch_failure_frees_the_slot(env, monkeypatch):
    def broken(*a, **k):
        raise OSError('no')

    monkeypatch.setattr(webapp.subprocess, 'Popen', broken)
    webapp.queue_strategy_job('clip')  # the slot scheduler logs the failed start
    assert webapp.jobs['strategy_clip']['status'] == 'error'
    assert webapp.jobs['strategy_clip']['message'] == '策略报告无法启动 / strategy report could not start'
    assert webapp._job_slots.snapshot()['running'] == []


def test_regenerate_queues_a_job(env):
    c, *_ = env
    res = c.post('/api/strategy/clip/regenerate', json={'focus': 'upper'})
    assert res.status_code == 200
    body = res.get_json()
    assert body['ok'] is True and body['job_id'] == 'strategy_clip' and body['queued'] is False
    _wait('strategy_clip')


def test_regenerate_409_when_queued(env, monkeypatch):
    c, *_ = env
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(1, on_queue_change=webapp._queue_changed))
    webapp.jobs['other'] = {'status': 'running'}
    webapp._job_slots.submit('other', lambda release: None)  # holds the only slot
    first = c.post('/api/strategy/clip/regenerate', json={})
    assert first.status_code == 200 and first.get_json()['queued'] is True
    assert first.get_json()['position'] == 1
    second = c.post('/api/strategy/clip/regenerate', json={})
    assert second.status_code == 409
    got = c.get('/api/strategy/clip').get_json()
    assert got['status'] == 'pending' and got['job'] == {
        'id': 'strategy_clip', 'status': 'pending', 'position': 1}
    webapp._job_slots.cancel('strategy_clip')
    webapp.jobs['strategy_clip']['status'] = 'cancelled'


def test_regenerate_blocked_while_analysing(env):
    c, _, _, calls, _ = env
    webapp.jobs['clip'] = {'status': 'running', 'video_name': 'clip'}
    res = c.post('/api/strategy/clip/regenerate', json={})
    assert res.status_code == 409 and calls == []
    assert 'strategy_clip' not in webapp.jobs


def test_regenerate_bad_focus_400(env):
    c, *_ = env
    for bad in ('left', 3, ''):
        assert c.post('/api/strategy/clip/regenerate', json={'focus': bad}).status_code == 400


def test_regenerate_404_without_analysis(env):
    c, _, outputs, *_ = env
    (outputs / 'clip' / 'rally_segments.json').unlink()
    assert c.post('/api/strategy/clip/regenerate', json={}).status_code == 404


def test_get_none_then_report(env):
    c, _, outputs, *_ = env
    got = c.get('/api/strategy/clip').get_json()
    assert got == {'enabled': True, 'status': 'none', 'stale': False, 'job': None,
                   'report': None}
    out = outputs / 'clip'
    from badminton_analysis.strategy.facts import current_inputs
    _write_report(out, inputs=current_inputs(out, {}))
    got = c.get('/api/strategy/clip').get_json()
    assert got['status'] == 'ok' and got['stale'] is False and got['report']['schema'] == 1
    (out / 'insights.json').write_text('{}', encoding='utf-8')
    assert c.get('/api/strategy/clip').get_json()['stale'] is True


def test_get_reports_disabled(env, monkeypatch):
    c, *_ = env
    monkeypatch.setenv('GB_STRATEGY_REPORT', 'off')
    assert c.get('/api/strategy/clip').get_json()['enabled'] is False


def test_markdown_download(env):
    c, _, outputs, *_ = env
    assert c.get('/api/strategy/clip.md').status_code == 404
    _write_report(outputs / 'clip')
    res = c.get('/api/strategy/clip.md?lang=zh')
    assert res.status_code == 200
    assert res.headers['Content-Type'] == 'text/markdown; charset=utf-8'
    assert res.headers['Content-Disposition'] == 'attachment; filename="clip-strategy-zh.md"'
    odd = c.get('/api/strategy/clip.md?lang=fr')
    assert odd.headers['Content-Disposition'] == 'attachment; filename="clip-strategy-en.md"'


def test_markdown_download_non_ascii_stem(env):
    c, _, outputs, *_ = env
    d = outputs / '比赛'
    d.mkdir()
    _write_report(d)
    res = c.get('/api/strategy/比赛.md')
    assert res.status_code == 200
    assert "filename*=UTF-8''" in res.headers['Content-Disposition']


def test_unsafe_stem_404(env):
    c, *_ = env
    assert c.get('/api/strategy/ghost').status_code == 404
    assert c.get('/api/strategy/ghost.md').status_code == 404
    assert c.post('/api/strategy/ghost/regenerate', json={}).status_code == 404
    assert c.get('/api/strategy/..%2Fx').status_code == 404


def test_jobs_list_kind_strategy(env):
    c, *_ = env
    webapp.jobs['strategy_clip'] = {'status': 'error', 'video_name': 'clip', 'stage': 'error',
                                    'kind': 'strategy', 'message': 'LLM 超时 / LLM timed out'}
    (row,) = c.get('/api/jobs').get_json()
    assert row['id'] == 'strategy_clip' and row['kind'] == 'strategy' and row['video'] == 'clip'
    assert row['message'] == 'LLM 超时 / LLM timed out'


def test_delete_all_removes_the_report(env):
    c, _, outputs, *_ = env
    _write_report(outputs / 'clip')
    webapp.jobs['strategy_clip'] = {'status': 'completed', 'video_name': 'clip'}
    res = c.post('/api/delete/clip', json={'scope': 'all'})
    assert res.status_code == 200
    assert not (outputs / 'clip').exists()
    assert 'strategy_clip' not in webapp.jobs
