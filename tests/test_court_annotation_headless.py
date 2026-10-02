"""annotate_court must never block on a keypress when non-interactive.

Regression test for the defect that silently consumed 4h43m of the B1
validation run: the auto-detect branch opened a cv2 window and spun in
`while True: cv2.waitKey(1)` with no timeout and no way for a caller to
opt out.
"""
import numpy as np
import pytest

from badminton_analysis.court import mapper

# Every cv2 GUI entry point annotate_court could reach. Stubbed to raise so a
# regression FAILS instead of blocking on a window (or a keypress) forever.
_GUI_CALLS = ("namedWindow", "imshow", "waitKey", "destroyWindow", "setMouseCallback")


def test_non_interactive_accepts_auto_detection_without_gui(monkeypatch):
    corners = [(10, 20), (90, 20), (95, 70), (5, 70)]
    monkeypatch.setattr(mapper, "auto_detect_court_corners",
                        lambda img: (corners, None, {}))
    monkeypatch.setattr(mapper, "compute_expanded_roi",
                        lambda c, shape: [(0, 0), (99, 99)])
    monkeypatch.setattr(mapper, "render_auto_court_preview",
                        lambda *a, **k: np.zeros((720, 1080, 3), np.uint8))

    def _explode(*a, **k):
        raise AssertionError("non-interactive annotate_court touched the GUI")

    for name in _GUI_CALLS:
        monkeypatch.setattr(mapper.cv2, name, _explode)

    image = np.zeros((1080, 1920, 3), np.uint8)
    got_corners, got_roi, mid = mapper.annotate_court(image, interactive=False)

    assert len(got_corners) == 4
    assert len(got_roi) == 2
    assert isinstance(mid, int)


def test_non_interactive_raises_when_auto_detection_fails(monkeypatch):
    """No corners and no human to ask: fail loudly rather than hang or guess."""
    monkeypatch.setattr(mapper, "auto_detect_court_corners",
                        lambda img: (None, None, {}))

    def _explode(*a, **k):
        raise AssertionError("non-interactive annotate_court touched the GUI")

    for name in _GUI_CALLS:
        monkeypatch.setattr(mapper.cv2, name, _explode)

    image = np.zeros((1080, 1920, 3), np.uint8)
    with pytest.raises(RuntimeError, match="auto court detection failed"):
        mapper.annotate_court(image, interactive=False)


def test_the_refusal_message_points_to_the_web_ui_annotation_step(monkeypatch):
    monkeypatch.setattr(mapper, "auto_detect_court_corners",
                        lambda img: (None, None, {}))
    image = np.zeros((1080, 1920, 3), np.uint8)
    with pytest.raises(RuntimeError, match="web UI"):
        mapper.annotate_court(image, interactive=False)


# --- M2: the call site that prevents the web-run hang ---------------------------


@pytest.mark.parametrize("show_display, expected", [(False, False), (True, True)])
def test_setup_court_annotation_passes_interactive_from_show_display(
        tmp_path, monkeypatch, show_display, expected):
    """A web run (show_display False) with no court_annotations.txt must call
    annotate_court non-interactively, or it blocks on a window nobody can see."""
    import badminton_analysis.system as sysmod

    seen = {}

    def _stub(template_color, auto_preview_path=None, interactive=True):
        seen["interactive"] = interactive
        seen["preview"] = auto_preview_path
        return [(1, 2), (3, 4), (5, 6), (7, 8)], [(0, 0), (9, 9)], 5

    monkeypatch.setattr(sysmod, "annotate_court", _stub, raising=False)
    s = object.__new__(sysmod.BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.show_display = show_display
    assert not (tmp_path / "court_annotations.txt").exists()

    corners, roi, mid = s._setup_court_annotation(np.zeros((10, 10, 3), np.uint8))

    assert seen["interactive"] is expected
    assert seen["preview"] == str(tmp_path / "auto_court_preview.png")
    assert len(corners) == 4 and (tmp_path / "court_annotations.txt").exists()
