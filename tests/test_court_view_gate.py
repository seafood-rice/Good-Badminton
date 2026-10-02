"""C0/C1: the court-view gate calibrates itself per video.

Today's gate uses one global 0.75 cutoff on a similarity score whose
absolute scale is video-dependent, which admitted 0.66% of frames on the
Axelsen match (design spec §0.3). These tests pin the replacement.

This file must pass when run alone: it never relies on the runtime globals
(cv2, np, write_json) that ``load_runtime_dependencies()`` binds in
``badminton_analysis.system``.
"""
import inspect
import json

import numpy as np
import pytest

from badminton_analysis import system


def test_cut_is_median_minus_k_robust_sd():
    # 100 court frames near 0.80 plus 5 clear outliers near 0.10.
    scores = [0.80] * 100 + [0.10] * 5
    out = system.courtview_cut_from_scores(scores, k=4.0)
    # MAD of this set is 0.0 -> the robust SD collapses, so the cut must fall
    # back to the median rather than emitting median - 0 and admitting nothing
    # below it.
    assert out["median"] == 0.80
    assert out["cut"] == 0.80
    assert out["samples"] == 105
    assert out["method"] == "median-4mad"
    assert out["calibration"] == "median-4mad"
    assert out["k"] == 4.0


def test_cut_separates_play_from_non_play():
    rng = np.random.default_rng(11)
    play = list(0.80 + 0.02 * rng.standard_normal(400))
    non_play = list(0.30 + 0.02 * rng.standard_normal(40))
    out = system.courtview_cut_from_scores(play + non_play, k=4.0)
    assert min(play) > out["cut"] > max(non_play)


def test_template_mismatch_passes_everything():
    """A template that does not match this video must lose the gate, not the run."""
    out = system.courtview_cut_from_scores([0.05, 0.07, 0.06, 0.04], k=4.0)
    assert out["cut"] == float("-inf")
    assert out["method"] == "median-4mad"
    assert out["calibration"] == "template_mismatch_pass_all"


def test_no_samples_falls_back_to_the_shipped_constant():
    out = system.courtview_cut_from_scores([], k=4.0)
    assert out["cut"] == system.COURT_VIEW_FALLBACK_CUT
    assert out["method"] == "median-4mad"   # the method that was attempted
    assert out["calibration"] == "fallback_constant"
    assert out["samples"] == 0


def test_non_finite_scores_are_dropped_before_the_cut():
    nan, inf = float("nan"), float("inf")
    scores = [0.8, nan, 0.82, inf, 0.78, -inf, 0.81, 0.79]
    out = system.courtview_cut_from_scores(scores, k=4.0)
    # Only the 5 finite scores inform the cut; a NaN must not turn the median
    # into NaN and let the clamp silently produce 0.95.
    assert out["samples"] == 5
    assert out["median"] == 0.80
    assert out["calibration"] == "median-4mad"
    assert 0.0 < out["cut"] < 0.80


def test_all_non_finite_scores_take_the_fallback_path():
    out = system.courtview_cut_from_scores([float("nan"), float("inf")], k=4.0)
    assert out["samples"] == 0
    assert out["cut"] == system.COURT_VIEW_FALLBACK_CUT
    assert out["calibration"] == "fallback_constant"


def test_cut_is_clamped_to_a_usable_range():
    out = system.courtview_cut_from_scores([0.99] * 50 + [0.98] * 50, k=4.0)
    assert 0.0 <= out["cut"] <= 0.95


def _fake_system():
    """A bare instance with only the attributes the gate touches."""
    cls = system.BadmintonAnalysisSystem
    s = cls.__new__(cls)
    s.court_view_cut = 0.5
    s._court_view_cached = None
    s._court_view_last_frame = -10
    s.court_view_threshold_override = None
    s.court_view_calibration = None
    s.video_path = "unused.mp4"
    return s


def test_downscaled_scoring_separates_a_match_from_a_mismatch():
    rng = np.random.default_rng(3)
    template = rng.integers(0, 255, (720, 1280), dtype=np.uint8)
    mismatch = rng.integers(0, 255, (720, 1280), dtype=np.uint8)

    s = _fake_system()
    small = system.downscale_for_gate(template)
    assert s._court_view_score(template.copy(), small) > 0.9
    assert s._court_view_score(mismatch, small) < 0.5


