"""annotate_court must never block on a keypress when non-interactive.

Regression test for the defect that silently consumed 4h43m of the B1
validation run: the auto-detect branch opened a cv2 window and spun in
`while True: cv2.waitKey(1)` with no timeout and no way for a caller to
opt out.
"""
import numpy as np
import pytest

from badminton_analysis.court import mapper


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

    for name in ("namedWindow", "imshow", "waitKey", "destroyWindow"):
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
    image = np.zeros((1080, 1920, 3), np.uint8)
    with pytest.raises(RuntimeError, match="auto court detection failed"):
        mapper.annotate_court(image, interactive=False)
