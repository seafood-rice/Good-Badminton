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