def test_downscale_preserves_aspect_ratio_and_never_upscales():
    tall = np.zeros((2160, 3840), np.uint8)
    out = system.downscale_for_gate(tall, width=480)
    assert out.shape == (270, 480)
    small = np.zeros((90, 160), np.uint8)
    assert system.downscale_for_gate(small, width=480).shape == (90, 160)


# --- C0 wiring: manual pin, never-fatal fallback, cheap decoding, metadata ---


def _template():
    rng = np.random.default_rng(5)
    return rng.integers(0, 255, (720, 1280), dtype=np.uint8)


def _boom(*_args, **_kwargs):
    raise AssertionError("calibration must not run")


def test_constructor_accepts_court_view_threshold_defaulting_to_none():
    param = inspect.signature(
        system.BadmintonAnalysisSystem.__init__).parameters["court_view_threshold"]
    assert param.default is None


def test_manual_threshold_skips_calibration_and_is_recorded(monkeypatch):
    s = _fake_system()
    s.court_view_threshold_override = 0.62
    monkeypatch.setattr(s, "_calibrate_court_view", _boom)

    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 30.0)

    assert s.court_view_cut == 0.62
    record = s._court_view_metadata()
    assert record["cut"] == 0.62
    assert record["method"] == "manual"
    assert record["calibration"] == "manual"
    assert record["samples"] == 0
    assert record["median"] is None and record["mad"] is None


def test_calibration_failure_is_never_fatal_and_falls_back(monkeypatch, capsys):
    s = _fake_system()

    def explode(*_args, **_kwargs):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(s, "_calibrate_court_view", explode)

    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 30.0)

    assert s.court_view_cut == system.COURT_VIEW_FALLBACK_CUT
    record = s._court_view_metadata()
    assert record["method"] == "median-4mad"
    assert record["calibration"] == "fallback_constant"
    assert record["cut"] == system.COURT_VIEW_FALLBACK_CUT
    assert "decoder exploded" in capsys.readouterr().out


def test_prepare_runs_calibration_with_a_downscaled_template(monkeypatch):
    s = _fake_system()
    seen = {}

    def fake_calibrate(video_path, template_small, fps):
        seen.update(video_path=video_path, shape=template_small.shape, fps=fps)
        s.court_view_calibration = system.courtview_cut_from_scores([0.8] * 10)
        s.court_view_cut = s.court_view_calibration["cut"]

    monkeypatch.setattr(s, "_calibrate_court_view", fake_calibrate)
    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 25.0)

    assert seen == {"video_path": "unused.mp4", "shape": (270, 480), "fps": 25.0}
    record = s._court_view_metadata()
    assert record["method"] == "median-4mad"
    assert record["calibration"] == "median-4mad"


@pytest.mark.parametrize("bad_pin", [float("nan"), float("inf"), -0.1, 1.5, "abc", True, [0.6]])
def test_unusable_pin_warns_and_calibrates_instead_of_failing(monkeypatch, capsys, bad_pin):
    s = _fake_system()
    s.court_view_threshold_override = bad_pin
    called = []

    def fake_calibrate(video_path, template_small, fps):
        called.append(True)
        s.court_view_calibration = system.courtview_cut_from_scores([0.8] * 10)
        s.court_view_cut = s.court_view_calibration["cut"]

    monkeypatch.setattr(s, "_calibrate_court_view", fake_calibrate)
    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 30.0)

    assert called == [True]
    assert s._court_view_metadata()["method"] == "median-4mad"
    assert "not a finite number in [0, 1]" in capsys.readouterr().out


@pytest.mark.parametrize("pin", [0.0, 1.0, 0.62, 1])
def test_boundary_pins_are_usable(monkeypatch, pin):
    s = _fake_system()
    s.court_view_threshold_override = pin
    monkeypatch.setattr(s, "_calibrate_court_view", _boom)
    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 30.0)
    assert s.court_view_cut == float(pin)
    assert s._court_view_metadata()["calibration"] == "manual"


