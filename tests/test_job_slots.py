"""Concurrency cap and FIFO queue (badminton_analysis/web/job_slots.py)."""
import threading

from badminton_analysis.web import job_slots
from badminton_analysis.web.job_slots import JobSlots, default_max_jobs, resolve_max_jobs


def _starter(log, name):
    return lambda: log.append(name)


def test_starts_up_to_the_cap_then_queues_in_order():
    log = []
    slots = JobSlots(2)
    assert slots.submit('a', _starter(log, 'a')) is True
    assert slots.submit('b', _starter(log, 'b')) is True
    assert slots.submit('c', _starter(log, 'c')) is False
    assert slots.submit('d', _starter(log, 'd')) is False
    assert log == ['a', 'b']
    assert slots.position('c') == 1 and slots.position('d') == 2
    assert slots.position('a') is None

    slots.release('a')
    assert log == ['a', 'b', 'c']
    assert slots.snapshot() == {'max_jobs': 2, 'running': ['b', 'c'], 'queued': ['d']}
    slots.release('b')
    slots.release('c')
    assert log == ['a', 'b', 'c', 'd']
    assert slots.snapshot()['queued'] == []


def test_a_new_job_never_jumps_the_queue():
    log = []
    slots = JobSlots(1)
    slots.submit('a', _starter(log, 'a'))
    slots.submit('b', _starter(log, 'b'))
    slots.release('a')  # b takes the slot
    assert slots.submit('c', _starter(log, 'c')) is False
    assert log == ['a', 'b']


def test_cancel_drops_only_a_queued_job():
    log = []
    slots = JobSlots(1)
    slots.submit('a', _starter(log, 'a'))
    slots.submit('b', _starter(log, 'b'))
    slots.submit('c', _starter(log, 'c'))
    assert slots.cancel('a') is False  # running, not queued
    assert slots.cancel('b') is True
    assert slots.cancel('b') is False
    slots.release('a')
    assert log == ['a', 'c']


def test_a_start_that_raises_frees_its_slot():
    log = []
    slots = JobSlots(1)

    def boom():
        raise OSError('no such file')

    slots.submit('bad', boom)
    assert slots.snapshot()['running'] == []
    assert slots.submit('ok', _starter(log, 'ok')) is True
    assert log == ['ok']


def test_a_failing_queued_start_hands_the_slot_on():
    log = []
    slots = JobSlots(1)
    slots.submit('a', _starter(log, 'a'))
    slots.submit('bad', lambda: (_ for _ in ()).throw(RuntimeError('x')))
    slots.submit('c', _starter(log, 'c'))
    slots.release('a')
    assert log == ['a', 'c']
    assert slots.snapshot() == {'max_jobs': 1, 'running': ['c'], 'queued': []}


def test_queue_change_hook_sees_the_order():
    seen = []
    slots = JobSlots(1, on_queue_change=lambda order: seen.append(list(order)))
    slots.submit('a', lambda: None)
    slots.submit('b', lambda: None)
    slots.submit('c', lambda: None)
    assert seen[-1] == ['b', 'c']
    slots.release('a')
    assert seen[-1] == ['c']
    slots.cancel('c')
    assert seen[-1] == []


def test_never_more_than_the_cap_under_concurrent_submits():
    slots = JobSlots(3)
    lock = threading.Lock()
    live = {'now': 0, 'peak': 0}

    def start():
        with lock:
            live['now'] += 1
            live['peak'] = max(live['peak'], live['now'])

    def worker(i):
        slots.submit(f'j{i}', start)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert live['peak'] == 3
    assert len(slots.snapshot()['queued']) == 37


def test_default_sizing_for_the_4090_host():
    # Docker VM on the RTX 4090 / 7950X3D box: 32 threads, ~31 GB, one GPU.
    assert default_max_jobs(32, 30.9, 1) == 2


def test_default_sizing_takes_the_tightest_limit():
    assert default_max_jobs(8, 64, 1) == 1        # CPU-bound laptop
    assert default_max_jobs(64, 12, 2) == 2       # RAM-bound
    assert default_max_jobs(64, 256, 4) == 8      # GPU-bound workstation
    assert default_max_jobs(32, 64, 0) == 5       # CPU only: cores and RAM decide
    assert default_max_jobs(2, None, 0) == 1      # never below one


def test_env_override_wins_when_valid():
    probe = lambda: (32, 31.0, 1)
    assert resolve_max_jobs({'GB_MAX_JOBS': '4'}, probe) == (4, 'GB_MAX_JOBS')
    n, source = resolve_max_jobs({}, probe)
    assert n == 2 and source.startswith('host')
    for bad in ('0', '-1', 'two', '1.5'):
        assert resolve_max_jobs({'GB_MAX_JOBS': bad}, probe)[0] == 2


def test_probes_do_not_raise():
    assert job_slots._cpu_count() >= 1
    ram = job_slots._ram_gb()
    assert ram is None or ram > 0
    assert job_slots._gpu_count() >= 0
