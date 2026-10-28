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
    monkeypatch.setattr(model_eval, 'rollback', lambda w, **k: False)
    assert client.post('/api/stroke-model/rollback').status_code == 404
    monkeypatch.setattr(model_eval, 'rollback', lambda w, **k: True)
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


# ---- cancel, split lock, relabel vs label saves (QA findings 2, 6, 8)

class _BlockingProc:
    """A child that runs until ``kill`` is called, then exits with -9."""
    pid = 4242

    def __init__(self):
        self.returncode = None
        self.done = threading.Event()

    def poll(self):
        return self.returncode

    def wait(self):
        self.done.wait(10)
        return self.returncode

    def kill(self):
        self.returncode = -9
        self.done.set()


@pytest.fixture
def blocking(env, monkeypatch):
    _, tmp, popen, _ = env
    procs, killed = [], []

    def fake_popen(cmd, *args, **kwargs):
        popen['cmds'].append(cmd)
        proc = _BlockingProc()
        procs.append(proc)
        return proc

    def fake_kill_tree(pid, should_abort=None):
        killed.append(pid)
        procs[-1].kill()

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(webapp.proc_control, 'kill_tree', fake_kill_tree)
    return procs, killed


def _wait_status(job_id, status):
    for _ in range(500):
        job = webapp.jobs.get(job_id)
        if job and job.get('status') == status:
            return job
        threading.Event().wait(0.01)
    raise AssertionError(f'{job_id} never reached {status}: {webapp.jobs.get(job_id)}')


def test_cancel_a_running_training_kills_the_tree_and_cleans_up(env, blocking):
    client, tmp, popen, _ = env
    procs, killed = blocking
    cand = tmp / 'weights' / 'candidates'
    cand.mkdir(parents=True)
    (cand / 'stroke-classifier-20261028T000000Z.pt').write_bytes(b'x')
    (cand / 'stroke-classifier-20261028T000000Z.json').write_text('{}')
    assert client.post('/api/stroke-model/retrain').status_code == 200
    _wait_status('train_strokes', 'running')
    r = client.post('/api/jobs/train_strokes/cancel')
    assert r.status_code == 200 and killed == [4242]
    job = _wait('train_strokes', ('cancelled',))
    assert job['stage'] == 'cancelled'
    assert not list(cand.glob('stroke-classifier-*'))
    assert 'relabel_strokes' not in webapp.jobs        # a cancel never queues the relabel
    assert len(popen['cmds']) == 1
    assert client.post('/api/stroke-model/retrain').status_code == 200   # the slot is free again
    _wait_status('train_strokes', 'running')
    client.post('/api/jobs/train_strokes/cancel')
    _wait('train_strokes', ('cancelled',))


def test_cancel_is_refused_once_the_model_is_being_promoted(env, blocking):
    client, tmp, _, _ = env
    procs, killed = blocking
    assert client.post('/api/stroke-model/retrain').status_code == 200
    job = _wait_status('train_strokes', 'running')
    with open(job['log_path'], 'a', encoding='utf-8') as fh:
        fh.write('training...\nPHASE promote\n')
    r = client.post('/api/jobs/train_strokes/cancel')
    assert r.status_code == 409 and r.get_json()['reason'] == 'promoting' and killed == []
    procs[-1].kill()
    _wait('train_strokes', ('completed', 'error'))


def test_cancel_a_running_relabel_does_not_rerun_it(env, blocking):
    client, tmp, popen, _ = env
    procs, killed = blocking
    webapp.queue_relabel_strokes()
    _wait_status('relabel_strokes', 'running')
    assert webapp.queue_relabel_strokes()[0] is False          # asks for a rerun
    assert webapp.jobs['relabel_strokes'].get('rerun') is True
    assert client.post('/api/jobs/relabel_strokes/cancel').status_code == 200
    _wait('relabel_strokes', ('cancelled',))
    assert len(popen['cmds']) == 1 and killed == [4242]


