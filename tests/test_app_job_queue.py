"""Concurrent analysis jobs in app.py: the cap, the queue, the duplicate guard, cancel."""
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
    monkeypatch.setattr(webapp, '_job_slots', JobSlots(1, on_queue_change=webapp._queue_changed))
    real_sleep = webapp.time.sleep
    monkeypatch.setattr(webapp.time, 'sleep', lambda s: real_sleep(0.01))
    procs = {}

    def fake_popen(cmd, *args, **kwargs):
        video = cmd[cmd.index('--video-path') + 1]
        proc = _Proc()
        procs[video.replace('\\', '/').rsplit('/', 1)[-1]] = proc
        return proc

    monkeypatch.setattr(webapp.subprocess, 'Popen', fake_popen)
    webapp.app.config['TESTING'] = True
    yield webapp.app.test_client(), procs
    for proc in procs.values():
        proc.finish()


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
    assert webapp.jobs['c']['queue_position'] == 2
    procs['a.mp4'].finish()
    _wait_status('b', ('running',))
    assert webapp.jobs['c']['queue_position'] == 1


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
    assert client.get('/api/posture-analyze-status/posture_b').get_json()['status'] == 'pending'
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
