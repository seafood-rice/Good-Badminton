"""Concurrent analysis jobs in app.py: the cap, the queue, the duplicate guard, cancel."""
import os
import threading

import pytest

import app as webapp
from badminton_analysis.web.job_slots import JobSlots


class _Proc:
    """A subprocess that runs until the test calls finish()."""

    def __init__(self):
        self.returncode = None
        self._done = threading.Event()
        self.stdout = iter(())

    def poll(self):
        return self.returncode

    def wait(self):
        self._done.wait(10)
        return self.returncode

    def finish(self, code=1):
        # Non-zero keeps the tracker off the ffmpeg re-encode path.
        self.returncode = code
        self._done.set()


@pytest.fixture
def env(tmp_path, monkeypatch):
    videos = tmp_path / 'videos'
    videos.mkdir()
    for name in ('a.mp4', 'b.mp4', 'c.mp4'):
        (videos / name).write_bytes(b'x')
    monkeypatch.setattr(webapp, 'VIDEOS', videos)
    monkeypatch.setattr(webapp, 'OUTPUTS', tmp_path / 'out')
    (tmp_path / 'out').mkdir()
    monkeypatch.setattr(webapp, 'TEMPLATES', tmp_path / 'tpl')
    (tmp_path / 'tpl').mkdir()
    slots = JobSlots(1, on_queue_change=webapp._queue_changed)
    monkeypatch.setattr(webapp, '_job_slots', slots)
    real_sleep = webapp.time.sleep
    monkeypatch.setattr(webapp.time, 'sleep', lambda s: real_sleep(0.01))
    procs = {}
    trackers = []

    def fake_popen(cmd, *args, **kwargs):
        video = cmd[cmd.index('--video-path') + 1]
        proc = _Proc()
        procs[os.path.basename(video)] = proc
        return proc

    real_thread = threading.Thread

    def thread(*args, **kwargs):
        t = real_thread(*args, **kwargs)
        trackers.append(t)
        return t

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(webapp.threading, 'Thread', thread)
    webapp.app.config['TESTING'] = True
    yield webapp.app.test_client(), procs
    # Drain before monkeypatch undoes anything: no queued job may start against
    # the real Popen, and no tracker may outlive this test.
    for job_id in list(slots.snapshot()['queued']):
        slots.cancel(job_id)
    for _ in range(500):
        for proc in list(procs.values()):
            proc.finish()
        snap = slots.snapshot()
        if not snap['running'] and not snap['queued']:
            break
        real_sleep(0.01)
    for t in trackers:
        t.join(5)
    assert not any(t.is_alive() for t in trackers)
    assert slots.snapshot()['running'] == []


def _wait_status(job_id, statuses):
    for _ in range(200):
        job = webapp.jobs.get(job_id)
        if job and job.get('status') in statuses:
            return job
        threading.Event().wait(0.01)
    raise AssertionError(f'{job_id} never reached {statuses}: {webapp.jobs.get(job_id)}')


def test_second_video_queues_until_the_first_ends(env):
    client, procs = env
    r1 = client.post('/api/analyze', json={'video': 'a.mp4'})
    r2 = client.post('/api/analyze', json={'video': 'b.mp4'})
    assert r1.get_json() == {'ok': True, 'job_id': 'a', 'queued': False, 'position': None}
    assert r2.get_json() == {'ok': True, 'job_id': 'b', 'queued': True, 'position': 1}
    assert list(procs) == ['a.mp4']  # b never started

    status = client.get('/api/status/b').get_json()
    assert status['status'] == 'pending'
    assert status['queue_position'] == 1
    assert 'queued, #1 in line' in status['message']

    procs['a.mp4'].finish()
    _wait_status('a', ('error', 'completed'))
    _wait_status('b', ('running',))
    assert list(procs) == ['a.mp4', 'b.mp4']
    assert client.get('/api/status/b').get_json()['queue_position'] is None


def test_queue_positions_move_up(env):
    client, procs = env
    for name in ('a.mp4', 'b.mp4', 'c.mp4'):
        client.post('/api/analyze', json={'video': name})
    assert client.get('/api/status/c').get_json()['queue_position'] == 2
    procs['a.mp4'].finish()
    _wait_status('b', ('running',))
    status = env[0].get('/api/status/c').get_json()
    assert status['queue_position'] == 1
    assert 'queued, #1 in line' in status['message']


def test_same_video_twice_is_rejected_while_in_flight(env):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    r = client.post('/api/analyze', json={'video': 'a.mp4'})
    assert r.status_code == 409
    assert 'already' in r.get_json()['error']
    # Queued counts as in flight too.
    client.post('/api/analyze', json={'video': 'b.mp4'})
    assert client.post('/api/analyze', json={'video': 'b.mp4'}).status_code == 409
    assert list(procs) == ['a.mp4']


