"""Per-video insights computed from the files in ``outputs/<stem>/``.

Pure functions over loaded files: no Flask, no cv2. Every input file is optional
and any may be malformed; an insight that cannot be computed is reported as
``unavailable`` with a reason code (see ``envelope``), never raised.
"""
