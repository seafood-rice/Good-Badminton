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


def _full_frame_hist(frame, poly):
    """The original whole-frame computation, kept here as the reference."""
    import cv2
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(poly.astype(np.int32)), 255)
    if int(np.count_nonzero(mask)) < ap.MIN_REGION_PX:
        return None
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [ap.H_BINS, ap.S_BINS], [0, 180, 0, 256]).flatten()
    return (hist / float(hist.sum())).astype(np.float64)


@pytest.mark.parametrize("poly", [
    [[300, 150], [340, 150], [335, 210], [305, 210]],         # inside
    [[-30, 400], [40, 380], [60, 520], [-10, 530]],           # off the left / bottom edges
    [[600, -20], [660, -10], [650, 40], [610, 30]],           # off the top-right corner
    [[100, 100], [101, 100], [101, 101], [100, 101]],         # below the pixel floor
    [[700, 500], [760, 500], [760, 560], [700, 560]],         # wholly outside
])
def test_region_hist_matches_full_frame_computation(poly):
    rng = np.random.default_rng(1)
    frame = rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)
    poly = np.array(poly, np.float32)
    got, ref = ap._region_hist(frame, poly), _full_frame_hist(frame, poly)
    if ref is None:
        assert got is None
    else:
        assert np.array_equal(got, ref)


def test_region_hist_converts_only_the_crop(monkeypatch):
    seen = []
    real = ap.cv2.cvtColor

    def spy(img, code):
        seen.append(img.shape[:2])
        return real(img, code)

    monkeypatch.setattr(ap.cv2, "cvtColor", spy)
    frame = np.zeros((480, 640, 3), np.uint8)
    ap._region_hist(frame, np.array([[300, 150], [340, 150], [335, 210], [305, 210]], np.float32))
    assert seen and all(h <= 61 and w <= 41 for h, w in seen)


def test_distance_without_terms_is_one():
    assert ap.distance({"torso": None, "legs": None, "height": None, "embed": None},
                       {"torso": None, "legs": None, "height": None, "embed": None}) == 1.0


def _hist(i, n=64):
    h = np.full(n, 0.001)
    h[i] = 1.0
    return h / h.sum()


def _sig(torso=None, legs=None, height=None, embed=None):
    return {"torso": torso, "legs": legs, "height": height, "embed": embed}


def test_embedding_term_cosine():
    e = np.array([1.0, 0.0, 0.0])
    assert ap.distance(_sig(embed=e), _sig(embed=e)) < 1e-6
    assert abs(ap.distance(_sig(embed=e), _sig(embed=np.array([0.0, 1.0, 0.0]))) - 0.5) < 1e-6
    assert abs(ap.distance(_sig(embed=e), _sig(embed=-e)) - 1.0) < 1e-6


def test_embedding_reweights_with_the_other_terms():
    t, e = _hist(3), np.array([1.0, 0.0])
    d = ap.distance(_sig(torso=t, embed=e), _sig(torso=t, embed=-e))
    assert abs(d - ap.W_EMBED / (ap.W_TORSO + ap.W_EMBED)) < 1e-6


def test_only_shared_terms_count():
    a = _sig(torso=_hist(3), legs=_hist(5), height=1.8, embed=np.array([1.0, 0.0]))
    b = _sig(torso=_hist(9))                         # torso only: legs/height/embed ignored
    assert abs(ap.distance(a, b) - ap.chi2(a["torso"], b["torso"])) < 1e-9
    c = _sig(legs=_hist(5), height=1.5)              # legs identical, height 0.3 m apart
    expect = (ap.W_LEGS * 0.0 + ap.W_HEIGHT * min(1.0, 0.3 / ap.HEIGHT_SCALE_M)) / (ap.W_LEGS + ap.W_HEIGHT)
    assert abs(ap.distance(a, c) - expect) < 1e-9


def test_median_signature_keeps_embeddings_and_skips_missing_terms():
    s = ap.median_signature([_sig(torso=_hist(1), height=1.7, embed=np.array([1.0, 0.0])),
                             _sig(torso=_hist(1), embed=np.array([3.0, 0.0])),
                             _sig(legs=_hist(2))])
    assert np.allclose(s["embed"], [2.0, 0.0])
    assert s["height"] == 1.7
    assert np.allclose(s["torso"], _hist(1)) and np.allclose(s["legs"], _hist(2))
