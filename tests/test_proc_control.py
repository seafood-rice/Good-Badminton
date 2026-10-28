"""Freezing and thawing a real subprocess tree (badminton_analysis/web/proc_control.py)."""
import subprocess
import sys
import time

import psutil
import pytest

from badminton_analysis.web import proc_control

# A parent that starts a child; both count up in files, so the test can see whether each is running.
_CHILD = "import sys, time\nn = 0\nwhile True:\n    n += 1\n    open(sys.argv[1], 'w').write(str(n))\n    time.sleep(0.02)\n"


def _count(path):
    try:
        return int(path.read_text() or 0)
    except (OSError, ValueError):
        return 0


def _grows(path, wait=0.5):
    before = _count(path)
    time.sleep(wait)
    return _count(path) > before


@pytest.fixture
def tree(tmp_path):
    child_py = tmp_path / 'child.py'
    child_py.write_text(_CHILD)
    parent_out, child_out = tmp_path / 'parent.txt', tmp_path / 'child.txt'
    parent_src = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child_py)!r}, {str(child_out)!r}])\n"
        + _CHILD.split('\n', 1)[1].replace('sys.argv[1]', repr(str(parent_out)))
    )
    proc = subprocess.Popen([sys.executable, '-c', parent_src])
    for _ in range(200):
        if _count(parent_out) and _count(child_out):
            break
        time.sleep(0.05)
    yield proc, parent_out, child_out
    try:
        leftovers = [psutil.Process(proc.pid)] + psutil.Process(proc.pid).children(recursive=True)
    except psutil.Error:      # already gone (kill_tree test)
        leftovers = []
    for p in leftovers:
        try:
            p.resume()
            p.kill()
        except psutil.Error:
            pass
    proc.wait(10)


def test_suspend_freezes_parent_and_child_and_resume_continues(tree):
    proc, parent_out, child_out = tree
    assert _grows(parent_out) and _grows(child_out)
    proc_control.suspend_tree(proc.pid)
    time.sleep(0.2)  # let any write in flight land
    assert not _grows(parent_out) and not _grows(child_out)
    proc_control.resume_tree(proc.pid)
    assert _grows(parent_out) and _grows(child_out)


def test_an_exited_process_raises():
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait(10)
    with pytest.raises(psutil.NoSuchProcess):
        proc_control.suspend_tree(proc.pid)
    with pytest.raises(psutil.NoSuchProcess):
        proc_control.resume_tree(proc.pid)


def test_kill_tree_stops_parent_and_child(tree):
    proc, parent_out, child_out = tree
    child_pids = [c.pid for c in psutil.Process(proc.pid).children(recursive=True)]
    assert child_pids
    proc_control.kill_tree(proc.pid)
    proc.wait(10)
    assert not psutil.pid_exists(proc.pid) or psutil.Process(proc.pid).status() == psutil.STATUS_ZOMBIE
    for pid in child_pids:
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert not _grows(parent_out, 0.3) and not _grows(child_out, 0.3)


def test_kill_tree_of_an_exited_process_is_quiet():
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait(10)
    proc_control.kill_tree(proc.pid)


def test_kill_tree_stops_before_touching_anything_when_told_to_abort(tree):
    proc, parent_out, child_out = tree
    asked = []
    assert proc_control.kill_tree(proc.pid, should_abort=lambda: asked.append(1) or True) is False
    assert asked == [1] and proc.poll() is None and psutil.Process(proc.pid).is_running()
    assert proc_control.kill_tree(proc.pid, should_abort=lambda: False) is None
    proc.wait(10)
