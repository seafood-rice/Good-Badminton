"""Rally model card, retrain and re-segment jobs in app.py (badminton_analysis/web/rally_model_routes.py)."""
import json
import threading

import pytest

import app as webapp
from badminton_analysis.outcomes import build as outcomes_build
from badminton_analysis.outcomes import model_eval
from badminton_analysis.stroke import owner_labels
from badminton_analysis.web.job_slots import JobSlots
from tests.test_app_stroke_model import _BlockingProc, _Proc, _wait, _wait_status

IDS = ('train_strokes', 'relabel_strokes', 'train_rally', 'resegment_rallies')
READY = {'live': {'segmenter': None, 'decoder': None},
         'counts': {'matches': 2, 'points': 49, 'new_points': 49}, 'can_retrain': True,
         'missing': [], 'skipped': [], 'history': [], 'can_rollback': False}
REJECTED = {'segmenter': {'kept': False, 'reason': {'code': 'lower_f1', 'candidate': 0.45, 'swing': 0.53}},
            'decoder': {'kept': False, 'reason': {'code': 'precision_unreachable', 'target': 0.9}},
            'totals': {'seg_f1': 0.45}}
KEPT = {'segmenter': {'kept': True, 'reason': {'code': 'f1_ok'}},
        'decoder': {'kept': False, 'reason': {'code': 'low_precision', 'value': 0.5, 'bar': 0.85}}, 'totals': {}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    outputs = tmp_path / 'outputs'
    for stem, ready in (('a', True), ('b', True), ('c', False), ('d', True)):
        (outputs / stem).mkdir(parents=True)
        if ready:
            (outputs / stem / 'hits.json').write_text('{}', encoding='utf-8')
            (outputs / stem / 'detections.jsonl').write_text('', encoding='utf-8')
    monkeypatch.setattr(webapp, 'OUTPUTS', outputs)
    monkeypatch.setattr(webapp, 'WEIGHTS', tmp_path / 'weights')
    monkeypatch.setattr(webapp, 'VIDEOS', tmp_path / 'videos')
    monkeypatch.setattr(webapp, 'STROKE_LABELS_DIR', tmp_path / 'data' / 'stroke_labels')
    monkeypatch.setattr(webapp, 'RALLY_LABELS_DIR', tmp_path / 'data' / 'rally_labels')
    monkeypatch.setattr(webapp, 'ATHLETES_PATH', tmp_path / 'data' / 'athletes.json')
    (tmp_path / 'data' / 'stroke_labels').mkdir(parents=True)
    (tmp_path / 'data' / 'rally_labels').mkdir(parents=True)
    (tmp_path / 'videos').mkdir()
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(2, on_queue_change=webapp._queue_changed))
    for j in IDS + ('a', 'b', 'c', 'd'):
        webapp.jobs.pop(j, None)
    webapp._rally_model_cache.update(key=None, value=None)
    webapp._skipped_rally_stems.clear()
    webapp._skipped_stroke_stems.clear()
    state = {'status': dict(READY), 'calls': 0}

    def fake_status(*args, **kwargs):
        state['kwargs'] = kwargs
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
        if popen['final'] is not None and 'badminton_analysis.outcomes.retrain' in cmd:
            kwargs['stdout'].write('measuring...\n' + json.dumps(popen['final']) + '\n')
            kwargs['stdout'].flush()
        return _Proc(popen['code'])

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    webapp.app.config['TESTING'] = True
    yield webapp.app.test_client(), tmp_path, popen, state
    for t in trackers:
        t.join(5)
    for j in IDS:
        webapp.jobs.pop(j, None)


# ---- card and gates --------------------------------------------------------------------------

def test_card_has_the_job_and_can_rollback(env):
    client, _, _, state = env
    state['status'] = dict(READY, can_rollback=True)
    body = client.get('/api/rally-model').get_json()
    assert body['counts']['points'] == 49 and body['job'] is None and body['can_rollback'] is True
    webapp.jobs['resegment_rallies'] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    assert client.get('/api/rally-model').get_json()['job']['id'] == 'resegment_rallies'
    webapp.jobs['resegment_rallies']['status'] = 'cancelled'


