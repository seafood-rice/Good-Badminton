import numpy as np
import pytest

from badminton_analysis.court.scale import PerspectiveScale
from badminton_analysis.tracking import appearance as ap

QUAD = [[100, 100], [540, 100], [620, 460], [20, 460]]


def _person(cx=320, top=150):
    """Synthetic COCO-17 keypoints of an upright person, full-frame pixels."""
    kp = np.zeros((17, 2))
    kp[5], kp[6] = (cx - 20, top), (cx + 20, top)                 # shoulders
    kp[11], kp[12] = (cx - 15, top + 60), (cx + 15, top + 60)     # hips
    kp[13], kp[14] = (cx - 15, top + 100), (cx + 15, top + 100)   # knees
    kp[15], kp[16] = (cx - 15, top + 140), (cx + 15, top + 140)   # ankles
    kp[9], kp[10] = (cx - 30, top + 40), (cx + 30, top + 40)      # wrists
    return kp


def _frame(torso_bgr, legs_bgr, cx=320, top=150):
    f = np.zeros((480, 640, 3), np.uint8)
    f[top:top + 60, cx - 25:cx + 25] = torso_bgr
    f[top + 60:top + 100, cx - 20:cx + 20] = legs_bgr
    return f


def test_signature_has_normalised_histograms():
    scale = PerspectiveScale.from_quad(QUAD)
    sig = ap.signature(_frame((0, 0, 255), (255, 0, 0)), _person(), scale)
    assert sig["torso"].shape == (64,) and sig["legs"].shape == (64,)
    assert sig["torso"].sum() == pytest.approx(1.0)
    assert sig["height"] > 0


def test_same_kit_is_closer_than_different_kit():
    scale = PerspectiveScale.from_quad(QUAD)
    red = ap.signature(_frame((0, 0, 255), (40, 40, 40)), _person(), scale)
    red2 = ap.signature(_frame((10, 10, 245), (40, 40, 40)), _person(), scale)
    blue = ap.signature(_frame((255, 0, 0), (40, 40, 40)), _person(), scale)
    assert ap.distance(red, red2) < ap.distance(red, blue)


def test_height_separates_same_kit():
    scale = PerspectiveScale.from_quad(QUAD)
    f = _frame((0, 0, 255), (40, 40, 40))
    short = ap.signature(f, _person(), scale)
    tall_kp = _person()
    tall_kp[5][1] -= 40
    tall_kp[6][1] -= 40
    tall = ap.signature(f, tall_kp, scale)
    assert tall["height"] > short["height"]
    assert ap.distance(short, tall) > ap.distance(short, short)


def test_missing_keypoints_give_none():
    scale = PerspectiveScale.from_quad(QUAD)
    assert ap.signature(np.zeros((480, 640, 3), np.uint8), np.zeros((17, 2)), scale) is None


def test_tiny_region_is_skipped():
    scale = PerspectiveScale.from_quad(QUAD)
    kp = _person()
    kp[5], kp[6] = (320, 150), (323, 150)      # shoulders 3 px apart
    kp[11], kp[12] = (320, 153), (323, 153)    # hips 3 px below -> ~16 px torso area
    sig = ap.signature(_frame((0, 0, 255), (40, 40, 40)), kp, scale)
    assert sig is None or sig["torso"] is None


def test_median_signature_and_json_roundtrip():
    scale = PerspectiveScale.from_quad(QUAD)
    s = ap.signature(_frame((0, 0, 255), (40, 40, 40)), _person(), scale)
    m = ap.median_signature([s, s, s])
    assert ap.distance(m, s) == pytest.approx(0.0, abs=1e-9)
    assert ap.distance(ap.from_json(ap.to_json(m)), m) == pytest.approx(0.0, abs=1e-9)
    assert ap.median_signature([]) is None


def test_distance_without_terms_is_one():
    assert ap.distance({"torso": None, "legs": None, "height": None, "embed": None},
                       {"torso": None, "legs": None, "height": None, "embed": None}) == 1.0
