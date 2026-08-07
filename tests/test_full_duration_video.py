# tests/test_full_duration_video.py
import badminton_analysis.system as sysmod

# Load runtime dependencies before running tests
sysmod.load_runtime_dependencies()


class _Writer:
    def __init__(self):
        self.count = 0
    def write(self, frame):
        self.count += 1


def _bare_system():
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.show_display = False
    s.save_images = False
    s.court_view_frames_threshold = 5
    s.non_court_frames_threshold = 5
    return s


def test_non_court_frame_is_written_passthrough():
    s = _bare_system()
    out = _Writer()
    # is_court_view False -> historically returned before out.write. Force non-court.
    s.is_court_view = lambda *a, **k: False
    # Minimal stubs for the pre-branch code in _process_frame:
    s.consecutive_non_court_frames = 0
    s.is_court_view_count = 0
    s.rally_active = False
    s.rally_count = 0
    s._current_rally_start = 0
    s.rally_segments = []
    s.shuttlecock_tracker = type("T", (), {"clear_trajectory": lambda self=None: None})()
    import numpy as np
    frame = np.zeros((10, 10, 3), dtype=np.uint8)
    gray = frame[:, :, 0]
    s._process_frame(frame, gray, [(0,0)]*4, [(0,0),(1,1)], 1, out, 0)
    assert out.count == 1  # non-court frame written (was 0 before the fix)