def test_status_is_cached_until_the_key_changes(env):
    client, tmp, _, state = env
    for _ in range(3):
        assert client.get('/api/rally-model').status_code == 200
    assert state['calls'] == 1
    (tmp / 'data' / 'rally_labels' / 'a.json').write_text('{}', encoding='utf-8')
    client.get('/api/rally-model')
    assert state['calls'] == 2
    (tmp / 'weights').mkdir(exist_ok=True)
    (tmp / 'weights' / 'rally-segmenter.json').write_text('{}', encoding='utf-8')
    client.get('/api/rally-model')
    assert state['calls'] == 3


def test_status_is_a_stub_while_a_training_job_runs(env):
    client, _, _, state = env
    webapp.jobs['train_strokes'] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    body = client.get('/api/rally-model').get_json()
    assert state['calls'] == 0 and body['can_retrain'] is False and body['live'] is None
    webapp.jobs['train_strokes']['status'] = 'cancelled'


def test_retrain_refused_when_not_enough_labels(env):
    client, _, popen, state = env
    state['status'] = dict(READY, can_retrain=False, missing=[{'code': 'matches', 'need': 2, 'have': 1}])
    r = client.post('/api/rally-model/retrain')
    assert r.status_code == 409
    body = r.get_json()
    assert body['reason'] == 'not_enough_labels' and body['missing'] == [{'code': 'matches', 'need': 2, 'have': 1}]
    assert popen['cmds'] == []


@pytest.mark.parametrize('other', ['train_strokes', 'relabel_strokes', 'resegment_rallies'])
def test_one_training_job_across_stroke_and_rally_ids(env, other):
    client, _, popen, _ = env
    webapp.jobs[other] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    r = client.post('/api/rally-model/retrain')
    assert r.status_code == 409 and r.get_json()['reason'] == 'busy'
    assert webapp.queue_train_rally()[0] is False and webapp.queue_resegment_rallies()[0] is False
    assert client.post('/api/rally-model/rollback').status_code == 409
    assert popen['cmds'] == []
    webapp.jobs[other]['status'] = 'cancelled'


def test_a_running_rally_job_blocks_the_stroke_card_too(env):
    client, *_ = env
    webapp.jobs['train_rally'] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    assert client.post('/api/stroke-model/retrain').status_code == 409
    assert webapp.queue_relabel_strokes()[0] is False
    webapp.jobs['train_rally']['status'] = 'cancelled'


# ---- the retrain job -------------------------------------------------------------------------

def test_retrain_queues_the_child_and_a_second_click_is_busy(env, monkeypatch):
    client, tmp, popen, _ = env
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(1, on_queue_change=webapp._queue_changed))
    webapp._job_slots.submit('other', lambda release: None)       # holds the only slot
    r = client.post('/api/rally-model/retrain')
    assert r.status_code == 200 and r.get_json() == {'job_id': 'train_rally', 'queued': True}
    job = webapp.jobs['train_rally']
    assert job['kind'] == 'training' and job['video_name'] == 'rally-models'
    again = client.post('/api/rally-model/retrain')
    assert again.status_code == 409 and again.get_json()['reason'] == 'busy'
    webapp._job_slots.cancel('train_rally')
    job['status'] = 'cancelled'


def test_retrain_command(env):
    client, tmp, popen, _ = env
    popen['final'] = KEPT
    assert client.post('/api/rally-model/retrain').status_code == 200
    _wait('train_rally')
    cmd = popen['cmds'][0]
    assert cmd[1:3] == ['-m', 'badminton_analysis.outcomes.retrain']
    assert cmd[cmd.index('--weights-dir') + 1] == str(tmp / 'weights')
    assert cmd[cmd.index('--labels-dir') + 1] == str(tmp / 'data' / 'rally_labels')
    assert cmd[cmd.index('--results-dir') + 1] == str(tmp / 'outputs')
    assert cmd[cmd.index('--athletes') + 1] == str(tmp / 'data' / 'athletes.json')