class _FakeCapture:
    """Counts grab/retrieve/read calls over a fixed-length synthetic video."""

    instances = []

    def __init__(self, _path, frames=1000):
        self.frames = frames
        self.pos = 0
        self.grabs = self.retrieves = self.reads = 0
        self.released = False
        self.frame = np.full((720, 1280, 3), 127, np.uint8)
        _FakeCapture.instances.append(self)

    def get(self, prop):
        import cv2
        return self.frames if prop == cv2.CAP_PROP_FRAME_COUNT else 0

    def grab(self):
        self.grabs += 1
        if self.pos >= self.frames:
            return False
        self.pos += 1
        return True

    def retrieve(self):
        self.retrieves += 1
        return True, self.frame

    def read(self):
        self.reads += 1
        return False, None

    def release(self):
        self.released = True


def test_calibration_only_decodes_the_sampled_frames(monkeypatch):
    import cv2

    _FakeCapture.instances.clear()
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    s = _fake_system()
    template_small = system.downscale_for_gate(_template())

    s._calibrate_court_view("video.mp4", template_small, 30.0)

    cap = _FakeCapture.instances[0]
    # stride = round(0.5 s * 30 fps) = 15 -> frames 0, 15, ... 990
    assert cap.retrieves == 67
    assert cap.grabs >= 1000
    assert cap.reads == 0
    assert cap.released
    assert s.court_view_calibration["samples"] == 67
    assert s.court_view_cut == s.court_view_calibration["cut"]


def test_calibration_covers_the_whole_video_when_capped(monkeypatch):
    import cv2

    class Long(_FakeCapture):
        def __init__(self, path):
            super().__init__(path, frames=10 * system.COURT_VIEW_CALIBRATION_MAX_SAMPLES + 7)

    _FakeCapture.instances.clear()
    monkeypatch.setattr(cv2, "VideoCapture", Long)
    s = _fake_system()

    s._calibrate_court_view("video.mp4", system.downscale_for_gate(_template()), 30.0)

    cap = _FakeCapture.instances[0]
    # The stride must be sized so the sample budget spans the whole video
    # rather than being exhausted in its first part.
    assert cap.pos >= cap.frames - 1
    assert s.court_view_calibration["samples"] <= system.COURT_VIEW_CALIBRATION_MAX_SAMPLES


def test_calibration_with_an_unreadable_video_falls_back(monkeypatch):
    import cv2

    class Broken(_FakeCapture):
        def __init__(self, path):
            super().__init__(path)

        def grab(self):
            raise RuntimeError("codec missing")

    _FakeCapture.instances.clear()
    monkeypatch.setattr(cv2, "VideoCapture", Broken)
    s = _fake_system()

    s._calibrate_court_view("video.mp4", system.downscale_for_gate(_template()), 30.0)

    assert s.court_view_cut == system.COURT_VIEW_FALLBACK_CUT
    assert s.court_view_calibration["method"] == "median-4mad"
    assert s.court_view_calibration["calibration"] == "fallback_constant"
    assert _FakeCapture.instances[0].released


def test_non_finite_cut_is_recorded_as_null(monkeypatch, tmp_path):
    from badminton_analysis.data.writer import write_json

    # write_json is a runtime global bound by load_runtime_dependencies();
    # wire it directly so this file passes when run alone.
    monkeypatch.setattr(system, "write_json", write_json, raising=False)
    monkeypatch.setattr(system, "SCHEMA_VERSION", "1.0", raising=False)

    s = _fake_system()
    s.court_view_calibration = system.courtview_cut_from_scores([0.05, 0.07, 0.06])
    s.court_view_cut = s.court_view_calibration["cut"]
    assert s.court_view_cut == float("-inf")

    s.video_path = "v.mp4"
    s.video_name = "v"
    s.frame_width = 1280
    s.frame_height = 720
    s.output_video_path = "out.mp4"
    s.detections_path = "d.jsonl"
    s.ball_model_path = "ball.pt"
    s.metadata_path = str(tmp_path / "metadata.json")

    s._write_metadata(30.0, 300, 10.0, "t.png", [[0, 0]] * 4, [[0, 0], [1, 1]], 5)

    text = (tmp_path / "metadata.json").read_text(encoding="utf-8")
    assert "Infinity" not in text and "NaN" not in text
    court_view = json.loads(text)["court"]["court_view"]
    assert court_view["cut"] is None
    assert court_view["method"] == "median-4mad"
    assert court_view["calibration"] == "template_mismatch_pass_all"
    assert court_view["samples"] == 3
    assert court_view["k"] == 4.0
    assert court_view["mad"] is None
    assert court_view["median"] == 0.06


