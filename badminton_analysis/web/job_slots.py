"""Concurrency cap and FIFO queue for analysis jobs.

Every analysis (match or posture) is one ``main*.py`` subprocess that loads its
own models onto the GPU. Starting them all at once makes them fight for the GPU
and RAM, so at most ``max_jobs`` run together. The rest wait in submission
order with status ``pending`` (the Kestrel UI shows that as "Queued").

Sizing, measured on the RTX 4090 / Ryzen 7950X3D host (2026-10-25): one match
job uses about 5.5 CPU cores, 4 GB RAM and 1.5 GB VRAM, and keeps the GPU at
100% while it tracks the shuttle. So VRAM is not the limit; GPU compute is,
then CPU and RAM. A second job overlaps the CPU-heavy stages (decode, render,
ffmpeg) of the first; a third would only queue on the GPU.

``GB_MAX_JOBS`` (an integer >= 1) overrides the computed default.
"""
import os
import shutil
import subprocess
import threading
from collections import deque

CORES_PER_JOB = 6        # measured ~5.5, rounded up
RAM_GB_PER_JOB = 6       # measured ~4 GB, plus headroom for ffmpeg re-encode
JOBS_PER_GPU = 2


def _cpu_count():
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _ram_gb():
    """Physical RAM in GB, or None when it cannot be read."""
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 2**30
    except (AttributeError, ValueError, OSError):
        pass
    try:  # Windows
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]

        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullTotalPhys / 2**30
    except Exception:
        pass
    return None


def _gpu_count():
    """NVIDIA GPUs visible to this process (0 when nvidia-smi is absent or fails)."""
    exe = shutil.which('nvidia-smi')
    if not exe:
        return 0
    try:
        out = subprocess.run([exe, '-L'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 0
    if out.returncode != 0:
        return 0
    return sum(1 for line in out.stdout.splitlines() if line.startswith('GPU '))


def default_max_jobs(cpus, ram_gb, gpus):
    """How many analyses this host runs at once: the tightest of CPU, RAM and GPU."""
    limits = [max(1, int(cpus) // CORES_PER_JOB)]
    if ram_gb:
        limits.append(max(1, int(ram_gb) // RAM_GB_PER_JOB))
    if gpus:
        limits.append(int(gpus) * JOBS_PER_GPU)
    return max(1, min(limits))


def resolve_max_jobs(environ=None, probe=None):
    """``(max_jobs, source)``: ``GB_MAX_JOBS`` when it is a valid integer >= 1, else the host default."""
    environ = os.environ if environ is None else environ
    raw = str(environ.get('GB_MAX_JOBS', '')).strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            value = 0
        if value >= 1:
            return value, 'GB_MAX_JOBS'
        print(f'GB_MAX_JOBS={raw!r} ignored (needs an integer >= 1)')
    cpus, ram_gb, gpus = probe() if probe else (_cpu_count(), _ram_gb(), _gpu_count())
    return default_max_jobs(cpus, ram_gb, gpus), f'host (cpus={cpus}, ram_gb={ram_gb and round(ram_gb)}, gpus={gpus})'


class JobSlots:
    """At most ``max_jobs`` jobs hold a slot; the rest wait in FIFO order.

    ``submit(job_id, start)`` runs ``start()`` now if a slot is free, else queues
    it. A started job must call ``release(job_id)`` exactly once when it ends
    (success or failure); that hands its slot to the next queued job. ``start``
    runs on the caller's thread (the request thread, or the thread that
    released the slot) and must not block for long: it launches a subprocess
    and a tracking thread and returns. If ``start`` raises, its slot is freed.
    """

    def __init__(self, max_jobs, on_queue_change=None):
        self.max_jobs = max(1, int(max_jobs))
        self._lock = threading.Lock()
        self._running = set()
        self._queue = deque()  # (job_id, start)
        # Called (outside the lock) with [job_id, ...] in queue order after every change.
        self._on_queue_change = on_queue_change

    def submit(self, job_id, start):
        """``True`` when started now, ``False`` when queued."""
        with self._lock:
            if len(self._running) < self.max_jobs and not self._queue:
                self._running.add(job_id)
                now = True
            else:
                self._queue.append((job_id, start))
                now = False
        if now:
            self._start(job_id, start)
        else:
            self._notify()
        return now

    def release(self, job_id):
        with self._lock:
            self._running.discard(job_id)
        self._drain()

    def cancel(self, job_id):
        """Drop a queued (not yet started) job. ``True`` when it was queued."""
        with self._lock:
            before = len(self._queue)
            self._queue = deque(item for item in self._queue if item[0] != job_id)
            removed = len(self._queue) != before
        if removed:
            self._notify()
        return removed

    def position(self, job_id):
        """1-based place in the queue, or None when not queued."""
        with self._lock:
            for index, (queued_id, _) in enumerate(self._queue):
                if queued_id == job_id:
                    return index + 1
        return None

    def snapshot(self):
        with self._lock:
            return {'max_jobs': self.max_jobs, 'running': sorted(self._running),
                    'queued': [job_id for job_id, _ in self._queue]}

    def _start(self, job_id, start):
        try:
            start()
        except Exception as exc:
            print(f'job {job_id} failed to start ({type(exc).__name__})')
            self.release(job_id)

    def _drain(self):
        while True:
            with self._lock:
                if len(self._running) >= self.max_jobs or not self._queue:
                    break
                job_id, start = self._queue.popleft()
                self._running.add(job_id)
            self._notify()
            self._start(job_id, start)
        self._notify()

    def _notify(self):
        if self._on_queue_change is None:
            return
        with self._lock:
            order = [job_id for job_id, _ in self._queue]
        try:
            self._on_queue_change(order)
        except Exception as exc:
            print(f'queue change hook failed ({type(exc).__name__})')