def test_kept_queues_the_resegment_job_for_every_result_with_learned_inputs(env):
    client, tmp, popen, _ = env
    webapp.jobs['a'] = {'status': 'running', 'video_name': 'a'}          # an analysis is running on a
    popen['final'] = KEPT
    client.post('/api/rally-model/retrain')
    job = _wait('train_rally')
    assert job['status'] == 'completed' and job['message'].startswith('已采用一个回合模型')
    seg = _wait('resegment_rallies')
    assert seg['status'] == 'completed' and seg['kind'] == 'training'
    cmd = popen['cmds'][1]
    assert cmd[1:3] == ['-m', 'badminton_analysis.outcomes.resegment']
    assert cmd[cmd.index('--stroke-labels-dir') + 1] == str(tmp / 'data' / 'stroke_labels')
    assert cmd[cmd.index('--weights-dir') + 1] == str(tmp / 'weights')
    dirs = [c.replace('\\', '/').rsplit('/', 1)[1] for c in cmd if str(tmp / 'outputs') in c]
    assert sorted(dirs) == ['b', 'd']                # a is being analysed; c has no hits / detections
    webapp.jobs['a']['status'] = 'cancelled'


def test_rejected_says_why_with_numbers_and_does_not_resegment(env):
    client, _, popen, state = env
    client.get('/api/rally-model')
    assert state['calls'] == 1
    popen['final'] = REJECTED
    client.post('/api/rally-model/retrain')
    job = _wait('train_rally')
    assert job['status'] == 'completed' and job['message'].startswith('回合模型未采用')
    assert job['reason_code'] == {'code': 'lower_f1', 'candidate': 0.45, 'swing': 0.53, 'model': 'segmenter'}
    assert 'resegment_rallies' not in webapp.jobs and len(popen['cmds']) == 1
    row = {r['id']: r for r in client.get('/api/jobs').get_json()}['train_rally']
    assert row['kind'] == 'training' and row['reason_code']['candidate'] == 0.45
    client.get('/api/rally-model')
    assert state['calls'] == 2           # recomputed once after the run


def test_failed_training_is_an_error_job(env):
    client, _, popen, _ = env
    popen['code'] = 1
    client.post('/api/rally-model/retrain')
    job = _wait('train_rally')
    assert job['status'] == 'error' and job['message'] == '训练失败 / training failed'


def test_job_listing_pause_and_kind(env):
    client, *_ = env
    for job_id in ('train_rally', 'resegment_rallies'):
        webapp.jobs[job_id] = {'status': 'running', 'kind': 'training', 'proc': _Proc(None),
                               'video_name': 'rally-models', 'message': 'x', 'updated': 1.0}
        r = client.post(f'/api/jobs/{job_id}/pause')
        assert r.status_code == 409 and '不能暂停' in r.get_json()['error']
    rows = {r['id']: r for r in client.get('/api/jobs').get_json()}
    assert rows['train_rally']['kind'] == 'training' and rows['resegment_rallies']['kind'] == 'training'
    for job_id in ('train_rally', 'resegment_rallies'):
        webapp.jobs[job_id]['status'] = 'cancelled'


def test_resegment_with_nothing_to_do_completes_without_a_child(env, monkeypatch):
    _, tmp, popen, _ = env
    monkeypatch.setattr(webapp, 'OUTPUTS', tmp / 'empty')
    webapp.queue_resegment_rallies()
    assert _wait('resegment_rallies')['status'] == 'completed'
    assert popen['cmds'] == []


# ---- rollback --------------------------------------------------------------------------------

def test_rollback_busy_none_and_ok(env, monkeypatch):
    client, *_ = env
    webapp.jobs['train_rally'] = {'status': 'running', 'kind': 'training'}
    assert client.post('/api/rally-model/rollback').status_code == 409
    webapp.jobs['train_rally']['status'] = 'cancelled'
    monkeypatch.setattr(model_eval, 'rollback', lambda w, **k: False)
    assert client.post('/api/rally-model/rollback').status_code == 404
    seen = {}
    monkeypatch.setattr(model_eval, 'rollback', lambda w, **k: seen.update(k) or True)
    r = client.post('/api/rally-model/rollback')
    assert r.status_code == 200 and 'points' in seen
    assert _wait('resegment_rallies')['status'] == 'completed'