def test_a_finished_video_can_run_again(env):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    first = procs['a.mp4']
    first.finish()
    _wait_status('a', ('error', 'completed'))
    assert client.post('/api/analyze', json={'video': 'a.mp4'}).status_code == 200
    assert procs['a.mp4'] is not first


def test_match_and_posture_share_the_cap(env, monkeypatch):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    r = client.post('/api/posture/analyze', json={'video': 'b.mp4', 'stroke_type': 'smash'})
    assert r.get_json()['queued'] is True
    status = client.get('/api/posture-analyze-status/posture_b').get_json()
    assert status['status'] == 'pending'
    assert status['queue_position'] == 1
    procs['a.mp4'].finish()
    _wait_status('posture_b', ('running',))
    assert 'b.mp4' in procs


def test_cancel_a_queued_job(env):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    assert client.post('/api/jobs/a/cancel').status_code == 409  # running
    r = client.post('/api/jobs/b/cancel')
    assert r.status_code == 200
    assert webapp.jobs['b']['status'] == 'cancelled'
    assert client.post('/api/jobs/b/cancel').status_code == 409
    assert client.post('/api/jobs/nope/cancel').status_code == 404
    procs['a.mp4'].finish()
    _wait_status('a', ('error', 'completed'))
    assert 'b.mp4' not in procs
    # A cancelled video may be started again.
    assert client.post('/api/analyze', json={'video': 'b.mp4'}).status_code == 200


def test_slots_endpoint(env):
    client, _ = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    assert client.get('/api/jobs/slots').get_json() == {
        'max_jobs': 1, 'running': ['a'], 'queued': ['b']}


def test_queued_job_is_listed_as_pending(env):
    client, _ = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    rows = {r['id']: r for r in client.get('/api/jobs').get_json()}
    assert rows['b']['status'] == 'pending'
    assert rows['a']['status'] == 'running'


def test_popen_failure_marks_the_job_failed_and_frees_the_slot(env, monkeypatch):
    client, procs = env

    def broken(*a, **k):
        raise OSError('missing interpreter')

    monkeypatch.setattr(webapp.subprocess, 'Popen', broken)
    r = client.post('/api/analyze', json={'video': 'a.mp4'})
    assert r.status_code == 200
    assert webapp.jobs['a']['status'] == 'error'
    assert webapp._job_slots.snapshot()['running'] == []


def test_cancel_leaves_no_stale_queue_message(env):
    client, _ = env
    for name in ('a.mp4', 'b.mp4', 'c.mp4'):
        client.post('/api/analyze', json={'video': name})
    client.post('/api/jobs/b/cancel')
    webapp._queue_changed(['b', 'c'])  # a late notification naming the cancelled job
    b = client.get('/api/status/b').get_json()
    assert b['status'] == 'cancelled'
    assert b['message'] == '' and b['queue_position'] is None


def test_thread_start_failure_kills_the_process_and_frees_the_slot(env, monkeypatch):
    client, procs = env
    killed = []

    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(webapp.threading, 'Thread', _NoThread)
    monkeypatch.setattr(_Proc, 'kill', lambda self: killed.append(self), raising=False)
    client.post('/api/analyze', json={'video': 'a.mp4'})
    assert webapp.jobs['a']['status'] == 'error'
    assert killed == [procs['a.mp4']]
    assert webapp._job_slots.snapshot()['running'] == []


def test_a_late_tracker_failure_leaves_the_rerun_alone(env, monkeypatch):
    client, procs = env
    gate = threading.Event()
    calls = []

    def slow_summary(sd):
        calls.append(sd)
        gate.wait(5)
        raise RuntimeError('summary broke')

    monkeypatch.setattr(webapp, '_rally_summary', slow_summary)
    monkeypatch.setattr(webapp, '_reencode_to_h264', lambda *a, **k: None)
    monkeypatch.setattr(webapp, '_reencode_players_video', lambda *a, **k: None)
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(2, on_queue_change=webapp._queue_changed))
    client.post('/api/analyze', json={'video': 'a.mp4'})
    first = webapp.jobs['a']
    procs['a.mp4'].finish(0)
    _wait_status('a', ('completed',))  # tracker now blocked in _rally_summary
    assert client.post('/api/analyze', json={'video': 'a.mp4'}).status_code == 200
    rerun = webapp.jobs['a']
    assert rerun is not first and rerun['status'] == 'running'
    gate.set()
    for _ in range(200):
        if webapp._job_slots.snapshot()['running'] == ['a']:
            break
        threading.Event().wait(0.01)
    assert rerun['status'] == 'running'
    assert webapp._job_slots.snapshot()['running'] == ['a']  # the re-run kept its slot
    procs['a.mp4'].finish()