# --- C1: the live gate uses the per-video cut, downscaled, every frame ---


def _noisy(template, sigma, seed):
    rng = np.random.default_rng(seed)
    noisy = template.astype(np.float32) + rng.normal(0, sigma, template.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _blocky(seed):
    rng = np.random.default_rng(seed)
    return np.kron(rng.integers(0, 255, (18, 32), dtype=np.uint8),
                   np.ones((40, 40), np.uint8))


def test_prepare_hands_the_once_downscaled_template_to_calibration(monkeypatch):
    s = _fake_system()
    small = system.downscale_for_gate(_template())
    seen = {}

    def fake_calibrate(video_path, template_small, fps):
        seen["template"] = template_small
        s.court_view_calibration = system.courtview_cut_from_scores([0.8] * 10)
        s.court_view_cut = s.court_view_calibration["cut"]

    def no_second_downscale(*_a, **_k):
        raise AssertionError("the template must be downscaled exactly once")

    monkeypatch.setattr(s, "_calibrate_court_view", fake_calibrate)
    monkeypatch.setattr(system, "downscale_for_gate", no_second_downscale)
    s._prepare_court_view_cut(small, 30.0)
    assert seen["template"] is small


def test_load_template_returns_the_downscaled_template_too(tmp_path):
    import cv2

    rng = np.random.default_rng(8)
    path = str(tmp_path / "court.png")
    cv2.imwrite(path, rng.integers(0, 255, (300, 500, 3), dtype=np.uint8))

    class Cap:
        def get(self, prop):
            return {cv2.CAP_PROP_FRAME_WIDTH: 1280,
                    cv2.CAP_PROP_FRAME_HEIGHT: 720}[prop]

    gray, color, small = _fake_system()._load_template(path, Cap())
    assert gray.shape == (720, 1280) and color.shape == (720, 1280, 3)
    assert small.shape == (270, 480)
    assert np.array_equal(small, system.downscale_for_gate(gray))


def test_unpinned_gate_scores_the_downscale_on_every_frame(monkeypatch):
    s = _fake_system()
    s.court_view_pinned = False
    template = _template()
    small = system.downscale_for_gate(template)
    s.court_view_template_small = small
    scored = []
    real = s._court_view_score

    def counting(gray, tmpl):
        scored.append(gray.shape)
        assert tmpl is small
        return real(gray, tmpl)

    monkeypatch.setattr(s, "_court_view_score", counting)
    monkeypatch.setattr(s, "is_court_view", _boom)   # the full-res path is not used
    for f in range(1, 8):
        assert s._court_view_for_frame(template.copy(), template, f) is True
    assert len(scored) == 7


def test_unpinned_gate_downscales_the_template_once_when_not_preloaded(monkeypatch):
    s = _fake_system()
    s.court_view_pinned = False
    template = _template()
    real = system.downscale_for_gate
    template_calls = []

    def counting(gray, *a, **k):
        if gray is template:
            template_calls.append(1)
        return real(gray, *a, **k)

    monkeypatch.setattr(system, "downscale_for_gate", counting)
    for f in range(1, 6):
        s._court_view_for_frame(template.copy(), template, f)
    assert len(template_calls) == 1


def test_unpinned_gate_honours_the_calibrated_cut():
    """A frame scoring between two cuts flips with the cut; nothing is 0.75."""
    template = _template()
    frame = _noisy(template, 90, 21)
    s = _fake_system()
    s.court_view_pinned = False
    s.court_view_template_small = system.downscale_for_gate(template)
    score = s._court_view_score(frame, s.court_view_template_small)
    assert 0.05 < score < 0.74     # strictly between the cuts below, and below 0.75

    s.court_view_cut = score - 0.02
    assert s._court_view_for_frame(frame, template, 1) is True
    s.court_view_cut = score + 0.02
    assert s._court_view_for_frame(frame, template, 2) is False
    s.court_view_cut = 0.75        # the shipped constant is just another cut
    assert s._court_view_for_frame(frame, template, 3) is False


def test_pass_all_cut_admits_every_frame_through_the_live_gate():
    """A template that is not of this video loses the gate, not the run."""
    rng = np.random.default_rng(6)
    template = _template()
    s = _fake_system()
    s.court_view_pinned = False
    s.court_view_cut = float("-inf")
    s.court_view_template_small = system.downscale_for_gate(template)
    for f in range(1, 6):
        noise = rng.integers(0, 255, (720, 1280), dtype=np.uint8)
        assert s._court_view_for_frame(noise, template, f) is True
    # ... and a constant frame (NaN score) is admitted too, not an error.
    assert s._court_view_for_frame(np.full((720, 1280), 9, np.uint8), template, 9) is True


def test_a_constant_frame_is_not_court_view_under_a_finite_cut():
    template = _template()
    s = _fake_system()
    s.court_view_pinned = False
    s.court_view_template_small = system.downscale_for_gate(template)
    assert s._court_view_for_frame(np.full((720, 1280), 9, np.uint8), template, 1) is False


def test_manual_pin_marks_the_gate_pinned_and_calibration_does_not(monkeypatch):
    small = system.downscale_for_gate(_template())

    s = _fake_system()
    s.court_view_threshold_override = 0.62
    monkeypatch.setattr(s, "_calibrate_court_view", _boom)
    s._prepare_court_view_cut(small, 30.0)
    assert s.court_view_pinned is True

    def calibrating(system_under_test):
        def fake_calibrate(video_path, template_small, fps):
            system_under_test.court_view_calibration = \
                system.courtview_cut_from_scores([0.8] * 10)
            system_under_test.court_view_cut = \
                system_under_test.court_view_calibration["cut"]
        return fake_calibrate

    s = _fake_system()
    monkeypatch.setattr(s, "_calibrate_court_view", calibrating(s))
    s._prepare_court_view_cut(small, 30.0)
    assert s.court_view_pinned is False

    s = _fake_system()    # unusable pin -> calibrates -> not pinned
    s.court_view_threshold_override = float("nan")
    monkeypatch.setattr(s, "_calibrate_court_view", calibrating(s))
    s._prepare_court_view_cut(small, 30.0)
    assert s.court_view_pinned is False


def test_pinned_gate_keeps_todays_full_resolution_interval_3_path(monkeypatch):
    template = _template()
    s = _fake_system()
    s.court_view_pinned = True
    s.court_view_cut = 0.62
    seen = []

    def full_res(frame, tmpl, threshold=None):
        seen.append((frame.shape, tmpl.shape, threshold))
        return True

    monkeypatch.setattr(s, "is_court_view", full_res)
    monkeypatch.setattr(s, "_court_view_score", _boom)   # no downscaled scoring
    assert system.COURT_VIEW_CHECK_INTERVAL == 3
    for f in range(1, 8):
        assert s._court_view_for_frame(template, template, f) is True
    # frames 1, 4, 7 evaluated; the rest hold the cache. Full resolution both sides.
    assert len(seen) == 3
    assert all(frame == tmpl == (720, 1280) for frame, tmpl, _ in seen)


def test_pinned_gate_compares_against_the_pinned_value_at_full_resolution():
    import cv2

    template = _template()
    frame = _noisy(template, 90, 21)
    score = float(np.max(cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)))

    s = _fake_system()
    s.court_view_pinned = True
    s.court_view_cut = score - 0.02
    assert s._court_view_for_frame(frame, template, 1) is True

    s = _fake_system()
    s.court_view_pinned = True
    s.court_view_cut = score + 0.02
    assert s._court_view_for_frame(frame, template, 1) is False


