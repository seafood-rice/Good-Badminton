"""When was a video recorded: the filename if it says so, else a file mtime."""
import datetime
import os
import re

_DATE = re.compile(r"(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])")


def _from_stem(stem):
    for m in _DATE.finditer(str(stem)):
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            continue            # e.g. 20260230: matches the pattern, not a real day
    return None


def _mtime_date(path):
    if path is None:
        return None
    try:
        return datetime.date.fromtimestamp(os.stat(path).st_mtime).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def video_date(stem, video_path=None, out_dir=None):
    """``(iso_date, source)``; source is ``"filename"`` or ``"mtime"``.

    The first real ``YYYYMMDD`` in the stem wins; otherwise the video file's
    mtime date, then the run directory's. With none of those available the date
    is ``None`` (still source ``"mtime"``): a date is never invented.
    """
    named = _from_stem(stem)
    if named:
        return named, "filename"
    return _mtime_date(video_path) or _mtime_date(out_dir), "mtime"
