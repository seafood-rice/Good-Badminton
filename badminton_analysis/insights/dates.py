"""When was a video recorded: the filename if it says so, else a file mtime."""
import datetime
import os
import re

_DATE = re.compile(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")
METADATA = "metadata.json"


def _from_stem(stem):
    for m in _DATE.finditer(str(stem)):
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue            # e.g. 20260230: matches the pattern, not a real day
    return None


def _mtime(path):
    if path is None:
        return None
    try:
        return os.stat(path).st_mtime
    except (OSError, ValueError):
        return None


def _iso(timestamp):
    if timestamp is None:
        return None
    try:
        return datetime.date.fromtimestamp(timestamp).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _run_input_mtime(out_dir, run_inputs):
    """The mtime of a stable run input: ``metadata.json``, else the oldest of ``run_inputs``.

    Never the directory itself (every sidecar write bumps it) and never a file
    outside ``run_inputs`` (``insights.json``, temp files).
    """
    if out_dir is None:
        return None
    names = tuple(run_inputs or ())
    if METADATA in names:
        meta = _mtime(os.path.join(out_dir, METADATA))
        if meta is not None:
            return meta
    found = [m for m in (_mtime(os.path.join(out_dir, n)) for n in names) if m is not None]
    return min(found) if found else None


def video_date(stem, video_path=None, out_dir=None, run_inputs=()):
    """``(iso_date, source)``; source is ``"filename"`` or ``"mtime"``.

    The first real ``YYYYMMDD`` in the stem wins; otherwise the video file's
    mtime date, then that of a stable run input in ``out_dir`` (``metadata.json``,
    else the oldest existing file named in ``run_inputs``). The run directory's
    own mtime is never used: writing the sidecar into it moves it to "today".
    With none of those available the date is ``None`` (still source ``"mtime"``):
    a date is never invented.
    """
    named = _from_stem(stem)
    if named:
        return named, "filename"
    return (_iso(_mtime(video_path)) or _iso(_run_input_mtime(out_dir, run_inputs))), "mtime"
