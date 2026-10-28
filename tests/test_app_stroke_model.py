"""Stroke model card, retrain and relabel jobs in app.py (badminton_analysis/web/stroke_model_routes.py)."""
import json
import threading

import pytest

import app as webapp
from badminton_analysis.stroke import model_eval
from badminton_analysis.web.job_slots import JobSlots


class _Proc:
    def __init__(self, code=0):
        self.returncode = code
        self.pid = 1

    def poll(self):
        return self.returncode

    def wait(self):
        return self.returncode


READY = {'live': {'version': 'v1', 'metrics': None, 'bar_met': False, 'bar_reasons': []},
         'counts': {'train': 60, 'test': 25, 'new_train': 60}, 'can_retrain': True,
         'missing': [], 'history': []}


@pytest.fixture
def env(tmp_path, monkeypatch):
    outputs = tmp_path / 'outputs'
    for stem, doubles in (('a', False), ('b', True), ('c', False)):
        (outputs / stem).mkdir(parents=True)
        (outputs / stem / 'strokes.json').write_text('{}', encoding='utf-8')
    (outputs / 'nostrokes').mkdir()
    monkeypatch.setattr(webapp, 'OUTPUTS', outputs)
    monkeypatch.setattr(webapp, 'WEIGHTS', tmp_path / 'weights')
    monkeypatch.setattr(webapp, 'STROKE_LABELS_DIR', tmp_path / 'data' / 'stroke_labels')
    (tmp_path / 'data' / 'stroke_labels').mkdir(parents=True)
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(2, on_queue_change=webapp._queue_changed))
    for j in ('train_strokes', 'relabel_strokes', 'a', 'b', 'c'):
        webapp.jobs.pop(j, None)
    webapp._stroke_model_cache.update(key=None, value=None)
    state = {'status': dict(READY), 'calls': 0}

    def fake_status(*args):
        state['calls'] += 1
        return state['status']

    monkeypatch.setattr(model_eval, 'status', fake_status)
    trackers = []
    real_thread = threading.Thread

    def thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        trackers.append(t)
        return t

    monkeypatch.setattr(webapp.threading, 'Thread', thread)
    popen = {'cmds': [], 'code': 0, 'final': None}

    def fake_popen(cmd, *args, **kwargs):
        popen['cmds'].append(cmd)
        if popen['final'] is not None and 'badminton_analysis.stroke.retrain' in cmd:
            kwargs['stdout'].write('training...\n' + json.dumps(popen['final']) + '\n')
            kwargs['stdout'].flush()
        return _Proc(popen['code'])

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    webapp.app.config['TESTING'] = True
    yield webapp.app.test_client(), tmp_path, popen, state
    for t in trackers:
        t.join(5)
    for j in ('train_strokes', 'relabel_strokes'):
        webapp.jobs.pop(j, None)


def _wait(job_id, statuses=('completed', 'error')):
    for _ in range(500):
        job = webapp.jobs.get(job_id)
        if job and job.get('status') in statuses and job.get('ended'):
            return job
        threading.Event().wait(0.01)
    raise AssertionError(f'{job_id} never reached {statuses}: {webapp.jobs.get(job_id)}')


def test_retrain_refused_when_not_enough_labels(env):
    client, _, popen, state = env
    state['status'] = dict(READY, can_retrain=False, missing=['3 more train labels'])
    r = client.post('/api/stroke-model/retrain')
    assert r.status_code == 409
    body = r.get_json()
    assert body['reason'] == 'not_enough_labels' and body['missing'] == ['3 more train labels']
    assert popen['cmds'] == []


def test_retrain_queues_the_child_and_a_second_click_is_busy(env, monkeypatch):
    client, tmp, popen, _ = env
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(1, on_queue_change=webapp._queue_changed))
    webapp._job_slots.submit('other', lambda release: None)       # holds the only slot
    r = client.post('/api/stroke-model/retrain')
    assert r.status_code == 200 and r.get_json() == {'job_id': 'train_strokes', 'queued': True}
    assert webapp.jobs['train_strokes']['kind'] == 'training'
    again = client.post('/api/stroke-model/retrain')
    assert again.status_code == 409 and again.get_json()['reason'] == 'busy'
    assert client.get('/api/stroke-model').get_json()['job']['id'] == 'train_strokes'
    webapp._job_slots.cancel('train_strokes')
    webapp.jobs['train_strokes']['status'] = 'cancelled'


def test_retrain_command_and_kept_queues_relabel(env):
    client, tmp, popen, _ = env
    popen['final'] = {'kept': True, 'reason': 'ok', 'candidate': {}, 'live': None}
    assert client.post('/api/stroke-model/retrain').status_code == 200
    job = _wait('train_strokes')
    assert job['status'] == 'completed' and job['message'].startswith('新模型已采用')
    cmd = popen['cmds'][0]
    assert cmd[1:3] == ['-m', 'badminton_analysis.stroke.retrain']
    assert cmd[cmd.index('--weights-dir') + 1] == str(tmp / 'weights')
    assert cmd[cmd.index('--labels-dir') + 1] == str(tmp / 'data' / 'stroke_labels')
    relabel = _wait('relabel_strokes')
    assert relabel['status'] == 'completed' and relabel['kind'] == 'training'
    rcmd = popen['cmds'][1]
    assert rcmd[1:3] == ['-m', 'badminton_analysis.stroke.relabel']
    assert rcmd[rcmd.index('--labels-dir') + 1] == str(tmp / 'data' / 'stroke_labels')
    dirs = [c for c in rcmd if str(tmp / 'outputs') in c]
    assert sorted(d.replace('\\', '/').rsplit('/', 1)[1] for d in dirs) == ['a', 'b', 'c']