# ---- cancel and failure ----------------------------------------------------------------------

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


def test_cancel_a_running_retrain_kills_the_tree_and_cleans_up(env, blocking):
    client, tmp, popen, _ = env
    procs, killed = blocking
    cand = tmp / 'weights' / 'candidates'
    cand.mkdir(parents=True)
    (cand / 'rally-segmenter-20261028T000000Z.json').write_text('{}')
    (cand / 'rally-decoder-20261028T000000Z.json').write_text('{}')
    (cand / 'stroke-classifier-1.pt').write_bytes(b'x')           # another model's candidate stays
    assert client.post('/api/rally-model/retrain').status_code == 200
    _wait_status('train_rally', 'running')
    r = client.post('/api/jobs/train_rally/cancel')
    assert r.status_code == 200 and killed == [4242]
    job = _wait('train_rally', ('cancelled',))
    assert job['stage'] == 'cancelled'
    assert not list(cand.glob('rally-*')) and (cand / 'stroke-classifier-1.pt').is_file()
    assert 'resegment_rallies' not in webapp.jobs and len(popen['cmds']) == 1


def test_cancel_is_refused_once_the_models_are_being_promoted(env, blocking):
    client, *_ = env
    procs, killed = blocking
    assert client.post('/api/rally-model/retrain').status_code == 200
    job = _wait_status('train_rally', 'running')
    with open(job['log_path'], 'a', encoding='utf-8') as fh:
        fh.write('measuring...\nPHASE promote\n')
    r = client.post('/api/jobs/train_rally/cancel')
    assert r.status_code == 409 and r.get_json()['reason'] == 'promoting' and killed == []
    procs[-1].kill()
    _wait('train_rally', ('completed', 'error'))


def _stub_stroke_apply(monkeypatch):
    applied = []
    monkeypatch.setattr(owner_labels, 'apply_to_result',
                        lambda d, ld, stem: applied.append(stem) or {'applied': False})
    return applied


def test_cancel_a_running_resegment_reapplies_stroke_labels_for_its_results(env, blocking, monkeypatch):
    client, tmp, popen, _ = env
    procs, killed = blocking
    applied = _stub_stroke_apply(monkeypatch)
    webapp.queue_resegment_rallies()
    _wait_status('resegment_rallies', 'running')
    assert webapp.queue_resegment_rallies()[0] is False            # asks for a rerun
    assert client.post('/api/jobs/resegment_rallies/cancel').status_code == 200
    _wait('resegment_rallies', ('cancelled',))
    assert sorted(applied) == ['a', 'b', 'd']                           # the child may have died mid-result
    assert len(popen['cmds']) == 1 and killed == [4242]            # a cancel never reruns


def test_a_failed_resegment_reapplies_stroke_labels_and_is_an_error(env, monkeypatch):
    client, tmp, popen, _ = env
    applied = _stub_stroke_apply(monkeypatch)
    popen['code'] = 1
    webapp.queue_resegment_rallies()
    job = _wait('resegment_rallies')
    assert job['status'] == 'error' and job['message'] == '重新分段失败 / re-segmenting failed'
    assert sorted(applied) == ['a', 'b', 'd']


def test_rally_label_saves_during_a_resegment_are_rebuilt_when_it_ends(env, blocking, monkeypatch):
    client, tmp, popen, _ = env
    procs, killed = blocking
    rebuilt = []
    monkeypatch.setattr(outcomes_build, 'rebuild_match',
                        lambda outputs, stem, *a, **k: rebuilt.append(stem) or [stem])
    webapp.queue_resegment_rallies()
    _wait_status('resegment_rallies', 'running')
    label = {'start_sec': 1.0, 'end_sec': 3.0, 'verdict': 'upper'}
    r = client.put('/api/rally-labels/b', json={'revision': 0, 'labels': [label]})
    assert r.status_code == 200 and r.get_json()['rebuild_skipped'] == 'job_active' and rebuilt == []
    procs[-1].returncode = 0
    procs[-1].done.set()
    _wait('resegment_rallies')
    assert rebuilt == ['b']
    r = client.put('/api/rally-labels/b', json={'revision': 1, 'labels': [label]})
    assert r.get_json().get('rebuild_skipped') is None and rebuilt == ['b', 'b']