def test_is_court_view_uses_the_cut_unless_a_threshold_is_given():
    template = _template()
    s = _fake_system()
    s.court_view_cut = 0.99
    assert s.is_court_view(template.copy(), template) is True
    s.court_view_cut = 1.01
    assert s.is_court_view(template.copy(), template) is False
    s.court_view_cut = 0.5
    assert s.is_court_view(template.copy(), template, threshold=1.01) is False
    assert s.is_court_view(template.copy(), template, threshold=0.99) is True
    s.court_view_cut = float("-inf")
    assert s.is_court_view(_noisy(template, 200, 1), template) is True


@pytest.mark.parametrize("make_template", [
    lambda: np.random.default_rng(31).integers(0, 255, (720, 1280), dtype=np.uint8),
    lambda: _blocky(32),
])
def test_downscale_does_not_change_decisions_away_from_the_boundary(make_template):
    """Spec section 8: 480px scoring agrees with full resolution off the cut."""
    template = make_template()
    other = np.random.default_rng(33).integers(0, 255, template.shape, dtype=np.uint8)
    frames = {
        "same": (template.copy(), True),
        "slightly_noisy": (_noisy(template, 12, 34), True),
        "unrelated": (other, False),
        "constant": (np.full(template.shape, 128, np.uint8), False),
    }
    cut = 0.5
    s = _fake_system()
    s.court_view_cut = cut
    s.court_view_pinned = False
    s.court_view_template_small = system.downscale_for_gate(template)
    for name, (frame, expected) in frames.items():
        full = s.is_court_view(frame, template, threshold=cut)
        small = s._court_view_for_frame(frame, template, 1)
        assert full is expected, name
        assert small is expected, name


