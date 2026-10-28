"""Freeze and thaw an analysis subprocess with everything it spawned.

A paused analysis is suspended in place (SIGSTOP on Linux, NtSuspendProcess on
Windows, both through psutil), so it resumes from the exact frame it stopped
at. It keeps its RAM and GPU memory while paused; only its CPU and GPU time
are given back.
"""
import psutil


def _children(parent):
    try:
        return parent.children(recursive=True)
    except psutil.Error:
        return []


def suspend_tree(pid):
    """Suspend *pid*, then its descendants (the parent first, so it spawns no new ones).

    Raises ``psutil.NoSuchProcess`` when *pid* has already exited.
    """
    parent = psutil.Process(pid)
    parent.suspend()
    for child in _children(parent):
        try:
            child.suspend()
        except psutil.Error:
            pass  # exited in the meantime


def resume_tree(pid):
    """Resume the descendants of *pid*, then *pid* itself.

    Raises ``psutil.NoSuchProcess`` when *pid* has already exited.
    """
    parent = psutil.Process(pid)
    for child in _children(parent):
        try:
            child.resume()
        except psutil.Error:
            pass
    parent.resume()


def kill_tree(pid, timeout=5.0, should_abort=None):
    """Terminate *pid* and every descendant; kill whatever ignores it for *timeout* seconds.

    Quiet when *pid* has already exited (the job ended on its own first). ``should_abort()`` is
    asked right before each terminate; when it says True nothing further is touched and the
    result is False (None otherwise).
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = _children(parent) + [parent]   # collect first: a dead parent hides its children
    for p in procs:
        if should_abort is not None and should_abort():
            return False
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    if alive:
        psutil.wait_procs(alive, timeout=timeout)