@pytest.fixture
def frozen(monkeypatch):
    """Record suspend/resume calls instead of signalling a real process."""
    calls = []
    monkeypatch.setattr(webapp.proc_control, 'suspend_tree', lambda pid: calls.append(('suspend', pid)))
    monkeypatch.setattr(webapp.proc_control, 'resume_tree', lambda pid: calls.append(('resume', pid)))
    monkeypatch.setattr(_Proc, 'pid', 4242, raising=False)
    return calls


def test_pause_frees_the_slot_and_resume_waits_at_the_front(env, frozen):
    client, procs = env
    for name in ('a.mp4', 'b.mp4', 'c.mp4'):
        client.post('/api/analyze', json={'video': name})
    assert client.post('/api/jobs/a/pause').status_code == 200
    assert frozen == [('suspend', 4242)]
    assert webapp.jobs['a']['status'] == 'paused'
    _wait_status('b', ('running',))  # a's slot went to the next queued job
    assert client.get('/api/status/a').get_json()['message'] == '已暂停 / paused'

    r = client.post('/api/jobs/a/resume').get_json()
    assert r == {'ok': True, 'queued': True, 'position': 1}  # ahead of c
    status = client.get('/api/status/a').get_json()
    assert status['status'] == 'resuming' and status['queue_position'] == 1
    assert 'resume, #1 in line' in status['message']

    procs['b.mp4'].finish()
    _wait_status('a', ('running',))
    assert frozen[-1] == ('resume', 4242)
    assert webapp._job_slots.snapshot() == {'max_jobs': 1, 'running': ['a'], 'queued': ['c']}
    procs['a.mp4'].finish()
    _wait_status('c', ('running',))  # the resumed run frees its new slot when it ends