def test_rejected_does_not_relabel_and_invalidates_the_cache(env):
    client, _, popen, state = env
    client.get('/api/stroke-model')
    assert state['calls'] == 1
    popen['final'] = {'kept': False, 'reason': 'candidate accuracy 0.5 < live 0.6',
                      'candidate': {}, 'live': {}}
    client.post('/api/stroke-model/retrain')
    job = _wait('train_strokes')
    assert job['status'] == 'completed' and job['message'].startswith('新模型未采用')
    assert 'relabel_strokes' not in webapp.jobs
    assert len(popen['cmds']) == 1
    client.get('/api/stroke-model')
    assert state['calls'] == 2       # recomputed once after the run


def test_failed_training_is_an_error_job(env):
    client, _, popen, _ = env
    popen['code'] = 1
    client.post('/api/stroke-model/retrain')
    job = _wait('train_strokes')
    assert job['status'] == 'error' and job['message'] == '训练失败 / training failed'


def test_status_is_cached_until_the_key_changes(env):
    client, tmp, _, state = env
    for _ in range(3):
        assert client.get('/api/stroke-model').status_code == 200
    assert state['calls'] == 1
    (tmp / 'data' / 'stroke_labels' / 'a.json').write_text('{}', encoding='utf-8')
    client.get('/api/stroke-model')
    assert state['calls'] == 2
    (tmp / 'weights').mkdir(exist_ok=True)
    (tmp / 'weights' / 'stroke-classifier.pt').write_bytes(b'x')
    client.get('/api/stroke-model')
    assert state['calls'] == 3


def test_status_is_not_computed_while_training_runs(env):
    client, _, _, state = env
    client.get('/api/stroke-model')
    webapp.jobs['train_strokes'] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    (webapp.STROKE_LABELS_DIR / 'x.json').write_text('{}', encoding='utf-8')
    body = client.get('/api/stroke-model').get_json()
    assert state['calls'] == 1 and body['job']['status'] == 'running' and body['live']['version'] == 'v1'
    webapp.jobs['train_strokes']['status'] = 'cancelled'


def test_status_with_no_cache_while_training_is_a_stub(env, monkeypatch):
    client, _, _, state = env
    webapp.jobs['train_strokes'] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    body = client.get('/api/stroke-model').get_json()
    assert state['calls'] == 0 and body['can_retrain'] is False and body['live'] is None
    webapp.jobs['train_strokes']['status'] = 'cancelled'


def test_pause_refused(env):
    client, *_ = env
    for job_id in ('train_strokes', 'relabel_strokes'):
        webapp.jobs[job_id] = {'status': 'running', 'kind': 'training', 'proc': _Proc(None)}
        r = client.post(f'/api/jobs/{job_id}/pause')
        assert r.status_code == 409 and '不能暂停' in r.get_json()['error']
        webapp.jobs[job_id]['status'] = 'cancelled'


def test_job_listing_kind_is_training(env):
    client, *_ = env
    webapp.jobs['train_strokes'] = {'status': 'running', 'video_name': 'stroke-classifier',
                                    'message': 'x', 'updated': 1.0}
    rows = {r['id']: r for r in client.get('/api/jobs').get_json()}
    assert rows['train_strokes']['kind'] == 'training'
    webapp.jobs['train_strokes']['status'] = 'cancelled'


def test_rollback_busy_none_and_ok(env, monkeypatch):
    client, *_ = env
    webapp.jobs['train_strokes'] = {'status': 'running', 'kind': 'training'}
    assert client.post('/api/stroke-model/rollback').status_code == 409
    webapp.jobs['train_strokes']['status'] = 'cancelled'
    monkeypatch.setattr(model_eval, 'rollback', lambda w: False)
    assert client.post('/api/stroke-model/rollback').status_code == 404
    monkeypatch.setattr(model_eval, 'rollback', lambda w: True)
    r = client.post('/api/stroke-model/rollback')
    assert r.status_code == 200
    assert _wait('relabel_strokes')['status'] == 'completed'


def test_relabel_skips_an_active_match(env):
    _, tmp, popen, _ = env
    webapp.jobs['a'] = {'status': 'running', 'video_name': 'a'}
    ok, job_id, _ = webapp.queue_relabel_strokes()
    assert ok and job_id == 'relabel_strokes'
    _wait('relabel_strokes')
    dirs = [c.replace('\\', '/').rsplit('/', 1)[1] for c in popen['cmds'][0] if str(tmp / 'outputs') in c]
    assert sorted(dirs) == ['b', 'c']
    webapp.jobs['a']['status'] = 'cancelled'


def test_relabel_with_nothing_to_do_completes_without_a_child(env, monkeypatch):
    _, tmp, popen, _ = env
    monkeypatch.setattr(webapp, 'OUTPUTS', tmp / 'empty')
    webapp.queue_relabel_strokes()
    assert _wait('relabel_strokes')['status'] == 'completed'
    assert popen['cmds'] == []


def test_split_lock_uses_model_eval(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(model_eval, 'split_locked', lambda stem, w: stem == 'a')
    assert client.get('/api/stroke-labels/a').get_json()['split_locked'] is True
    assert client.get('/api/stroke-labels/b').get_json()['split_locked'] is False