# ---- review fixes ----------------------------------------------------------------------------

def test_decoder_only_keep_rebuilds_outcomes(env):
    """Review 3: a kept decoder alone re-segments nothing, so the job must ask for every outcome rebuilt."""
    client, tmp, popen, _ = env
    popen['final'] = {'segmenter': {'kept': False, 'reason': {'code': 'lower_f1'}},
                      'decoder': {'kept': True, 'reason': {'code': 'decoder_ok'}}, 'totals': {}}
    client.post('/api/rally-model/retrain')
    _wait('train_rally')
    _wait('resegment_rallies')
    assert '--rebuild-all' in popen['cmds'][1]
    # a segmenter-only keep does not
    webapp.jobs.pop('train_rally'), webapp.jobs.pop('resegment_rallies')
    popen['cmds'].clear()
    popen['final'] = KEPT
    client.post('/api/rally-model/retrain')
    _wait('train_rally')
    _wait('resegment_rallies')
    assert '--rebuild-all' not in popen['cmds'][1]


def test_rollback_rebuilds_every_outcome(env, monkeypatch):
    client, _, popen, _ = env
    monkeypatch.setattr(model_eval, 'rollback', lambda w, **k: True)
    assert client.post('/api/rally-model/rollback').status_code == 200
    _wait('resegment_rallies')
    assert '--rebuild-all' in popen['cmds'][0]


def test_a_cancelled_resegment_asks_for_the_rebuild_again(env, blocking):
    client, _, popen, _ = env
    webapp.queue_resegment_rallies(rebuild_all=True)
    _wait_status('resegment_rallies', 'running')
    assert client.post('/api/jobs/resegment_rallies/cancel').status_code == 200
    _wait('resegment_rallies', ('cancelled',))
    webapp.jobs.pop('resegment_rallies')
    webapp.queue_resegment_rallies()                  # the next job, queued for any reason, still rebuilds all
    _wait_status('resegment_rallies', 'running')
    assert '--rebuild-all' in popen['cmds'][1]
    client.post('/api/jobs/resegment_rallies/cancel')
    _wait('resegment_rallies', ('cancelled',))


def _stub_repair(monkeypatch):
    from badminton_analysis.outcomes import resegment
    calls = []
    monkeypatch.setattr(resegment, 'repair', lambda dirs, *a, **k: calls.append([d.name for d in dirs])
                        or {'repaired': [], 'failed': []})
    return calls


def test_resegment_cancel_repairs_the_results_it_was_given(env, blocking, monkeypatch):
    """Review 8: a cancelled child may have died between its two writes; every stem it got is repaired."""
    client, *_ = env
    calls = _stub_repair(monkeypatch)
    _stub_stroke_apply(monkeypatch)
    webapp.queue_resegment_rallies()
    _wait_status('resegment_rallies', 'running')
    client.post('/api/jobs/resegment_rallies/cancel')
    _wait('resegment_rallies', ('cancelled',))
    assert calls == [['a', 'b', 'd']]


def test_a_failed_resegment_repairs_the_results_it_was_given(env, monkeypatch):
    _, _, popen, _ = env
    calls = _stub_repair(monkeypatch)
    _stub_stroke_apply(monkeypatch)
    popen['code'] = 1
    webapp.queue_resegment_rallies()
    assert _wait('resegment_rallies')['status'] == 'error'
    assert calls == [['a', 'b', 'd']]


def test_a_clean_resegment_runs_no_repair(env, monkeypatch):
    calls = _stub_repair(monkeypatch)
    webapp.queue_resegment_rallies()
    assert _wait('resegment_rallies')['status'] == 'completed' and calls == []