def test_cancel_of_a_finished_or_unstarted_training_job_is_409(env):
    client, *_ = env
    webapp.jobs['train_strokes'] = {'status': 'running', 'kind': 'training', 'proc': _Proc(0)}
    assert client.post('/api/jobs/train_strokes/cancel').status_code == 409
    webapp.jobs['train_strokes'] = {'status': 'running', 'kind': 'training'}
    assert client.post('/api/jobs/train_strokes/cancel').status_code == 409
    webapp.jobs['train_strokes']['status'] = 'cancelled'


def test_split_is_locked_while_a_training_job_is_queued_or_running(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(model_eval, 'split_locked', lambda stem, w: False)
    assert client.get('/api/stroke-labels/a').get_json()['split_locked'] is False
    for status in ('pending', 'running'):
        webapp.jobs['train_strokes'] = {'status': status, 'kind': 'training'}
        assert client.get('/api/stroke-labels/a').get_json()['split_locked'] is True
        r = client.put('/api/stroke-labels/a', json={'revision': 0, 'labels': [], 'split': 'test'})
        assert r.status_code == 409 and r.get_json()['reason'] == 'split_locked'
    webapp.jobs['train_strokes']['status'] = 'completed'
    assert client.get('/api/stroke-labels/a').get_json()['split_locked'] is False
    webapp.jobs['relabel_strokes'] = {'status': 'running', 'kind': 'training'}
    assert client.get('/api/stroke-labels/a').get_json()['split_locked'] is False
    webapp.jobs['relabel_strokes']['status'] = 'cancelled'


def _owner_run(outputs, stem):
    from badminton_analysis.stroke import stroke_classifier as sc
    from badminton_analysis.stroke.stroke_types import group_of
    run = outputs / stem
    run.mkdir(parents=True, exist_ok=True)
    (run / 'hits.json').write_text(json.dumps({'fps': 60.0, 'hits': []}), encoding='utf-8')
    rows = [{'frame': 60, 'hitter': 'lower', 'stroke': 'clear', 'group': group_of('clear'), 'confidence': 0.6}]
    (run / 'strokes.json').write_text(
        json.dumps(sc.build_payload(rows, {'version': 't'}, 'model', 'tracknet')), encoding='utf-8')
    return run


def _stroke_of(run):
    return json.loads((run / 'strokes.json').read_text(encoding='utf-8'))['strokes'][0]['stroke']


LABEL = {'time_sec': 1.0, 'hitter': 'lower', 'stroke': 'smash', 'kind': 'label'}


def test_label_save_during_a_relabel_job_skips_apply_and_the_job_applies_it_at_its_end(env, monkeypatch):
    client, tmp, popen, _ = env
    run = _owner_run(webapp.OUTPUTS, 'a')
    saved = {}

    def fake_popen(cmd, *args, **kwargs):
        popen['cmds'].append(cmd)
        # the owner saves a label while the child is running
        saved['res'] = client.put('/api/stroke-labels/a', json={'revision': 0, 'labels': [LABEL]})
        return _Proc(0)

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    webapp.queue_relabel_strokes()
    _wait('relabel_strokes')
    body = saved['res'].get_json()
    assert saved['res'].status_code == 200
    assert body['applied'] is False and body['apply_skipped'] == 'job_active'
    assert _stroke_of(run) == 'smash'             # applied by the job's final re-apply
    # once the job has finished a save applies itself again
    r = client.put('/api/stroke-labels/a', json={'revision': 1, 'labels': []})
    assert r.get_json()['applied'] is True and _stroke_of(run) == 'clear'


def test_rollback_records_the_train_count(env, monkeypatch):
    client, *_ = env
    seen = {}
    monkeypatch.setattr(model_eval, 'train_count', lambda d: 77)
    monkeypatch.setattr(model_eval, 'rollback', lambda w, **k: seen.update(k) or True)
    assert client.post('/api/stroke-model/rollback').status_code == 200
    assert seen == {'train_labels': 77}
    _wait('relabel_strokes')


# ---- review findings: cancel applies saved labels, promote re-check, reason codes, can_rollback

def test_cancel_a_running_relabel_applies_labels_saved_during_it(env, blocking):
    client, *_ = env
    procs, killed = blocking
    run = _owner_run(webapp.OUTPUTS, 'a')
    webapp.queue_relabel_strokes()
    _wait_status('relabel_strokes', 'running')
    res = client.put('/api/stroke-labels/a', json={'revision': 0, 'labels': [LABEL]})
    assert res.get_json()['apply_skipped'] == 'job_active' and _stroke_of(run) == 'clear'
    assert client.post('/api/jobs/relabel_strokes/cancel').status_code == 200
    _wait('relabel_strokes', ('cancelled',))
    assert _stroke_of(run) == 'smash'            # applied by the cancel, not left for the next save
    assert client.put('/api/stroke-labels/a', json={'revision': 1, 'labels': []}).get_json()['applied'] is True


def test_only_stems_skipped_during_the_job_are_reapplied(env, monkeypatch):
    from badminton_analysis.stroke import owner_labels
    client, tmp, popen, _ = env
    run_a = _owner_run(webapp.OUTPUTS, 'a')
    run_b = _owner_run(webapp.OUTPUTS, 'b')
    owner_labels.save(owner_labels.labels_path(webapp.STROKE_LABELS_DIR, 'b'),
                      {'schema': owner_labels.SCHEMA, 'revision': 1, 'split': 'train', 'labels': [LABEL]})

    def fake_popen(cmd, *args, **kwargs):
        popen['cmds'].append(cmd)
        client.put('/api/stroke-labels/a', json={'revision': 0, 'labels': [LABEL]})
        return _Proc(0)

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    webapp.queue_relabel_strokes()
    _wait('relabel_strokes')
    assert _stroke_of(run_a) == 'smash'
    assert _stroke_of(run_b) == 'clear'          # b had no save during the job: untouched
    assert not webapp._skipped_stroke_stems


def test_cancel_rechecks_the_promote_line_right_before_the_kill(env, blocking, monkeypatch):
    client, tmp, _, _ = env
    procs, killed = blocking
    client.post('/api/stroke-model/retrain')
    job = _wait_status('train_strokes', 'running')

    def fake_kill_tree(pid, should_abort=None):
        with open(job['log_path'], 'a', encoding='utf-8') as fh:      # the child reaches the swap now
            fh.write('PHASE promote\n')
        assert should_abort() is True
        return False

    monkeypatch.setattr(webapp.proc_control, 'kill_tree', fake_kill_tree)
    r = client.post('/api/jobs/train_strokes/cancel')
    assert r.status_code == 409 and r.get_json()['reason'] == 'promoting'
    assert not job.get('cancel_requested') and job['status'] == 'running'
    procs[-1].kill()
    assert _wait('train_strokes', ('completed', 'error'))['status'] != 'cancelled'


def test_training_job_view_and_listing_carry_the_reason_code(env):
    client, _, popen, _ = env
    popen['final'] = {'kept': False, 'reason': {'code': 'lower_accuracy', 'candidate': 0.5, 'live': 0.6},
                      'candidate': {}, 'live': {}}
    client.post('/api/stroke-model/retrain')
    job = _wait('train_strokes')
    assert job['reason_code']['code'] == 'lower_accuracy' and 'accuracy' not in job['message']
    rows = {r['id']: r for r in client.get('/api/jobs').get_json()}
    assert rows['train_strokes']['reason_code'] == {'code': 'lower_accuracy', 'candidate': 0.5, 'live': 0.6}
    webapp.jobs['relabel_strokes'] = {'status': 'running', 'kind': 'training', 'message': 'm',
                                      'reason_code': {'code': 'x'}}
    assert webapp._training_job_view()['reason_code'] == {'code': 'x'}
    webapp.jobs['relabel_strokes']['status'] = 'cancelled'


def test_status_offers_can_rollback_from_the_history_dirs(env):
    client, tmp, _, _ = env
    assert client.get('/api/stroke-model').get_json()['can_rollback'] is False
    old = tmp / 'weights' / 'history' / '20261001T000000000Z'
    old.mkdir(parents=True)
    (old / 'stroke-classifier.pt').write_bytes(b'x')
    assert client.get('/api/stroke-model').get_json()['can_rollback'] is True
    (old / '.retired').write_text('')
    assert client.get('/api/stroke-model').get_json()['can_rollback'] is False