def test_resume_with_a_free_slot_runs_at_once(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/jobs/a/pause')
    assert client.post('/api/jobs/a/resume').get_json() == {'ok': True, 'queued': False, 'position': None}
    assert webapp.jobs['a']['status'] == 'running'
    assert webapp._job_slots.snapshot()['running'] == ['a']


def test_pause_and_resume_refuse_the_wrong_states(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    assert client.post('/api/jobs/nope/pause').status_code == 404
    assert client.post('/api/jobs/nope/resume').status_code == 404
    assert client.post('/api/jobs/b/pause').status_code == 409   # queued, not running
    assert client.post('/api/jobs/a/resume').status_code == 409  # running, not paused
    client.post('/api/jobs/a/pause')
    assert client.post('/api/jobs/a/pause').status_code == 409   # already paused
    _wait_status('b', ('running',))
    client.post('/api/jobs/a/resume')                            # waits for b's slot
    assert client.post('/api/jobs/a/cancel').status_code == 409  # a queued resume is not a queued job
    assert client.post('/api/jobs/a/resume').status_code == 409
    assert client.post('/api/analyze', json={'video': 'a.mp4'}).status_code == 409  # still in flight


def test_no_more_paused_jobs_than_slots(env, frozen, monkeypatch):
    client, procs = env
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(2, on_queue_change=webapp._queue_changed))
    for name in ('a.mp4', 'b.mp4', 'c.mp4'):
        client.post('/api/analyze', json={'video': name})
    assert client.post('/api/jobs/a/pause').status_code == 200
    _wait_status('c', ('running',))
    assert client.post('/api/jobs/b/pause').status_code == 200
    r = client.post('/api/jobs/c/pause')
    assert r.status_code == 409 and 'at most 2' in r.get_json()['error']
    for job_id in ('a', 'b'):
        client.post(f'/api/jobs/{job_id}/resume')
    procs['c.mp4'].finish()
    for p in procs.values():
        p.finish()


def test_pause_of_a_job_that_just_ended_is_refused(env, monkeypatch):
    client, procs = env

    def gone(pid):
        raise webapp.proc_control.psutil.NoSuchProcess(pid)

    monkeypatch.setattr(webapp.proc_control, 'suspend_tree', gone)
    monkeypatch.setattr(_Proc, 'pid', 4242, raising=False)
    client.post('/api/analyze', json={'video': 'a.mp4'})
    assert client.post('/api/jobs/a/pause').status_code == 409
    assert webapp.jobs['a']['status'] == 'running'
    assert webapp._job_slots.snapshot()['running'] == ['a']


def test_a_paused_job_that_dies_gives_its_queued_resume_slot_away(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    client.post('/api/jobs/a/pause')
    _wait_status('b', ('running',))
    client.post('/api/jobs/a/resume')        # queued behind b
    procs['a.mp4'].finish()                  # killed from outside while frozen
    _wait_status('a', ('error',))
    procs['b.mp4'].finish()
    _wait_status('b', ('error', 'completed'))
    for _ in range(200):
        if webapp._job_slots.snapshot() == {'max_jobs': 1, 'running': [], 'queued': []}:
            break
        threading.Event().wait(0.01)
    assert webapp._job_slots.snapshot() == {'max_jobs': 1, 'running': [], 'queued': []}
    assert ('resume', 4242) not in frozen


def test_paused_jobs_are_listed_as_paused(env, frozen):
    client, _ = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/jobs/a/pause')
    rows = {r['id']: r for r in client.get('/api/jobs').get_json()}
    assert rows['a']['status'] == 'paused'
    assert rows['a']['message'] == '已暂停 / paused'


def test_delete_is_refused_while_the_job_is_paused_or_resuming(env, frozen):
    client, procs = env
    out = webapp.OUTPUTS / 'a'
    out.mkdir(exist_ok=True)
    (out / 'keep.txt').write_text('x')
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    client.post('/api/jobs/a/pause')
    assert client.post('/api/delete/a', json={'scope': 'all'}).status_code == 409
    _wait_status('b', ('running',))
    client.post('/api/jobs/a/resume')
    assert webapp.jobs['a']['status'] == 'resuming'
    assert client.post('/api/delete/a', json={'scope': 'all'}).status_code == 409
    assert (out / 'keep.txt').exists() and 'a' in webapp.jobs


def test_a_resume_that_fails_to_thaw_goes_back_to_paused(env, frozen, monkeypatch):
    client, procs = env

    def denied(pid):
        raise webapp.proc_control.psutil.AccessDenied(pid)

    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/jobs/a/pause')
    monkeypatch.setattr(webapp.proc_control, 'resume_tree', denied)
    r = client.post('/api/jobs/a/resume')
    assert r.status_code == 409 and 'could not resume' in r.get_json()['error']
    assert webapp.jobs['a']['status'] == 'paused'
    assert 'could not resume' in webapp.jobs['a']['message']
    assert webapp._job_slots.snapshot()['running'] == []
    monkeypatch.setattr(webapp.proc_control, 'resume_tree', lambda pid: None)
    assert client.post('/api/jobs/a/resume').get_json()['queued'] is False
    assert webapp.jobs['a']['status'] == 'running'


def test_a_dead_resuming_job_leaves_no_queue_entry(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    client.post('/api/jobs/a/pause')
    _wait_status('b', ('running',))
    client.post('/api/jobs/a/resume')
    procs['a.mp4'].finish()                    # killed from outside while frozen
    _wait_status('a', ('error',))
    for _ in range(200):
        if 'a' not in webapp._job_slots.snapshot()['queued']:
            break
        threading.Event().wait(0.01)
    assert webapp._job_slots.snapshot()['queued'] == []
    # A fresh run of the same video is the only one waiting.
    assert client.post('/api/analyze', json={'video': 'a.mp4'}).get_json()['position'] == 1


def test_a_resume_start_after_the_tracker_ended_takes_no_slot(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/analyze', json={'video': 'b.mp4'})
    client.post('/api/jobs/a/pause')
    _wait_status('b', ('running',))
    client.post('/api/jobs/a/resume')
    job = webapp.jobs['a']
    start = job['resume_start']
    procs['a.mp4'].finish()
    _wait_status('a', ('error',))
    for _ in range(200):
        if job.get('ended'):
            break
        threading.Event().wait(0.01)
    # The late start (as if it had been popped just before the tracker's cancel) is refused.
    freed = []
    with pytest.raises(RuntimeError):
        start(lambda: freed.append(1))
    assert job['status'] == 'error'
    assert ('resume', 4242) not in frozen


def test_a_paused_job_killed_from_outside_ends_as_error_and_cannot_resume(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/jobs/a/pause')
    procs['a.mp4'].finish(0)  # even a clean exit: the job never finished its work while paused
    job = webapp.jobs['a']
    for _ in range(300):
        if job.get('ended'):
            break
        threading.Event().wait(0.01)
    assert job['status'] in ('error', 'completed')  # the tracker's verdict, never 'paused'
    assert client.post('/api/jobs/a/resume').status_code == 409
    assert job['status'] != 'resuming'


def test_resume_refuses_a_paused_job_whose_process_is_gone(env, frozen):
    client, procs = env
    client.post('/api/analyze', json={'video': 'a.mp4'})
    client.post('/api/jobs/a/pause')
    job = webapp.jobs['a']
    job['proc'].returncode = -9  # dead, but the tracker has not noticed yet
    r = client.post('/api/jobs/a/resume')
    assert r.status_code == 409 and 'already ended' in r.get_json()['error']
    assert job['status'] == 'paused'