def test_match_analysis_is_refused_for_stems_the_resegment_child_is_processing(env, monkeypatch):
    """Review 10: a re-analysis would race the child's writes to the same result dir."""
    client, tmp, popen, _ = env
    (tmp / 'videos' / 'b.mp4').write_bytes(b'v')
    (tmp / 'videos' / 'c.mp4').write_bytes(b'v')
    webapp.jobs['resegment_rallies'] = {'status': 'running', 'kind': 'training', 'message': 'm'}
    webapp._relabel_job_stems.clear()
    webapp._relabel_job_stems.update({'b'})
    try:
        r = client.post('/api/analyze', json={'video': 'b.mp4'})
        assert r.status_code == 409 and r.get_json()['reason'] == 'models_updating'
        assert '/' in r.get_json()['error'] and 'b' not in webapp.jobs
        other = client.post('/api/analyze', json={'video': 'c.mp4'})      # not one of its stems
        assert other.status_code == 200
    finally:
        webapp.jobs['resegment_rallies']['status'] = 'cancelled'
        webapp._relabel_job_stems.clear()
    again = client.post('/api/analyze', json={'video': 'b.mp4'})          # the job is over: allowed
    assert again.status_code == 200
    for j in ('b', 'c'):
        webapp.jobs.pop(j, None)


# ---- clip staleness --------------------------------------------------------------------------

def test_stale_clips_are_re_cut_on_request(env, monkeypatch):
    client, tmp, _, _ = env
    out = tmp / 'outputs' / 'a'
    (out / 'rally_segments.json').write_text('{"rallies": []}', encoding='utf-8')
    clips = out / 'clips'
    clips.mkdir()
    (clips / 'old_rally.mp4').write_bytes(b'old')
    (clips / '.stale').write_text('', encoding='utf-8')
    (tmp / 'videos' / 'a.mp4').write_bytes(b'v')

    def fake_run(cmd, *args, **kwargs):
        (clips / 'new_rally.mp4').write_bytes(b'new')
        return type('R', (), {'stdout': '', 'returncode': 0})()

    monkeypatch.setattr(webapp.subprocess, 'run', fake_run)
    served = client.get('/api/output/a/clips/old_rally.mp4')
    assert served.status_code == 404 and served.get_json()['stale'] is True
    r = client.post('/api/clip', json={'video': 'a.mp4', 'mode': 'clips'})
    names = [c['name'] for c in r.get_json()['clips']]
    assert names == ['new_rally.mp4'] and not (clips / 'old_rally.mp4').exists()
    assert not (clips / '.stale').exists()
    assert client.get('/api/output/a/clips/new_rally.mp4').status_code == 200


def test_clips_without_the_marker_are_untouched(env, monkeypatch):
    client, tmp, _, _ = env
    out = tmp / 'outputs' / 'a'
    (out / 'rally_segments.json').write_text('{"rallies": []}', encoding='utf-8')
    (out / 'clips').mkdir()
    (out / 'clips' / 'keep.mp4').write_bytes(b'x')
    (tmp / 'videos' / 'a.mp4').write_bytes(b'v')
    monkeypatch.setattr(webapp.subprocess, 'run', lambda *a, **k: type('R', (), {'stdout': ''})())
    r = client.post('/api/clip', json={'video': 'a.mp4', 'mode': 'clips'})
    assert [c['name'] for c in r.get_json()['clips']] == ['keep.mp4']
    assert client.get('/api/output/a/clips/keep.mp4').status_code == 200


def test_status_uses_the_apps_athletes_path_and_its_mtime_is_in_the_key(env):
    client, tmp, _, state = env
    assert client.get('/api/rally-model').status_code == 200
    assert state['kwargs'] == {'athletes_path': tmp / 'data' / 'athletes.json'}
    client.get('/api/rally-model')
    assert state['calls'] == 1
    (tmp / 'data' / 'athletes.json').write_text('{}', encoding='utf-8')     # the match grouping changed
    client.get('/api/rally-model')
    assert state['calls'] == 2