# --- Gate fix (2026-10): shift-tolerant score, and never stricter than 0.75 ---


def _canvas(seed, width=1288):
    """A blocky, texture-rich canvas 8 px wider than a 1280 frame.

    8 px at 1280 wide is exactly 3 px at the gate's 480 px width, and lands on
    the INTER_AREA grid, so a window offset by 8 px is an exact 3 px shift of
    the downscaled image.
    """
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 255, (72, width // 8), dtype=np.uint8)
    return np.kron(blocks, np.ones((10, 8), np.uint8))


def _equal_size_score(frame, template):
    """The pre-fix score, written out independently of production."""
    import cv2

    small = system.downscale_for_gate(frame)
    tmpl = system.downscale_for_gate(template)
    return float(np.max(cv2.matchTemplate(small, tmpl, cv2.TM_CCOEFF_NORMED)))


def test_shift_tolerance_is_a_named_documented_constant():
    assert system.COURT_VIEW_SHIFT_PX == 4
    assert system.COURT_VIEW_SCORE_WIDTH == 480


def test_a_3px_shift_barely_moves_the_score_while_the_old_score_collapses():
    canvas = _canvas(41)
    template = canvas[:, 8:1288]
    frame = canvas[:, 0:1280]          # the image drifted 8 px = 3 px at 480 wide
    s = _fake_system()
    small = system.downscale_for_gate(template)

    aligned = s._court_view_score(template.copy(), small)
    shifted = s._court_view_score(frame, small)
    assert aligned > 0.99
    assert abs(aligned - shifted) < 0.01

    # The test must really exercise a shift: the equal-size score drops by a lot.
    old_aligned = _equal_size_score(template.copy(), template)
    old_shifted = _equal_size_score(frame, template)
    assert old_aligned - old_shifted > 0.15


def test_a_shift_beyond_the_tolerance_is_penalised():
    canvas = _canvas(42, width=1304)   # 24 px = 9 px at 480 wide, > 4
    template = canvas[:, 24:1304]
    frame = canvas[:, 0:1280]
    s = _fake_system()
    small = system.downscale_for_gate(template)
    assert s._court_view_score(template.copy(), small) - \
        s._court_view_score(frame, small) > 0.1


def test_an_unrelated_frame_still_scores_far_below_a_match():
    template = _canvas(43)[:, 0:1280]
    other = _canvas(44)[:, 0:1280]
    s = _fake_system()
    small = system.downscale_for_gate(template)
    assert s._court_view_score(template.copy(), small) > 0.99
    assert s._court_view_score(other, small) < 0.3


def test_the_score_is_a_python_float_and_non_finite_maxima_are_not_hidden(monkeypatch):
    import cv2

    s = _fake_system()
    small = system.downscale_for_gate(_template())
    score = s._court_view_score(_template().copy(), small)
    assert type(score) is float

    monkeypatch.setattr(cv2, "matchTemplate",
                        lambda *_a, **_k: np.full((9, 9), np.nan, np.float32))
    got = s._court_view_score(_template(), small)
    assert type(got) is float and got != got      # NaN, so calibration drops it


def test_a_template_too_small_to_crop_falls_back_to_the_equal_size_score():
    rng = np.random.default_rng(45)
    tiny = rng.integers(0, 255, (8, 100), dtype=np.uint8)    # 8 < 2*4 + 1
    s = _fake_system()
    assert abs(s._court_view_score(tiny.copy(), tiny) - 1.0) < 1e-4
    narrow = rng.integers(0, 255, (100, 8), dtype=np.uint8)
    assert abs(s._court_view_score(narrow.copy(), narrow) - 1.0) < 1e-4


def test_calibration_and_the_live_gate_use_the_same_scoring_function(monkeypatch):
    import cv2

    calls = []

    def scoring(gray, tmpl):
        calls.append(gray.shape)
        return 0.9

    _FakeCapture.instances.clear()
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    s = _fake_system()
    s.court_view_pinned = False
    template = _template()
    small = system.downscale_for_gate(template)
    s.court_view_template_small = small
    monkeypatch.setattr(s, "_court_view_score", scoring)

    s._calibrate_court_view("video.mp4", small, 30.0)
    in_calibration = len(calls)
    assert in_calibration == 67

    s.court_view_cut = 0.5
    assert s._court_view_for_frame(template.copy(), template, 1) is True
    assert len(calls) == in_calibration + 1


# --- the 0.75 cap ---


def _calibrate_with_scores(monkeypatch, score_value):
    import cv2

    _FakeCapture.instances.clear()
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    s = _fake_system()
    monkeypatch.setattr(s, "_court_view_score", lambda *_a: score_value)
    s._calibrate_court_view("video.mp4", system.downscale_for_gate(_template()), 30.0)
    return s


def test_a_calibrated_cut_above_the_fallback_is_capped_and_recorded(monkeypatch):
    s = _calibrate_with_scores(monkeypatch, 0.93)   # MAD 0 -> cut = median = 0.93
    assert s.court_view_cut == system.COURT_VIEW_FALLBACK_CUT == 0.75
    record = s._court_view_metadata()
    assert record["cut"] == 0.75
    assert record["capped"] is True
    assert record["calibrated_cut"] == pytest.approx(0.93)
    assert record["calibration"] == "median-4mad"
    assert record["median"] == pytest.approx(0.93)


def test_a_calibrated_cut_below_the_fallback_is_untouched(monkeypatch):
    s = _calibrate_with_scores(monkeypatch, 0.60)
    assert s.court_view_cut == pytest.approx(0.60)
    record = s._court_view_metadata()
    assert record["cut"] == pytest.approx(0.60)
    assert record["capped"] is False
    assert record["calibrated_cut"] == pytest.approx(0.60)


def test_a_calibrated_cut_exactly_at_the_fallback_is_not_reported_as_capped(monkeypatch):
    s = _calibrate_with_scores(monkeypatch, 0.75)
    assert s.court_view_cut == pytest.approx(0.75)
    assert s._court_view_metadata()["capped"] is False


def test_template_mismatch_is_never_capped(monkeypatch):
    s = _calibrate_with_scores(monkeypatch, 0.05)
    assert s.court_view_cut == float("-inf")
    record = s._court_view_metadata()
    assert record["calibration"] == "template_mismatch_pass_all"
    assert record["cut"] is None
    assert record["capped"] is False
    assert record["calibrated_cut"] is None


def test_the_fallback_constant_is_never_capped(monkeypatch):
    s = _calibrate_with_scores(monkeypatch, float("nan"))    # no finite samples
    assert s.court_view_cut == 0.75
    record = s._court_view_metadata()
    assert record["calibration"] == "fallback_constant"
    assert record["capped"] is False
    assert record["calibrated_cut"] is None


def test_a_failed_calibration_is_never_capped(monkeypatch):
    s = _fake_system()

    def explode(*_a, **_k):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(s, "_calibrate_court_view", explode)
    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 30.0)
    record = s._court_view_metadata()
    assert record["capped"] is False and record["calibrated_cut"] is None


def test_a_manual_pin_is_never_capped(monkeypatch):
    s = _fake_system()
    s.court_view_threshold_override = 0.9          # above 0.75, still honoured
    monkeypatch.setattr(s, "_calibrate_court_view", _boom)
    s._prepare_court_view_cut(system.downscale_for_gate(_template()), 30.0)
    assert s.court_view_cut == 0.9
    record = s._court_view_metadata()
    assert record["cut"] == 0.9
    assert record["capped"] is False
    assert record["calibrated_cut"] is None
