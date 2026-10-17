# Doubles Match Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `--match-format doubles` run tracks all 4 players with match-level identities (P1-P4, two teams), writes per-player stats/heatmaps and an identity-labelled video, and attributes each hit to an individual partner with a confidence; singles stays byte-for-byte unchanged.

**Architecture:** A `DoublesCollector` runs beside the unchanged `PlayerTracker` in the frame loop, feeding a Kalman+Hungarian `MultiTracker` (court metres) and sampling appearance signatures. After the loop, a pure constrained solver (`identity.assign_identities`) maps tracklets to P1-P4; `stroke/hitter.py` picks the partner for each side-level hit in `hits.json`; `visualization/doubles_render.py` writes heatmaps and the re-rendered video. Every doubles stage is never fatal.

**Tech Stack:** Python 3.11/3.12, numpy (<2), scipy (`linear_sum_assignment`), OpenCV 4.10, matplotlib (Agg), Flask, vanilla JS (`static/kestrel.js`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-17-doubles-match-analysis-design.md`

## Global Constraints

- Work only in the worktree `D:/Dev/Claude/Projects/good-badminton/Good-badminton-doubles`, branch `claude/doubles-match-analysis`.
- Run tests with the primary checkout's venv: `PYTHONUTF8=1 "D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv/Scripts/python.exe" -B -m pytest -q -p no:cacheprovider <targets>` from the worktree root.
- No new dependencies (requirements.txt unchanged). Nothing is downloaded at runtime.
- Do NOT edit `badminton_analysis/stroke/hit_*.py`, `badminton_analysis/stroke_recog/`, `badminton_analysis/shuttle_track/`, `third_party/tracknet/`, or the hit-detector scripts (another workstream's paths).
- Singles (the default, `match_format="singles"`) must produce exactly today's outputs; no doubles field appears in any singles output.
- `match_format` accepts only `"singles"` or `"doubles"`; default `"singles"`.
- Court coordinates: metres, width 6.1 (x), length 13.4 (y), net at y = 6.7; `upper` = court-y < 6.7.
- A keypoint is valid iff `x > 1 and y > 1` (project convention). COCO-17 indices: shoulders 5/6, wrists 9/10, hips 11/12, knees 13/14, ankles 15/16.
- Constants (verbatim from the spec): `APPEARANCE_EVERY = 5`, dedupe 0.4 m, `GATE_M = 1.0`, `MAX_MISS_SEC = 0.5`, `CROSS_M = 0.6`, net-cross split after 3 frames, `MIN_TRACKLET_SEC = 1.0`, bystander margin 0.3 m, HSV bins H16 x S4, region floor 50 px, `HITTER_MIN_CONF = 0.6`, swing window +/-0.3 s, coverage bar 0.2, team-confidence bar 0.3, speed cap 8 m/s, jitter floor 0.05 m.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never commit `BirdEye Prototype.html`.
- All user-derived text into HTML in kestrel.js goes through `escHTML`.

## Review Focus

1. **No people / one person all match** (empty court, camera only sees the near side): every doubles stage returns `status: "degraded"` with a reason and nothing raises — pinned in Task 4 (`test_no_tracklets_is_degraded`) and Task 6 (`test_finalize_with_no_observations`).
2. **A singles re-run after a doubles run** leaves stale `players.json` / heatmaps / players video in the folder, which the UI would show as current — pinned in Task 8 (`test_singles_run_removes_stale_doubles_outputs`).
3. **`hits.json` missing or malformed on a doubles run** (fast mode, technique capture off, contact rule found nothing): `players.json` still written, hit counts 0, no exception — pinned in Task 6 (`test_finalize_without_hits_json`).
4. **Both detection passes see the same far player** (whole-frame + far crop) producing a phantom fifth person — pinned in Task 6 (`test_observe_dedupes_near_duplicate_detections`).
5. **Fast mode stride (every 3rd frame)**: the tracker must not end tracks because of the regular gap — pinned in Task 3 (`test_regular_stride_does_not_end_tracks`).

---

## File Structure

| File | Responsibility |
|---|---|
| `badminton_analysis/tracking/appearance.py` (new) | Appearance signature (torso/legs HSV histograms, height in metres, optional embedding) and distances. |
| `badminton_analysis/tracking/multi.py` (new) | `MultiTracker`: frame-to-frame association into single-side tracklets; `filter_tracklets`. Pure. |
| `badminton_analysis/tracking/identity.py` (new) | `assign_identities`: constrained team + partner assignment, labels, confidences, status. Pure. |
| `badminton_analysis/stroke/hitter.py` (new) | `attribute_hitters`: within-side partner choice with confidence. Pure. |
| `badminton_analysis/tracking/doubles.py` (new) | `DoublesCollector` (in-loop adapter) and `finalize` orchestration: tracklets.jsonl, players.json, hits.json augmentation, names. |
| `badminton_analysis/visualization/doubles_render.py` (new) | Per-player/team heatmap PNGs; identity-labelled video re-render. |
| `badminton_analysis/system.py` (modify) | `match_format` arg, metadata, capture gate, in-loop hook, withholding, post-loop stage, stale-output removal. |
| `main.py`, `app.py` (modify) | `--match-format` flag; `/api/analyze` validation and forwarding. |
| `static/kestrel.js` (modify) | Format select with tag default; Players panel. |

---

### Task 1: Format switch (CLI, API, system constructor, metadata)

**Files:**
- Modify: `main.py` (parser after `--dominant-hand`, constructor call)
- Modify: `app.py` (`api_analyze`, around lines 803-865)
- Modify: `badminton_analysis/system.py` (`__init__` signature at line 281-294; `_write_metadata` at 805-837)
- Test: `tests/test_doubles_format_switch.py`

**Interfaces:**
- Produces: `MATCH_FORMATS = ("singles", "doubles")` in `badminton_analysis/system.py`; `BadmintonAnalysisSystem(..., match_format="singles")` storing `self.match_format`; `metadata.json` top-level key `"match_format"`; `main.build_parser()` returning the argparse parser (refactor `main()` to use it); `/api/analyze` returns 400 `{'error': 'invalid match_format'}` for other values and appends `['--match-format', value]` to the command.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_format_switch.py
import json

import pytest

import app as webapp
import main as main_mod
from badminton_analysis import system as sysmod


def test_cli_default_is_singles():
    args = main_mod.build_parser().parse_args([])
    assert args.match_format == "singles"


def test_cli_accepts_doubles():
    args = main_mod.build_parser().parse_args(["--match-format", "doubles"])
    assert args.match_format == "doubles"


def test_cli_rejects_other_values():
    with pytest.raises(SystemExit):
        main_mod.build_parser().parse_args(["--match-format", "mixed"])


def test_match_formats_constant():
    assert sysmod.MATCH_FORMATS == ("singles", "doubles")


def test_normalize_match_format():
    assert sysmod.BadmintonAnalysisSystem._normalize_match_format("doubles") == "doubles"
    assert sysmod.BadmintonAnalysisSystem._normalize_match_format(None) == "singles"
    with pytest.raises(ValueError):
        sysmod.BadmintonAnalysisSystem._normalize_match_format("triples")


def test_metadata_records_match_format(tmp_path):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.video_path, s.video_name = "v.mp4", "v"
    s.frame_width, s.frame_height = 640, 360
    s.ball_model_path = "b.pt"
    s.metadata_path = str(tmp_path / "metadata.json")
    s.output_video_path, s.detections_path = "o.mp4", "d.jsonl"
    s.court_view_calibration = None
    s.court_view_cut = sysmod.COURT_VIEW_FALLBACK_CUT
    s.court_view_threshold_override = None
    s.court_view_pinned = False
    s.match_format = "doubles"
    s._write_metadata(30.0, 10, 0.33, "t.png", [[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 0], [1, 1]], 5)
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["match_format"] == "doubles"


class _FakeProc:
    returncode = None

    def poll(self):
        return None


@pytest.fixture
def analyze_client(tmp_path, monkeypatch):
    captured = {}

    def _fake_popen(cmd, *args, **kwargs):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr(webapp, "VIDEOS", tmp_path)
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "out")
    (tmp_path / "out").mkdir()
    monkeypatch.setattr(webapp.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(webapp.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    (tmp_path / "clip.mp4").write_bytes(b"x")
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), captured


def test_api_forwards_match_format(analyze_client):
    client, captured = analyze_client
    r = client.post("/api/analyze", json={"video": "clip.mp4", "match_format": "doubles"})
    assert r.status_code == 200
    i = captured["cmd"].index("--match-format")
    assert captured["cmd"][i + 1] == "doubles"


def test_api_default_is_singles(analyze_client):
    client, captured = analyze_client
    client.post("/api/analyze", json={"video": "clip.mp4"})
    i = captured["cmd"].index("--match-format")
    assert captured["cmd"][i + 1] == "singles"


def test_api_rejects_invalid_match_format(analyze_client):
    client, captured = analyze_client
    r = client.post("/api/analyze", json={"video": "clip.mp4", "match_format": "mixed"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid match_format"
    assert "cmd" not in captured
```

Before writing the fixture for real, read `app.py` lines 860-960 to see how `api_analyze` starts its monitor (thread or otherwise) after `Popen`; adjust the fixture's stand-ins (`_FakeProc`, the `threading.Thread` patch) so the route returns 200 without running anything. Read `_write_metadata` / `_court_view_metadata` and set on the bare instance exactly the attributes they read.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 "D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv/Scripts/python.exe" -B -m pytest -q -p no:cacheprovider tests/test_doubles_format_switch.py`
Expected: FAIL (`build_parser` / `MATCH_FORMATS` missing).

- [ ] **Step 3: Implement**

`badminton_analysis/system.py`, module level near the other constants:

```python
MATCH_FORMATS = ("singles", "doubles")
```

Constructor: add keyword `match_format="singles"` to the signature (after `dense_whole_video=False`), and early in `__init__`:

```python
        self.match_format = self._normalize_match_format(match_format)
```

Static method on the class:

```python
    @staticmethod
    def _normalize_match_format(value):
        """None -> "singles"; anything outside MATCH_FORMATS raises ValueError."""
        if value is None:
            return "singles"
        if value not in MATCH_FORMATS:
            raise ValueError(f"match_format must be one of {MATCH_FORMATS}, got {value!r}")
        return value
```

`_write_metadata`: add `"match_format": getattr(self, "match_format", "singles"),` as a top-level key right after `"schema_version"`.

`main.py`: move the parser construction into `def build_parser():` returning the parser, have `main()` call `build_parser().parse_args()`, add

```python
    parser.add_argument('--match-format', default='singles', choices=['singles', 'doubles'],
                        help='比赛类型：singles（单打）/ doubles（双打）')
```

and pass `match_format=args.match_format,` to `BadmintonAnalysisSystem(...)`.

`app.py` `api_analyze`, after reading `quality`:

```python
    match_format = data.get('match_format', 'singles')
    if match_format not in ('singles', 'doubles'):
        return jsonify({'error': 'invalid match_format'}), 400
```

(placed before any filesystem side effect), and after `cmd += ['--analysis-quality', quality]`:

```python
    cmd += ['--match-format', match_format]
```

- [ ] **Step 4: Run tests to verify they pass**

Run the Step 2 command, then also `tests/test_app_*.py tests/test_integration_analysis.py`.
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py app.py badminton_analysis/system.py tests/test_doubles_format_switch.py
git commit -m "feat(doubles): match_format switch in CLI, API, system and metadata"
```

---

### Task 2: Appearance signatures

**Files:**
- Create: `badminton_analysis/tracking/appearance.py`
- Test: `tests/test_doubles_appearance.py`

**Interfaces:**
- Consumes: `badminton_analysis.court.scale.PerspectiveScale` (`from_quad(quad)`, `px_per_m(y)`).
- Produces:
  - `signature(frame_bgr, keypoints, scale) -> dict | None` with keys `torso` (np.ndarray shape (64,) or None), `legs` (same or None), `height` (float metres or None), `embed` (None; reserved).
  - `median_signature(samples: list[dict]) -> dict | None`
  - `distance(a: dict, b: dict) -> float` in [0, 1]; returns 1.0 when no comparable term exists.
  - `to_json(sig) -> dict` / `from_json(d) -> dict` (lists <-> arrays) for `tracklets.jsonl`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_appearance.py
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
    kp[11], kp[12] = kp[5] + [0, 2], kp[6] + [0, 2]   # torso 2 px tall -> under 50 px
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `... -m pytest -q -p no:cacheprovider tests/test_doubles_appearance.py`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# badminton_analysis/tracking/appearance.py
"""Appearance signatures for doubles identity (spec 6.3).

A signature is what makes the same person recognisable across tracklets:
HSV histograms of the torso and the legs (shirt and shorts), standing height in
metres, and an optional embedding slot (None unless local re-ID weights exist;
nothing is downloaded). Distances are in [0, 1].
"""
import cv2
import numpy as np

H_BINS, S_BINS = 16, 4
MIN_REGION_PX = 50
W_TORSO, W_LEGS, W_HEIGHT, W_EMBED = 0.6, 0.25, 0.15, 0.5
HEIGHT_SCALE_M = 0.3
_EPS = 1e-9


def _valid(kp, i):
    return kp is not None and len(kp) > i and float(kp[i][0]) > 1 and float(kp[i][1]) > 1


def _polygon(kp, idx):
    if not all(_valid(kp, i) for i in idx):
        return None
    return np.array([[float(kp[i][0]), float(kp[i][1])] for i in idx], dtype=np.float32)


def _region_hist(frame, poly):
    if poly is None:
        return None
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(poly.astype(np.int32)), 255)
    if int(np.count_nonzero(mask)) < MIN_REGION_PX:
        return None
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, [H_BINS, S_BINS], [0, 180, 0, 256]).flatten()
    total = float(hist.sum())
    if total <= 0:
        return None
    return (hist / total).astype(np.float64)


def standing_height_m(kp, scale):
    """Ankle-midpoint to shoulder-midpoint distance, in metres at the ankle row."""
    if scale is None or not all(_valid(kp, i) for i in (5, 6, 15, 16)):
        return None
    sh = (np.asarray(kp[5], float) + np.asarray(kp[6], float)) / 2.0
    an = (np.asarray(kp[15], float) + np.asarray(kp[16], float)) / 2.0
    ppm = float(scale.px_per_m(an[1]))
    if ppm <= 0:
        return None
    return float(np.linalg.norm(an - sh) / ppm)


def signature(frame_bgr, keypoints, scale):
    """Signature of one person in one frame, or None when nothing is measurable."""
    if frame_bgr is None or keypoints is None:
        return None
    kp = np.asarray(keypoints, dtype=float)
    torso = _region_hist(frame_bgr, _polygon(kp, (5, 6, 12, 11)))
    legs = _region_hist(frame_bgr, _polygon(kp, (11, 12, 14, 13)))
    height = standing_height_m(kp, scale)
    if torso is None and legs is None and height is None:
        return None
    return {"torso": torso, "legs": legs, "height": height, "embed": None}


def _median_hist(hists):
    hists = [h for h in hists if h is not None]
    if not hists:
        return None
    m = np.median(np.stack(hists), axis=0)
    s = float(m.sum())
    return m / s if s > 0 else None


def median_signature(samples):
    samples = [s for s in samples or [] if s is not None]
    if not samples:
        return None
    heights = [s["height"] for s in samples if s.get("height") is not None]
    embeds = [s["embed"] for s in samples if s.get("embed") is not None]
    return {
        "torso": _median_hist([s.get("torso") for s in samples]),
        "legs": _median_hist([s.get("legs") for s in samples]),
        "height": float(np.median(heights)) if heights else None,
        "embed": np.median(np.stack(embeds), axis=0) if embeds else None,
    }


def chi2(a, b):
    """Symmetric chi-square distance of two L1-normalised histograms, in [0, 1]."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(0.5 * np.sum((a - b) ** 2 / (a + b + _EPS)))


def distance(a, b):
    """Weighted distance in [0, 1] over the terms both signatures have; 1.0 if none."""
    if a is None or b is None:
        return 1.0
    terms = []
    if a.get("torso") is not None and b.get("torso") is not None:
        terms.append((W_TORSO, chi2(a["torso"], b["torso"])))
    if a.get("legs") is not None and b.get("legs") is not None:
        terms.append((W_LEGS, chi2(a["legs"], b["legs"])))
    if a.get("height") is not None and b.get("height") is not None:
        terms.append((W_HEIGHT, min(1.0, abs(a["height"] - b["height"]) / HEIGHT_SCALE_M)))
    if a.get("embed") is not None and b.get("embed") is not None:
        ea, eb = np.asarray(a["embed"], float), np.asarray(b["embed"], float)
        cos = float(ea @ eb / (np.linalg.norm(ea) * np.linalg.norm(eb) + _EPS))
        terms.append((W_EMBED, min(1.0, max(0.0, (1.0 - cos) / 2.0))))
    if not terms:
        return 1.0
    wsum = sum(w for w, _ in terms)
    return float(sum(w * d for w, d in terms) / wsum)


def to_json(sig):
    if sig is None:
        return None
    return {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in sig.items()}


def from_json(d):
    if d is None:
        return None
    return {k: (np.asarray(v, float) if isinstance(v, list) else v) for k, v in d.items()}
```

- [ ] **Step 4: Run tests to verify they pass** — same command, expect PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/tracking/appearance.py tests/test_doubles_appearance.py
git commit -m "feat(doubles): appearance signatures for player identity"
```

---

### Task 3: MultiTracker (tracklets)

**Files:**
- Create: `badminton_analysis/tracking/multi.py`
- Test: `tests/test_doubles_multi_tracker.py`

**Interfaces:**
- Produces:
  - `Detection` dataclass: `court: tuple[float, float]`, `image: tuple[float, float]`, `keypoints: np.ndarray | None` (17x2 full-frame), `appearance: dict | None`.
  - `MultiTracker(fps, *, gate_m=1.0, max_miss_sec=0.5, cross_m=0.6, net_y=6.7, cross_frames=3)` with `step(frame: int, detections: list[Detection]) -> None` and `finish() -> list[dict]`.
  - Tracklet dict: `{"id": int, "side": "upper"|"lower", "frames": [int], "court": [[x, y]], "image": [[x, y]], "keypoints": [list | None], "appearance": [dict]}` (appearance entries are signature dicts, only for observations that had one).
  - `filter_tracklets(tracklets, fps, *, min_sec=1.0, margin_m=0.3, court=(6.1, 13.4)) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_multi_tracker.py
import numpy as np

from badminton_analysis.tracking.multi import Detection, MultiTracker, filter_tracklets

FPS = 30


def det(x, y):
    return Detection(court=(x, y), image=(x * 100, y * 100), keypoints=None, appearance=None)


def run(frames):
    t = MultiTracker(FPS)
    for f, dets in frames:
        t.step(f, dets)
    return t.finish()


def ids_by_start(tracklets):
    return sorted(tracklets, key=lambda t: (t["frames"][0], t["court"][0][0]))


def test_four_static_players_give_four_tracklets():
    frames = [(f, [det(1.5, 2), det(4.5, 2), det(1.5, 11), det(4.5, 11)]) for f in range(60)]
    tr = run(frames)
    assert len(tr) == 4
    assert all(len(t["frames"]) == 60 for t in tr)
    assert sorted(t["side"] for t in tr) == ["lower", "lower", "upper", "upper"]


def test_partners_moving_past_each_other_without_touching_keep_ids():
    # Two lower-side partners run left/right past each other 1.5 m apart in y.
    frames = []
    for f in range(90):
        a = det(0.5 + 5.0 * f / 89, 8.5)
        b = det(5.5 - 5.0 * f / 89, 10.0)
        frames.append((f, [a, b]))
    tr = run(frames)
    assert len(tr) == 2
    for t in tr:
        ys = {round(c[1], 1) for c in t["court"]}
        assert len(ys) == 1          # each tracklet stays on its own y line: no swap


def test_occlusion_crossing_cuts_tracklets():
    # Partners converge to the same spot; one disappears (occluded) while close.
    frames = []
    for f in range(30):
        frames.append((f, [det(1.0 + f * 0.05, 9.0), det(4.0 - f * 0.05, 9.0)]))
    for f in range(30, 40):
        frames.append((f, [det(2.5, 9.0)]))            # only one visible, both within 0.6 m
    for f in range(40, 70):
        frames.append((f, [det(2.5 - (f - 40) * 0.05, 9.0), det(2.5 + (f - 40) * 0.05, 9.0)]))
    tr = run(frames)
    # Nothing may span the ambiguous stretch: every tracklet either ends by
    # frame 30 or starts at/after it.
    for t in tr:
        assert t["frames"][-1] <= 30 or t["frames"][0] >= 30


def test_gap_longer_than_half_second_ends_track():
    frames = [(f, [det(3, 10)]) for f in range(20)] + [(f, [det(3, 10)]) for f in range(40, 60)]
    tr = run(frames)
    assert len(tr) == 2


def test_regular_stride_does_not_end_tracks():
    frames = [(f, [det(3, 10)]) for f in range(0, 90, 3)]   # fast mode stride 3
    tr = run(frames)
    assert len(tr) == 1


def test_net_crossing_splits_tracklet():
    frames = [(f, [det(3, 5.0 + f * 0.1)]) for f in range(40)]   # walks from y=5 to y=8.9
    tr = run(frames)
    assert len(tr) == 2
    assert {t["side"] for t in tr} == {"upper", "lower"}
    for t in tr:
        ys = [c[1] for c in t["court"]]
        assert all(y < 6.7 for y in ys) or all(y >= 6.7 for y in ys)


def test_filter_drops_short_and_off_court():
    frames = [(f, [det(3, 10)]) for f in range(60)]
    frames += [(f, [det(3, 10), det(-2.0, 10)]) for f in range(60, 120)]   # bystander off court
    frames += [(f, [det(3, 10), det(1, 2)]) for f in range(120, 130)]      # 10 frames only
    kept = filter_tracklets(run(frames), FPS)
    assert len(kept) == 1
    assert kept[0]["court"][0] == [3, 10]


def test_appearance_samples_are_kept():
    sig = {"torso": np.ones(64) / 64, "legs": None, "height": 1.3, "embed": None}
    t = MultiTracker(FPS)
    for f in range(40):
        t.step(f, [Detection((3, 10), (300, 1000), None, sig if f % 5 == 0 else None)])
    (tr,) = t.finish()
    assert len(tr["appearance"]) == 8
```

- [ ] **Step 2: Run tests to verify they fail** — `tests/test_doubles_multi_tracker.py`, expect FAIL.

- [ ] **Step 3: Implement**

```python
# badminton_analysis/tracking/multi.py
"""Frame-to-frame association of N people into single-side tracklets (spec 6.2).

Pure: no I/O, no cv2. A constant-velocity Kalman filter per track predicts
court position; the Hungarian algorithm matches predictions to detections.
Tracks are ended early whenever identity could be confused (occlusion while
two people are close, a long miss, a net crossing): a short tracklet is cheap,
a swapped one poisons identity assignment downstream.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

NET_Y = 6.7


@dataclass
class Detection:
    court: tuple
    image: tuple
    keypoints: object = None
    appearance: object = None


def _side(y, net_y=NET_Y):
    return "upper" if float(y) < net_y else "lower"


class _Kalman:
    """2-D constant-velocity Kalman filter, time in frames."""

    def __init__(self, xy, q=0.02, r=0.05):
        self.x = np.array([xy[0], xy[1], 0.0, 0.0], dtype=float)
        self.P = np.diag([r, r, 1.0, 1.0])
        self.q, self.r = q, r

    def predict(self, dt):
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        Q = self.q * np.diag([dt ** 3 / 3, dt ** 3 / 3, dt, dt])
        return F @ self.x, F @ self.P @ F.T + Q

    def update(self, xy, dt):
        x, P = self.predict(dt)
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1.0
        S = H @ P @ H.T + self.r * np.eye(2)
        K = P @ H.T @ np.linalg.inv(S)
        self.x = x + K @ (np.asarray(xy, float) - H @ x)
        self.P = (np.eye(4) - K @ H) @ P


@dataclass
class _Track:
    id: int
    side: str
    kf: _Kalman
    last_frame: int
    frames: list = field(default_factory=list)
    court: list = field(default_factory=list)
    image: list = field(default_factory=list)
    keypoints: list = field(default_factory=list)
    appearance: list = field(default_factory=list)
    cross_run: int = 0

    def add(self, frame, d):
        self.frames.append(int(frame))
        self.court.append([float(d.court[0]), float(d.court[1])])
        self.image.append([float(d.image[0]), float(d.image[1])])
        kp = d.keypoints
        self.keypoints.append(None if kp is None else np.asarray(kp, float).tolist())
        if d.appearance is not None:
            self.appearance.append(d.appearance)
        self.last_frame = int(frame)

    def predicted(self, frame):
        x, _ = self.kf.predict(max(1, frame - self.last_frame))
        return x[:2]

    def to_dict(self):
        ys = [c[1] for c in self.court]
        return {"id": self.id, "side": _side(np.median(ys)) if ys else self.side,
                "frames": self.frames, "court": self.court, "image": self.image,
                "keypoints": self.keypoints, "appearance": self.appearance}


class MultiTracker:
    def __init__(self, fps, *, gate_m=1.0, max_miss_sec=0.5, cross_m=0.6,
                 net_y=NET_Y, cross_frames=3):
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.gate_m = gate_m
        self.max_miss = max(1, int(round(max_miss_sec * self.fps)))
        self.cross_m = cross_m
        self.net_y = net_y
        self.cross_frames = cross_frames
        self._active = []
        self._done = []
        self._next_id = 0
        self._stride = None
        self._last_step = None

    def _new(self, frame, d):
        t = _Track(self._next_id, _side(d.court[1], self.net_y), _Kalman(d.court), frame)
        self._next_id += 1
        t.add(frame, d)
        self._active.append(t)
        return t

    def _close(self, t):
        if t in self._active:
            self._active.remove(t)
            self._done.append(t)

    def _miss_limit(self):
        # A regular analysis stride (fast mode) is not a miss.
        return self.max_miss + (self._stride or 1)

    def step(self, frame, detections):
        frame = int(frame)
        if self._last_step is not None:
            gap = frame - self._last_step
            if gap > 0 and (self._stride is None or gap < self._stride):
                self._stride = gap
        self._last_step = frame
        dets = [d for d in detections if d is not None and d.court is not None]

        for t in list(self._active):
            if frame - t.last_frame > self._miss_limit():
                self._close(t)

        tracks = list(self._active)
        matches, unmatched_t, unmatched_d = [], set(range(len(tracks))), set(range(len(dets)))
        if tracks and dets:
            preds = [t.predicted(frame) for t in tracks]
            cost = np.array([[np.hypot(p[0] - d.court[0], p[1] - d.court[1]) for d in dets]
                             for p in preds])
            rows, cols = linear_sum_assignment(cost)
            for r, c in zip(rows, cols):
                gap = max(1, (frame - tracks[r].last_frame) / (self._stride or 1))
                if cost[r, c] <= self.gate_m * np.sqrt(gap):
                    matches.append((r, c))
                    unmatched_t.discard(r)
                    unmatched_d.discard(c)

        # Ambiguity: two tracks predicted within cross_m where either is unmatched.
        cut = set()
        if len(tracks) > 1:
            preds = [t.predicted(frame) for t in tracks]
            for i in range(len(tracks)):
                for j in range(i + 1, len(tracks)):
                    if (np.hypot(*(preds[i] - preds[j])) < self.cross_m
                            and (i in unmatched_t or j in unmatched_t)):
                        cut.update((i, j))
        for r, c in matches:
            if r in cut:
                unmatched_d.add(c)
        for i in cut:
            self._close(tracks[i])

        for r, c in matches:
            if r in cut:
                continue
            t, d = tracks[r], dets[c]
            dt = max(1, frame - t.last_frame)
            t.kf.update(d.court, dt)
            t.add(frame, d)
            if _side(d.court[1], self.net_y) != t.side:
                t.cross_run += 1
                if t.cross_run >= self.cross_frames:
                    self._split(t)
            else:
                t.cross_run = 0

        for c in sorted(unmatched_d):
            self._new(frame, dets[c])

    def _split(self, t):
        """Move the last cross_frames observations onto a new track on the other side."""
        n = self.cross_frames
        tail = {k: getattr(t, k)[-n:] for k in ("frames", "court", "image", "keypoints")}
        for k in ("frames", "court", "image", "keypoints"):
            setattr(t, k, getattr(t, k)[:-n])
        t.last_frame = t.frames[-1] if t.frames else t.last_frame
        self._close(t)
        new = _Track(self._next_id, _side(tail["court"][-1][1], self.net_y),
                     _Kalman(tail["court"][0]), tail["frames"][0])
        self._next_id += 1
        for k in ("frames", "court", "image", "keypoints"):
            setattr(new, k, tail[k])
        new.last_frame = tail["frames"][-1]
        for xy in tail["court"][1:]:
            new.kf.update(xy, 1)
        self._active.append(new)

    def finish(self):
        for t in list(self._active):
            self._close(t)
        return [t.to_dict() for t in sorted(self._done, key=lambda t: t.id) if t.frames]


def filter_tracklets(tracklets, fps, *, min_sec=1.0, margin_m=0.3, court=(6.1, 13.4)):
    """Drop tracklets shorter than min_sec or whose median position is off court."""
    fps = float(fps) if fps and fps > 0 else 30.0
    kept = []
    for t in tracklets:
        if not t["frames"] or (t["frames"][-1] - t["frames"][0] + 1) / fps < min_sec:
            continue
        xs = [c[0] for c in t["court"]]
        ys = [c[1] for c in t["court"]]
        mx, my = float(np.median(xs)), float(np.median(ys))
        if not (-margin_m <= mx <= court[0] + margin_m and -margin_m <= my <= court[1] + margin_m):
            continue
        kept.append(t)
    return kept
```

Note on `_split`: appearance samples stay with the original track (they cannot be attributed per frame cheaply); acceptable because the split tail is 3 frames.

- [ ] **Step 4: Run tests to verify they pass.** If `test_occlusion_crossing_cuts_tracklets` fails, inspect which tracklet spans frame 30 and fix the ambiguity rule (not the test): the rule must close both tracks the first frame either is unmatched while their predictions are within 0.6 m.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/tracking/multi.py tests/test_doubles_multi_tracker.py
git commit -m "feat(doubles): multi-person tracker producing single-side tracklets"
```

---

### Task 4: Identity assignment

**Files:**
- Create: `badminton_analysis/tracking/identity.py`
- Test: `tests/test_doubles_identity.py`

**Interfaces:**
- Consumes: tracklet dicts from Task 3; `appearance.median_signature`, `appearance.distance`.
- Produces: `assign_identities(tracklets, analysed_frames, *, min_coverage=0.2, team_conf_min=0.3) -> dict`:

```python
{
  "status": "ok" | "degraded" | "degraded_teams",
  "reason": str | None,
  "assignment": {tracklet_id: "P1".."P4"},   # every surviving tracklet
  "teams": {"P1": 1, "P2": 1, "P3": 2, "P4": 2},  # values None when degraded_teams
  "confidence": {tracklet_id: float},          # [0, 1]
  "coverage": {"P1": float, ...},              # identity frames / analysed_frames
  "dropped": [tracklet_id, ...],
}
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_identity.py
import numpy as np

from badminton_analysis.tracking.identity import assign_identities


def hist(i):
    h = np.full(64, 0.001)
    h[i] = 1.0
    return h / h.sum()


def sig(color, height=1.3):
    return {"torso": hist(color), "legs": hist(color + 20), "height": height, "embed": None}


def tl(tid, side, start, end, s):
    y = 10.0 if side == "lower" else 3.0
    frames = list(range(start, end))
    return {"id": tid, "side": side, "frames": frames,
            "court": [[3.0, y]] * len(frames), "image": [[300.0, y * 50]] * len(frames),
            "keypoints": [None] * len(frames), "appearance": [s] * 3}


def person_of(result, tid):
    return result["assignment"][tid]


def test_four_continuous_players_ok():
    t = [tl(0, "lower", 0, 100, sig(1)), tl(1, "lower", 0, 100, sig(2)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11))]
    r = assign_identities(t, 100)
    assert r["status"] == "ok"
    assert len(set(r["assignment"].values())) == 4
    assert r["teams"][person_of(r, 0)] == r["teams"][person_of(r, 1)] == 1
    assert r["teams"][person_of(r, 2)] == r["teams"][person_of(r, 3)] == 2
    assert all(c == 1.0 for c in r["confidence"].values())


def test_fragmented_tracklets_rejoin_by_appearance():
    # Each player's track breaks at frame 50; second halves must rejoin the right person.
    t = [tl(0, "lower", 0, 50, sig(1)), tl(1, "lower", 0, 50, sig(2)),
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),
         tl(4, "lower", 50, 100, sig(2)), tl(5, "lower", 50, 100, sig(1)),
         tl(6, "upper", 50, 100, sig(11)), tl(7, "upper", 50, 100, sig(10))]
    r = assign_identities(t, 100)
    assert r["status"] == "ok"
    assert person_of(r, 4) == person_of(r, 1) and person_of(r, 5) == person_of(r, 0)
    assert person_of(r, 6) == person_of(r, 3) and person_of(r, 7) == person_of(r, 2)


def test_end_change_keeps_identity_across_sides():
    # Team A (colours 1,2) lower then upper; team B (10,11) upper then lower.
    t = [tl(0, "lower", 0, 50, sig(1)), tl(1, "lower", 0, 50, sig(2)),
         tl(2, "upper", 0, 50, sig(10)), tl(3, "upper", 0, 50, sig(11)),
         tl(4, "upper", 60, 110, sig(1)), tl(5, "upper", 60, 110, sig(2)),
         tl(6, "lower", 60, 110, sig(10)), tl(7, "lower", 60, 110, sig(11))]
    r = assign_identities(t, 110)
    assert person_of(r, 4) == person_of(r, 0) and person_of(r, 5) == person_of(r, 1)
    assert person_of(r, 6) == person_of(r, 2) and person_of(r, 7) == person_of(r, 3)
    assert r["teams"][person_of(r, 0)] == 1      # team first seen on the lower side


def test_matching_kits_separated_by_height():
    t = [tl(0, "lower", 0, 50, sig(1, 1.20)), tl(1, "lower", 0, 50, sig(1, 1.50)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11)),
         tl(4, "lower", 50, 100, sig(1, 1.49)), tl(5, "lower", 50, 100, sig(1, 1.21))]
    r = assign_identities(t, 100)
    assert person_of(r, 4) == person_of(r, 1) and person_of(r, 5) == person_of(r, 0)


def test_three_people_on_one_side_drops_the_shortest():
    t = [tl(0, "lower", 0, 100, sig(1)), tl(1, "lower", 0, 100, sig(2)),
         tl(9, "lower", 10, 30, sig(5)),
         tl(2, "upper", 0, 100, sig(10)), tl(3, "upper", 0, 100, sig(11))]
    r = assign_identities(t, 100)
    assert 9 in r["dropped"] and 9 not in r["assignment"]


def test_only_near_side_visible_is_degraded():
    t = [tl(0, "lower", 0, 100, sig(1)), tl(1, "lower", 0, 100, sig(2))]
    r = assign_identities(t, 100)
    assert r["status"] == "degraded" and "identities" in r["reason"]


def test_no_tracklets_is_degraded():
    r = assign_identities([], 100)
    assert r["status"] == "degraded" and r["assignment"] == {}


def test_indistinguishable_teams_degraded_teams():
    # Second half has every tracklet in identical kit/height: team placement is a coin flip.
    same = sig(1, 1.3)
    t = [tl(0, "lower", 0, 50, same), tl(1, "lower", 0, 50, same),
         tl(2, "upper", 0, 50, same), tl(3, "upper", 0, 50, same),
         tl(4, "lower", 60, 110, same), tl(5, "lower", 60, 110, same),
         tl(6, "upper", 60, 110, same), tl(7, "upper", 60, 110, same)]
    r = assign_identities(t, 110)
    assert r["status"] == "degraded_teams"
    assert all(v is None for v in r["teams"].values())
```

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/tracking/identity.py
"""Constrained global assignment of tracklets to four players (spec 6.4).

Pure. Two stages, each a 2-colouring under hard constraints, with appearance
deciding only what the constraints leave open:

1. Teams. Same side + overlapping in time => same team (partners share a side);
   opposite sides + overlapping => different teams.
2. Partners. Same team + overlapping => different partners.

Constraint-forced placements get confidence 1.0; appearance-decided ones get
the margin between the two choices.
"""
from collections import defaultdict

import numpy as np

from . import appearance as ap


class _UF:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def _coexist(a, b):
    return bool(a["_fs"] & b["_fs"])


def _sig(members):
    samples = [s for m in members for s in m.get("appearance") or []]
    return ap.median_signature(samples)


def _same_side_triple(tl, ids):
    """Shortest of the first three same-side tracklets that share a frame, or None.

    Three people on one side at once is impossible in doubles, so one of them
    (the shortest, i.e. the cheapest to lose) is a bystander or a duplicate.
    """
    for i, a in enumerate(ids):
        peers = [b for b in ids[i + 1:]
                 if tl[b]["side"] == tl[a]["side"] and _coexist(tl[a], tl[b])]
        for x, b in enumerate(peers):
            for c in peers[x + 1:]:
                if tl[a]["_fs"] & tl[b]["_fs"] & tl[c]["_fs"]:
                    return min((a, b, c), key=lambda k: len(tl[k]["frames"]))
    return None


def _margin(d_own, d_other):
    tot = d_own + d_other
    if tot <= 0:
        return 0.0
    return float(np.clip(2.0 * d_other / tot - 1.0, 0.0, 1.0))


def _two_colour(nodes, edges):
    """BFS 2-colouring of each connected group. Returns (groups, conflict_edge).

    groups: list of {node: 0|1}. conflict_edge is the first odd-cycle edge or None.
    """
    adj = defaultdict(set)
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    colour, groups = {}, []
    for n in nodes:
        if n in colour:
            continue
        colour[n] = 0
        group, queue = {n: 0}, [n]
        while queue:
            u = queue.pop()
            for v in adj[u]:
                if v not in colour:
                    colour[v] = 1 - colour[u]
                    group[v] = colour[v]
                    queue.append(v)
                elif colour[v] == colour[u]:
                    return None, (u, v)
        groups.append(group)
    return groups, None


def _orient(groups, weight, sig_of, conf, base_conf=1.0):
    """Fix the heaviest group's orientation; orient the others by appearance.

    Returns {node: 0|1}; writes per-node confidence into ``conf``.
    """
    groups = sorted(groups, key=lambda g: -sum(weight(n) for n in g))
    out = {}
    for n, c in groups[0].items():
        out[n] = c
        conf[n] = min(conf.get(n, 1.0), base_conf)
    for g in groups[1:]:
        ref = [_sig_nodes([n for n in out if out[n] == k], sig_of) for k in (0, 1)]
        mine = [_sig_nodes([n for n in g if g[n] == k], sig_of) for k in (0, 1)]
        straight = sum(ap.distance(mine[k], ref[k]) for k in (0, 1) if mine[k] is not None)
        swapped = sum(ap.distance(mine[k], ref[1 - k]) for k in (0, 1) if mine[k] is not None)
        flip = swapped < straight
        m = _margin(min(straight, swapped), max(straight, swapped))
        for n, c in g.items():
            out[n] = (1 - c) if flip else c
            conf[n] = min(conf.get(n, 1.0), m)
    return out


def _sig_nodes(nodes, sig_of):
    samples = []
    for n in nodes:
        s = sig_of(n)
        if s is not None:
            samples.append(s)
    return ap.median_signature(samples)


def assign_identities(tracklets, analysed_frames, *, min_coverage=0.2, team_conf_min=0.3):
    result = {"status": "degraded", "reason": None, "assignment": {}, "teams": {},
              "confidence": {}, "coverage": {}, "dropped": []}
    tl = {t["id"]: dict(t, _fs=set(t["frames"])) for t in tracklets if t.get("frames")}
    if not tl:
        result["reason"] = "no tracklets"
        return result

    # --- Stage 1: teams ---------------------------------------------------
    while True:
        ids = sorted(tl)
        uf = _UF(ids)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if tl[a]["side"] == tl[b]["side"] and _coexist(tl[a], tl[b]):
                    uf.union(a, b)
        comps = defaultdict(list)
        for i in ids:
            comps[uf.find(i)].append(i)
        # A component with 3+ simultaneous same-side members is impossible.
        bad = None
        for members in comps.values():
            for i, a in enumerate(members):
                for b in members[i + 1:]:
                    if tl[a]["side"] != tl[b]["side"] and _coexist(tl[a], tl[b]):
                        bad = (a, b)
            if bad:
                break
        if bad is None:
            shortest = _same_side_triple(tl, ids)
            if shortest is not None:
                bad = (shortest, shortest)
        cannot = set()
        if bad is None:
            for i, a in enumerate(ids):
                for b in ids[i + 1:]:
                    if tl[a]["side"] != tl[b]["side"] and _coexist(tl[a], tl[b]):
                        ca, cb = uf.find(a), uf.find(b)
                        if ca != cb:
                            cannot.add((min(ca, cb), max(ca, cb)))
            groups, conflict = _two_colour(sorted(comps), cannot)
            if conflict is not None:
                bad = conflict
        if bad is None:
            break
        a, b = bad
        drop = min({a, b}, key=lambda k: len(tl[k]["frames"]))
        result["dropped"].append(drop)
        del tl[drop]
        if not tl:
            result["reason"] = "no tracklets after resolving conflicts"
            return result

    comp_members = {c: comps[c] for c in comps}
    weight = lambda c: sum(len(tl[m]["frames"]) for m in comp_members[c])
    sig_of_comp = lambda c: _sig([tl[m] for m in comp_members[c]])
    comp_conf = {}
    team_of_comp = _orient(groups, weight, sig_of_comp, comp_conf)
    team = {m: team_of_comp[c] for c, ms in comp_members.items() for m in ms}
    conf = {m: comp_conf[c] for c, ms in comp_members.items() for m in ms}
    forced_comps = set(sorted(groups, key=lambda g: -sum(weight(n) for n in g))[0])
    unforced_conf = [comp_conf[c] for c in comp_members if c not in forced_comps]

    # --- Stage 2: partners within each team ------------------------------
    partner = {}
    for k in (0, 1):
        members = sorted(m for m in tl if team[m] == k)
        if not members:
            continue
        while True:
            edges = [(a, b) for i, a in enumerate(members) for b in members[i + 1:]
                     if _coexist(tl[a], tl[b])]
            pgroups, conflict = _two_colour(members, edges)
            if conflict is None:
                break
            drop = min(conflict, key=lambda n: len(tl[n]["frames"]))
            result["dropped"].append(drop)
            members.remove(drop)
            del tl[drop]
        pconf = {}
        oriented = _orient(pgroups, lambda n: len(tl[n]["frames"]),
                           lambda n: _sig([tl[n]]), pconf)
        for n, c in oriented.items():
            partner[n] = c
            conf[n] = min(conf.get(n, 1.0), pconf.get(n, 1.0))

    # --- Labels -------------------------------------------------------------
    lower_first = min((t for t in tl.values() if t["side"] == "lower"),
                      key=lambda t: t["frames"][0], default=None)
    team1 = team[lower_first["id"]] if lower_first else 0
    label_team = {team1: 1, 1 - team1: 2}
    frames_of = defaultdict(set)
    for n, t in tl.items():
        frames_of[(team[n], partner[n])].update(t["_fs"])
    names = {}
    for k in (0, 1):
        order = sorted((0, 1), key=lambda p: -len(frames_of[(k, p)]))
        base = 1 if label_team[k] == 1 else 3
        for rank, p in enumerate(order):
            names[(k, p)] = f"P{base + rank}"

    assignment = {n: names[(team[n], partner[n])] for n in tl}
    present = sorted(set(assignment.values()))
    coverage = {pid: round(len(frames_of[key]) / max(1, analysed_frames), 4)
                for key, pid in names.items() if pid in present}
    teams = {pid: label_team[key[0]] for key, pid in names.items() if pid in present}

    result.update(assignment=assignment, confidence={n: round(float(conf.get(n, 1.0)), 4) for n in tl},
                  coverage=coverage, teams=teams)
    good = [p for p, c in coverage.items() if c >= min_coverage]
    if len(good) < 4:
        result["status"] = "degraded"
        result["reason"] = (f"{len(good)} of 4 identities cover at least "
                            f"{int(min_coverage * 100)}% of analysed frames")
    elif unforced_conf and float(np.median(unforced_conf)) < team_conf_min:
        result["status"] = "degraded_teams"
        result["reason"] = "teams cannot be told apart by appearance"
        result["teams"] = {p: None for p in teams}
    else:
        result["status"] = "ok"
    return result
```

- [ ] **Step 4: Run tests to verify they pass.** If `test_end_change_keeps_identity_across_sides` fails, the cause is team orientation of the post-change group: check `_orient` compares `mine[k]` against `ref[k]` built from the already-oriented heaviest group.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/tracking/identity.py tests/test_doubles_identity.py
git commit -m "feat(doubles): constrained team and partner identity assignment"
```

---

### Task 5: Who hit (within-side partner attribution)

**Files:**
- Create: `badminton_analysis/stroke/hitter.py`
- Test: `tests/test_doubles_hitter.py`

**Interfaces:**
- Consumes: identity tracks shaped `{pid: {frame: {"court": (x, y), "image": (x, y), "keypoints": list | None, "side": str}}}`; `PerspectiveScale` (optional).
- Produces: `attribute_hitters(hits, tracks, fps, *, teams=None, shuttle=None, scale=None) -> list[dict]` returning copies of each hit dict with added keys `hitter_id` (str | None), `hitter_team` (int | None), `hitter_confidence` (float | None), `hitter_evidence` ("shuttle" | "swing" | "single_candidate" | "none"). `HITTER_MIN_CONF = 0.6`. `shuttle` is `{frame: (x, y) | None}` in image pixels.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_hitter.py
import numpy as np

from badminton_analysis.stroke.hitter import HITTER_MIN_CONF, attribute_hitters

FPS = 30


def kp_at(x, y, wrist_dy=40.0, wrist_dx=30.0):
    kp = np.zeros((17, 2))
    kp[5], kp[6] = (x - 20, y - 120), (x + 20, y - 120)
    kp[9] = (x - wrist_dx, y - 120 + wrist_dy)
    kp[10] = (x + wrist_dx, y - 120 + wrist_dy)
    kp[15], kp[16] = (x - 10, y), (x + 10, y)
    return kp.tolist()


def still(x, y, frames, side="lower"):
    return {f: {"court": (3, 10), "image": (x, y), "keypoints": kp_at(x, y), "side": side}
            for f in frames}


def swinging(x, y, frames, hit_frame, side="lower"):
    out = {}
    for f in frames:
        d = abs(f - hit_frame)
        dy = 40.0 if d > 2 else -60.0 + 50.0 * d       # wrist whips above the shoulder
        out[f] = {"court": (3, 10), "image": (x, y), "keypoints": kp_at(x, y, wrist_dy=dy),
                  "side": side}
    return out


FR = range(0, 60)
TEAMS = {"P1": 1, "P2": 1, "P3": 2, "P4": 2}


def test_swing_evidence_picks_the_swinging_partner():
    tracks = {"P1": swinging(200, 400, FR, 30), "P2": still(450, 420, FR)}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] == "P1" and h["hitter_team"] == 1
    assert h["hitter_evidence"] == "swing"
    assert h["hitter_confidence"] >= HITTER_MIN_CONF
    assert h["hitter"] == "lower"                       # side kept


def test_shuttle_evidence_picks_the_nearest_wrist():
    tracks = {"P1": still(200, 400, FR), "P2": still(450, 420, FR)}
    shuttle = {f: (452.0 + 30, 420.0 - 80) for f in FR}  # at P2's right wrist
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS,
                             teams=TEAMS, shuttle=shuttle)
    assert h["hitter_id"] == "P2" and h["hitter_evidence"] == "shuttle"


def test_single_candidate_named_with_half_confidence():
    tracks = {"P1": still(200, 400, FR), "P3": still(300, 100, FR, side="upper")}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] == "P1"
    assert h["hitter_confidence"] == 0.5 and h["hitter_evidence"] == "single_candidate"


def test_no_candidate_on_side():
    tracks = {"P3": still(300, 100, FR, side="upper")}
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] is None and h["hitter_evidence"] == "none"
    assert h["hitter_confidence"] is None


def test_low_confidence_withholds_the_id_but_keeps_the_side():
    tracks = {"P1": still(200, 400, FR), "P2": still(450, 420, FR)}   # nobody swings
    (h,) = attribute_hitters([{"frame": 30, "hitter": "lower"}], tracks, FPS, teams=TEAMS)
    assert h["hitter_id"] is None and h["hitter"] == "lower"
    assert h["hitter_confidence"] < HITTER_MIN_CONF


def test_input_hits_are_not_mutated():
    hits = [{"frame": 30, "hitter": "lower"}]
    attribute_hitters(hits, {}, FPS)
    assert hits == [{"frame": 30, "hitter": "lower"}]
```

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/stroke/hitter.py
"""Which partner hit: within-side attribution for doubles (spec 6.5).

The side comes from the existing hit list (learned model or contact rule).
This module only chooses between the (at most two) identities on that side:
by shuttle proximity when a shuttle track exists, otherwise by swing evidence
(peak wrist speed, wrist above shoulder). Unvalidated without labels; the
confidence and evidence fields let consumers say so.
"""
import math

import numpy as np

HITTER_MIN_CONF = 0.6
SWING_WINDOW_SEC = 0.3
NEAR_FRAMES = 2
SPEED_NORM_MPS = 10.0
DEFAULT_PX_PER_M = 100.0


def _valid(kp, i):
    return kp is not None and len(kp) > i and kp[i][0] > 1 and kp[i][1] > 1


def _obs_near(track, frame, k=NEAR_FRAMES):
    for d in range(k + 1):
        for f in (frame - d, frame + d):
            if f in track:
                return track[f]
    return None


def _ppm(scale, y):
    if scale is None:
        return DEFAULT_PX_PER_M
    try:
        v = float(scale.px_per_m(y))
        return v if v > 0 else DEFAULT_PX_PER_M
    except Exception:
        return DEFAULT_PX_PER_M


def _shuttle_score(obs, pt, scale):
    kp = obs.get("keypoints")
    dists = [math.hypot(kp[i][0] - pt[0], kp[i][1] - pt[1]) for i in (9, 10) if _valid(kp, i)]
    if not dists:
        return None
    return -min(dists) / _ppm(scale, obs["image"][1])


def _swing_score(track, frame, fps, scale):
    w = max(1, int(round(SWING_WINDOW_SEC * fps)))
    frames = [f for f in range(frame - w, frame + w + 1) if f in track]
    peak, raised = 0.0, False
    for f in frames:
        kp = track[f].get("keypoints")
        for i, sh in ((9, 5), (10, 6)):
            if _valid(kp, i) and _valid(kp, sh) and kp[i][1] < kp[sh][1]:
                raised = True              # image y grows downwards: above the shoulder
    for a, b in zip(frames, frames[1:]):
        ka, kb = track[a].get("keypoints"), track[b].get("keypoints")
        ppm = _ppm(scale, track[b]["image"][1])
        for i in (9, 10):
            if _valid(ka, i) and _valid(kb, i):
                px = math.hypot(kb[i][0] - ka[i][0], kb[i][1] - ka[i][1]) / (b - a)
                peak = max(peak, px * fps / ppm)
    return min(peak, 2 * SPEED_NORM_MPS) / SPEED_NORM_MPS + (0.5 if raised else 0.0)


def _confidence(s1, s2):
    return float(math.tanh(abs(s1 - s2) / 2.0))


def attribute_hitters(hits, tracks, fps, *, teams=None, shuttle=None, scale=None):
    teams = teams or {}
    out = []
    for h in hits or []:
        h = dict(h)
        frame, side = int(h["frame"]), h.get("hitter")
        cands = []
        for pid, track in (tracks or {}).items():
            obs = _obs_near(track, frame)
            if obs is not None and obs.get("side") == side:
                cands.append((pid, obs))
        pid, conf, evidence = None, None, "none"
        if len(cands) == 1:
            pid, conf, evidence = cands[0][0], 0.5, "single_candidate"
        elif len(cands) >= 2:
            cands = cands[:2]
            pt = None
            if shuttle:
                for d in range(NEAR_FRAMES + 1):
                    pt = shuttle.get(frame - d) or shuttle.get(frame + d)
                    if pt is not None:
                        break
            scores = None
            if pt is not None:
                ss = [_shuttle_score(o, pt, scale) for _, o in cands]
                if all(s is not None for s in ss):
                    scores, evidence = ss, "shuttle"
            if scores is None:
                scores = [_swing_score(tracks[p], frame, fps, scale) for p, _ in cands]
                evidence = "swing"
            best = int(np.argmax(scores))
            conf = round(_confidence(*scores), 4)
            pid = cands[best][0] if conf >= HITTER_MIN_CONF else None
        h["hitter_id"] = pid
        h["hitter_team"] = teams.get(pid) if pid else None
        h["hitter_confidence"] = conf
        h["hitter_evidence"] = evidence
        out.append(h)
    return out
```

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/hitter.py tests/test_doubles_hitter.py
git commit -m "feat(doubles): within-side hitter attribution with confidence"
```

---

### Task 6: DoublesCollector and finalize (outputs)

**Files:**
- Create: `badminton_analysis/tracking/doubles.py`
- Test: `tests/test_doubles_collector.py`

**Interfaces:**
- Consumes: Tasks 2-5; `badminton_analysis.court.mapper.CourtMapper` (`image_to_court(point)` returns a 2-array or `[]`), `PerspectiveScale.from_quad`, `data.writer.write_json` / `clean_value`.
- Produces:
  - `DOUBLES_OUTPUTS = ("tracklets.jsonl", "players.json")`, `DOUBLES_GLOBS = ("heatmap_P*.png", "heatmap_team*.png", "detect_*_players.mp4")`, `remove_doubles_outputs(save_dir) -> None`.
  - `keypoints_from_pose_data(pose_data, n) -> list[np.ndarray | None]` (full-frame keypoints aligned with merged centroids; `None` entries when unavailable).
  - `DoublesCollector(court_mapper, quad, fps, *, appearance_every=5, dedupe_m=0.4)`:
    - `observe(frame_index, frame_bgr, centroids, keypoints_list) -> None` (never raises; logs once)
    - `finalize(save_dir, analysed_frames, *, shuttle=None) -> dict` (the `players.json` payload; never raises; writes `tracklets.jsonl`, `players.json`, augments `hits.json` in place when present)
    - attribute after finalize: `self.identity_tracks` (`{pid: {frame: obs}}`) and `self.tracklets` (filtered list) for the renderer.
  - `players.json` exactly as spec 6.6, plus `"stroke_labels_withheld": "doubles: BST is a singles model"`; heatmap filenames listed even before rendering (Task 7 writes them; if rendering fails the files are absent and the UI hides missing images).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_collector.py
import json

import numpy as np

from badminton_analysis.court.mapper import CourtMapper
from badminton_analysis.tracking import doubles as dbl

# Image quad mapping 1 px = 1 cm: TL (0,0) .. BR (610,1340).
QUAD = [[0, 0], [610, 0], [610, 1340], [0, 1340]]
FPS = 30


def kp_for(foot_x, foot_y):
    kp = np.zeros((17, 2))
    kp[5], kp[6] = (foot_x - 20, foot_y - 130), (foot_x + 20, foot_y - 130)
    kp[11], kp[12] = (foot_x - 15, foot_y - 70), (foot_x + 15, foot_y - 70)
    kp[13], kp[14] = (foot_x - 15, foot_y - 35), (foot_x + 15, foot_y - 35)
    kp[15], kp[16] = (foot_x - 10, foot_y - 10), (foot_x + 10, foot_y - 10)
    kp[9], kp[10] = (foot_x - 30, foot_y - 90), (foot_x + 30, foot_y - 90)
    return kp


POS = {"a": (150, 1000), "b": (450, 1100), "c": (150, 300), "d": (450, 200)}


def feed(collector, frames, people=("a", "b", "c", "d"), extra=()):
    img = np.zeros((1400, 700, 3), np.uint8)
    img[:, :350] = (0, 0, 200)
    img[:, 350:] = (200, 0, 0)
    for f in frames:
        cents, kps = [], []
        for p in people:
            x, y = POS[p]
            cents.append((x, y))
            kps.append(kp_for(x, y))
        for (x, y) in extra:
            cents.append((x, y))
            kps.append(kp_for(x, y))
        collector.observe(f, img, cents, kps)


def make():
    return dbl.DoublesCollector(CourtMapper(QUAD), QUAD, FPS)


def test_finalize_writes_players_and_tracklets(tmp_path):
    c = make()
    feed(c, range(1, 91))
    payload = c.finalize(str(tmp_path), analysed_frames=90)
    assert (tmp_path / "tracklets.jsonl").exists()
    saved = json.loads((tmp_path / "players.json").read_text(encoding="utf-8"))
    assert saved["match_format"] == "doubles" and saved["validated"] is False
    assert saved["status"] == payload["status"]
    assert len(saved["players"]) == 4
    assert {p["id"] for p in saved["players"]} == {"P1", "P2", "P3", "P4"}
    assert saved["stroke_labels_withheld"].startswith("doubles")


def test_observe_dedupes_near_duplicate_detections(tmp_path):
    c = make()
    # A duplicate of "c" 20 cm away (far player seen by both passes).
    feed(c, range(1, 91), extra=[(POS["c"][0] + 20, POS["c"][1])])
    c.finalize(str(tmp_path), analysed_frames=90)
    lines = (tmp_path / "tracklets.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4


def test_finalize_with_no_observations(tmp_path):
    payload = make().finalize(str(tmp_path), analysed_frames=0)
    assert payload["status"] == "degraded"
    assert payload["players"] == []
    assert (tmp_path / "players.json").exists()


def test_finalize_without_hits_json(tmp_path):
    c = make()
    feed(c, range(1, 91))
    payload = c.finalize(str(tmp_path), analysed_frames=90)
    assert payload["stats"]["hits_total"] == 0
    assert not (tmp_path / "hits.json").exists()


def test_finalize_augments_hits_json(tmp_path):
    c = make()
    feed(c, range(1, 91))
    (tmp_path / "hits.json").write_text(json.dumps(
        {"source": "contact_rule", "hits": [{"frame": 45, "hitter": "lower", "confidence": None}]}),
        encoding="utf-8")
    c.finalize(str(tmp_path), analysed_frames=90)
    hits = json.loads((tmp_path / "hits.json").read_text(encoding="utf-8"))
    assert hits["hitter_attribution"] == {"validated": False}
    h = hits["hits"][0]
    assert h["hitter"] == "lower"
    for key in ("hitter_id", "hitter_team", "hitter_confidence", "hitter_evidence"):
        assert key in h


def test_names_are_copied(tmp_path):
    (tmp_path / "player_names.json").write_text(json.dumps({"P1": "Alice"}), encoding="utf-8")
    c = make()
    feed(c, range(1, 91))
    payload = c.finalize(str(tmp_path), analysed_frames=90)
    names = {p["id"]: p["name"] for p in payload["players"]}
    assert names["P1"] == "Alice" and names["P2"] is None


def test_observe_never_raises():
    c = make()
    c.observe(1, None, [(1, 2)], [None])
    c.observe(2, np.zeros((10, 10, 3), np.uint8), [("x", None)], [object()])


def test_keypoints_from_pose_data_shifts_offsets():
    kp = np.zeros((1, 17, 2))
    kp[0, 5] = (10, 10)
    out = dbl.keypoints_from_pose_data({"keypoints": kp, "offset_x": 100, "offset_y": 50}, 1)
    assert tuple(out[0][5]) == (110, 60)
    assert tuple(out[0][0]) == (0, 0)                     # missing stays missing
    assert dbl.keypoints_from_pose_data(None, 2) == [None, None]
    assert dbl.keypoints_from_pose_data({"keypoints": kp, "offset_x": 0, "offset_y": 0}, 3) == [None, None, None]


def test_remove_doubles_outputs(tmp_path):
    for name in ("players.json", "tracklets.jsonl", "heatmap_P1.png", "heatmap_team1.png",
                 "detect_v_players.mp4", "keep.json"):
        (tmp_path / name).write_text("x")
    dbl.remove_doubles_outputs(str(tmp_path))
    assert [p.name for p in tmp_path.iterdir()] == ["keep.json"]
```

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/tracking/doubles.py
"""Doubles collection (in-loop) and post-loop orchestration (spec 6.1, 6.6).

Runs BESIDE the singles PlayerTracker, never instead of it. Every public
entry point is never fatal: observe() swallows and logs once; finalize()
returns (and writes, when it can) a players.json payload with status "error".
"""
import glob
import json
import os
from collections import defaultdict

import numpy as np

from ..court.scale import PerspectiveScale
from ..data.writer import clean_value, write_json
from ..stroke.hitter import attribute_hitters
from . import appearance as ap
from .identity import assign_identities
from .multi import Detection, MultiTracker, filter_tracklets

DOUBLES_OUTPUTS = ("tracklets.jsonl", "players.json")
DOUBLES_GLOBS = ("heatmap_P*.png", "heatmap_team*.png", "detect_*_players.mp4")
STROKES_WITHHELD = "doubles: BST is a singles model"
SPEED_CAP_MPS = 8.0
JITTER_M = 0.05


def remove_doubles_outputs(save_dir):
    paths = [os.path.join(save_dir, n) for n in DOUBLES_OUTPUTS]
    for g in DOUBLES_GLOBS:
        paths += glob.glob(os.path.join(save_dir, g))
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Could not remove the previous run's {os.path.basename(p)}: {exc}")


def keypoints_from_pose_data(pose_data, n):
    """Full-frame keypoints aligned with the merged centroid list, or Nones."""
    if not pose_data or pose_data.get("keypoints") is None:
        return [None] * n
    people = np.asarray(pose_data["keypoints"], dtype=float)
    if people.ndim != 3 or people.shape[0] != n:
        return [None] * n
    ox, oy = float(pose_data.get("offset_x", 0)), float(pose_data.get("offset_y", 0))
    out = []
    for kp in people:
        kp = kp[:, :2].copy()
        valid = (kp[:, 0] > 1) & (kp[:, 1] > 1)
        kp[valid, 0] += ox
        kp[valid, 1] += oy
        out.append(kp)
    return out


def _n_valid(kp):
    if kp is None:
        return 0
    kp = np.asarray(kp)
    return int(np.sum((kp[:, 0] > 1) & (kp[:, 1] > 1)))


class DoublesCollector:
    def __init__(self, court_mapper, quad, fps, *, appearance_every=5, dedupe_m=0.4):
        self.mapper = court_mapper
        self.fps = float(fps) if fps and fps > 0 else 30.0
        try:
            self.scale = PerspectiveScale.from_quad(quad)
        except Exception:
            self.scale = None
        self.appearance_every = max(1, int(appearance_every))
        self.dedupe_m = dedupe_m
        self.tracker = MultiTracker(self.fps)
        self._n_observed = 0
        self._error_logged = False
        self.tracklets = []
        self.identity_tracks = {}
        self.assignment = {}

    # -- in-loop -----------------------------------------------------------
    def observe(self, frame_index, frame_bgr, centroids, keypoints_list):
        try:
            self._observe(int(frame_index), frame_bgr, list(centroids or []),
                          list(keypoints_list or []))
        except Exception as exc:
            if not self._error_logged:
                self._error_logged = True
                print(f"Doubles collection failed at frame {frame_index}; skipping such "
                      f"frames, analysis continues: {exc!r}")

    def _observe(self, frame_index, frame_bgr, centroids, keypoints_list):
        self._n_observed += 1
        sample = self._n_observed % self.appearance_every == 1 or self.appearance_every == 1
        cands = []
        for i, cen in enumerate(centroids):
            try:
                img = (float(cen[0]), float(cen[1]))
            except (TypeError, ValueError, IndexError):
                continue
            court = self.mapper.image_to_court(img)
            if court is None or len(court) < 2:
                continue
            kp = keypoints_list[i] if i < len(keypoints_list) else None
            if kp is not None and not isinstance(kp, np.ndarray):
                try:
                    kp = np.asarray(kp, dtype=float)
                except (TypeError, ValueError):
                    kp = None
            if kp is not None and (kp.ndim != 2 or kp.shape[0] < 17):
                kp = None
            cands.append(((float(court[0]), float(court[1])), img, kp))

        kept = []
        for c in sorted(cands, key=lambda c: -_n_valid(c[2])):
            if all(np.hypot(c[0][0] - k[0][0], c[0][1] - k[0][1]) >= self.dedupe_m for k in kept):
                kept.append(c)

        dets = []
        for court, img, kp in kept:
            sig = None
            if sample and frame_bgr is not None and kp is not None:
                sig = ap.signature(frame_bgr, kp, self.scale)
            dets.append(Detection(court=court, image=img, keypoints=kp, appearance=sig))
        self.tracker.step(frame_index, dets)

    # -- post-loop ---------------------------------------------------------
    def finalize(self, save_dir, analysed_frames, *, shuttle=None):
        try:
            return self._finalize(save_dir, int(analysed_frames or 0), shuttle)
        except Exception as exc:
            print(f"Doubles analysis failed: {exc!r}")
            payload = self._payload("error", f"{type(exc).__name__}: {exc}", [], [], {})
            try:
                write_json(os.path.join(save_dir, "players.json"), payload)
            except Exception as wexc:
                print(f"players.json could not be written: {wexc!r}")
            return payload

    def _finalize(self, save_dir, analysed_frames, shuttle):
        raw = self.tracker.finish()
        self.tracklets = filter_tracklets(raw, self.fps)
        self._write_tracklets(save_dir, self.tracklets)
        ident = assign_identities(self.tracklets, analysed_frames)
        self.assignment = ident["assignment"]

        tracks = defaultdict(dict)
        for t in self.tracklets:
            pid = ident["assignment"].get(t["id"])
            if pid is None:
                continue
            for f, c, im, kp in zip(t["frames"], t["court"], t["image"], t["keypoints"]):
                tracks[pid][f] = {"court": tuple(c), "image": tuple(im), "keypoints": kp,
                                  "side": t["side"], "tracklet": t["id"]}
        self.identity_tracks = dict(tracks)

        hits = self._augment_hits(save_dir, ident, shuttle)
        names = self._read_names(save_dir)
        players = self._player_rows(ident, hits, names, analysed_frames)
        teams = [{"id": k, "players": sorted(p for p, tk in ident["teams"].items() if tk == k),
                  "coverage_heatmap": f"heatmap_team{k}.png"}
                 for k in (1, 2) if any(tk == k for tk in ident["teams"].values())]
        confs = list(ident["confidence"].values())
        named = sum(1 for h in hits if h.get("hitter_id"))
        stats = {"tracklets_total": len(raw), "tracklets_used": len(ident["assignment"]),
                 "tracklets_dropped": len(raw) - len(ident["assignment"]),
                 "median_confidence": round(float(np.median(confs)), 4) if confs else 0.0,
                 "hits_total": len(hits),
                 "hits_named_share": round(named / len(hits), 4) if hits else 0.0}
        payload = self._payload(ident["status"], ident["reason"], players, teams, stats)
        write_json(os.path.join(save_dir, "players.json"), payload)
        print(f"Doubles: status {payload['status']}, {len(players)} players, "
              f"{stats['tracklets_used']}/{stats['tracklets_total']} tracklets, "
              f"{named}/{len(hits)} hits named")
        return payload

    def _payload(self, status, reason, players, teams, stats):
        base = {"tracklets_total": 0, "tracklets_used": 0, "tracklets_dropped": 0,
                "median_confidence": 0.0, "hits_total": 0, "hits_named_share": 0.0}
        base.update(stats or {})
        return {"schema_version": "1.0", "match_format": "doubles", "status": status,
                "reason": reason, "players": players, "teams": teams, "stats": base,
                "stroke_labels_withheld": STROKES_WITHHELD, "validated": False}

    def _write_tracklets(self, save_dir, tracklets):
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, "tracklets.jsonl"), "w", encoding="utf-8") as fh:
            for t in tracklets:
                row = dict(t, appearance=[ap.to_json(s) for s in t.get("appearance") or []])
                fh.write(json.dumps(clean_value(row), separators=(",", ":")) + "\n")

    def _augment_hits(self, save_dir, ident, shuttle):
        path = os.path.join(save_dir, "hits.json")
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
            hits = doc.get("hits")
            if not isinstance(hits, list):
                return []
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            print(f"hits.json unreadable, no hitter attribution: {exc!r}")
            return []
        teams = {p: t for p, t in ident["teams"].items()}
        doc["hits"] = attribute_hitters(hits, self.identity_tracks, self.fps,
                                        teams=teams, shuttle=shuttle, scale=self.scale)
        doc["hitter_attribution"] = {"validated": False}
        write_json(path, doc)
        return doc["hits"]

    @staticmethod
    def _read_names(save_dir):
        try:
            with open(os.path.join(save_dir, "player_names.json"), encoding="utf-8") as fh:
                d = json.load(fh)
            return {str(k): str(v) for k, v in d.items() if isinstance(v, str) and v.strip()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _player_rows(self, ident, hits, names, analysed_frames):
        rows = []
        for pid in sorted(set(ident["assignment"].values())):
            obs = self.identity_tracks.get(pid, {})
            dist, maxv, secs = self._movement(pid)
            per_tl = [t for t in self.tracklets if ident["assignment"].get(t["id"]) == pid]
            rows.append({
                "id": pid, "team": ident["teams"].get(pid), "name": names.get(pid),
                "frames": len(obs), "coverage": ident["coverage"].get(pid, 0.0),
                "distance_m": round(dist, 2),
                "avg_speed_mps": round(dist / secs, 2) if secs > 0 else 0.0,
                "max_speed_mps": round(maxv, 2),
                "hits": sum(1 for h in hits if h.get("hitter_id") == pid),
                "hits_named": sum(1 for h in hits if h.get("hitter_id") == pid
                                  and h.get("hitter_evidence") in ("shuttle", "swing")),
                "heatmap": f"heatmap_{pid}.png",
                "tracklets": [{"id": t["id"], "start_frame": t["frames"][0],
                               "end_frame": t["frames"][-1],
                               "confidence": ident["confidence"].get(t["id"], 0.0)}
                              for t in per_tl],
            })
        return rows

    def _movement(self, pid):
        """(distance m, max speed m/s, observed seconds), PlayerTracker's floor and cap."""
        step = max(1, int(round(self.fps / 6)))
        dist = maxv = secs = 0.0
        for t in self.tracklets:
            if self.assignment.get(t["id"]) != pid:
                continue
            fr, co = t["frames"], t["court"]
            secs += (fr[-1] - fr[0] + 1) / self.fps
            idx = list(range(0, len(fr), step))
            if idx[-1] != len(fr) - 1:
                idx.append(len(fr) - 1)
            for a, b in zip(idx, idx[1:]):
                d = float(np.hypot(co[b][0] - co[a][0], co[b][1] - co[a][1]))
                dt = (fr[b] - fr[a]) / self.fps
                if dt <= 0 or d <= JITTER_M:
                    continue
                v = d / dt
                if v > SPEED_CAP_MPS:
                    continue
                dist += d
                maxv = max(maxv, v)
        return dist, maxv, secs
```

- [ ] **Step 4: Run tests to verify they pass.** With the synthetic colours (left half red, right half blue) every player has a stable signature; the test asserts 4 identities but not a specific status, because these static players never fragment.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/tracking/doubles.py tests/test_doubles_collector.py
git commit -m "feat(doubles): collector, players.json and hits.json attribution"
```

---

### Task 7: Heatmaps and identity video

**Files:**
- Create: `badminton_analysis/visualization/doubles_render.py`
- Test: `tests/test_doubles_render.py`

**Interfaces:**
- Consumes: `players.json` payload (Task 6), `DoublesCollector.identity_tracks` (`{pid: {frame: {"court", "image", ...}}}`); `badminton_analysis.media.video_audio.setup_video_writer(frame_width, frame_height, fps, temp_output_path)` and `process_video_without_audio(temp_video_path, output_path)`.
- Produces:
  - `PLAYER_COLORS_BGR = {"P1": (255, 140, 0), "P2": (255, 220, 120), "P3": (0, 80, 255), "P4": (120, 180, 255)}`
  - `write_heatmaps(save_dir, payload, identity_tracks) -> list[str]` (written filenames; never raises)
  - `render_identity_video(video_path, out_path, identity_tracks, players, fps=None) -> bool` (frame numbers in `identity_tracks` are 1-based like the system's `frame_count`; returns False and prints on failure)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_render.py
import cv2
import numpy as np

from badminton_analysis.visualization import doubles_render as dr

TRACKS = {
    "P1": {f: {"court": (1.5, 10.0), "image": (40, 60)} for f in range(1, 11)},
    "P3": {f: {"court": (4.5, 3.0), "image": (120, 20)} for f in range(1, 11)},
}
PAYLOAD = {"players": [{"id": "P1", "team": 1, "name": "Alice"}, {"id": "P3", "team": 2, "name": None}],
           "teams": [{"id": 1, "players": ["P1"]}, {"id": 2, "players": ["P3"]}]}


def test_write_heatmaps(tmp_path):
    written = dr.write_heatmaps(str(tmp_path), PAYLOAD, TRACKS)
    assert set(written) == {"heatmap_P1.png", "heatmap_P3.png", "heatmap_team1.png", "heatmap_team2.png"}
    for name in written:
        assert cv2.imread(str(tmp_path / name)) is not None


def test_write_heatmaps_never_raises(tmp_path):
    assert dr.write_heatmaps(str(tmp_path / "nope" / "\0bad"), PAYLOAD, TRACKS) == []


def _tiny_video(path, n=10):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 90))
    for _ in range(n):
        w.write(np.zeros((90, 160, 3), np.uint8))
    w.release()


def test_render_identity_video(tmp_path):
    src = tmp_path / "src.mp4"
    _tiny_video(src)
    out = tmp_path / "detect_src_players.mp4"
    assert dr.render_identity_video(str(src), str(out), TRACKS, PAYLOAD["players"]) is True
    cap = cv2.VideoCapture(str(out))
    ok, frame = cap.read()
    cap.release()
    assert ok and frame[60, 40].sum() > 0          # P1's marker drawn at its foot point


def test_render_identity_video_missing_source(tmp_path):
    assert dr.render_identity_video(str(tmp_path / "none.mp4"), str(tmp_path / "o.mp4"),
                                    TRACKS, PAYLOAD["players"]) is False
```

If `process_video_without_audio` requires ffmpeg and it is not on PATH in the test environment, read `badminton_analysis/media/video_audio.py:116-160` first: when it falls back to copying/renaming the temp file, the test works as written; otherwise make `render_identity_video` fall back to `os.replace(temp, out)` when the transcode raises, and keep the test.

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/visualization/doubles_render.py
"""Doubles visuals: per-player / per-team court heatmaps and the identity video."""
import os

import cv2
import numpy as np

PLAYER_COLORS_BGR = {"P1": (255, 140, 0), "P2": (255, 220, 120),
                     "P3": (0, 80, 255), "P4": (120, 180, 255)}
COURT_W, COURT_L = 6.1, 13.4


def _heatmap_png(path, points, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(3.2, 6.4), dpi=100)
    if points:
        xs, ys = zip(*points)
        ax.hist2d(xs, ys, bins=(12, 26), range=[[0, COURT_W], [0, COURT_L]], cmap="magma")
    ax.add_patch(plt.Rectangle((0, 0), COURT_W, COURT_L, fill=False, color="white", lw=1.5))
    ax.axhline(COURT_L / 2, color="white", lw=1.0)
    ax.set_xlim(-0.3, COURT_W + 0.3)
    ax.set_ylim(COURT_L + 0.3, -0.3)
    ax.set_facecolor("black")
    ax.set_title(title, fontsize=9)
    ax.set_xticks([])
    ax.set_yticks([])
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def write_heatmaps(save_dir, payload, identity_tracks):
    written = []
    try:
        names = {p["id"]: (p.get("name") or p["id"]) for p in payload.get("players") or []}
        for pid, obs in (identity_tracks or {}).items():
            pts = [o["court"] for o in obs.values() if o.get("court") is not None]
            fname = f"heatmap_{pid}.png"
            _heatmap_png(os.path.join(save_dir, fname), pts, names.get(pid, pid))
            written.append(fname)
        for team in payload.get("teams") or []:
            pts = [o["court"] for pid in team.get("players") or []
                   for o in (identity_tracks or {}).get(pid, {}).values()
                   if o.get("court") is not None]
            fname = f"heatmap_team{team['id']}.png"
            _heatmap_png(os.path.join(save_dir, fname), pts, f"Team {team['id']}")
            written.append(fname)
    except Exception as exc:
        print(f"Doubles heatmaps failed: {exc!r}")
        return []
    return written


def render_identity_video(video_path, out_path, identity_tracks, players, fps=None):
    from ..media import video_audio as vap

    cap = None
    writer = None
    temp = os.path.join(os.path.dirname(out_path) or ".", "temp_" + os.path.basename(out_path))
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print(f"Identity video skipped: cannot open {video_path}")
            return False
        fps = fps or cap.get(cv2.CAP_PROP_FPS) or 30.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        writer = vap.setup_video_writer(frame_width=w, frame_height=h, fps=fps, temp_output_path=temp)
        labels = {p["id"]: (p.get("name") or p["id"]) for p in players or []}
        frame_no = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            for pid, obs in (identity_tracks or {}).items():
                o = obs.get(frame_no)
                if o is None:
                    continue
                x, y = int(o["image"][0]), int(o["image"][1])
                color = PLAYER_COLORS_BGR.get(pid, (255, 255, 255))
                cv2.circle(frame, (x, y), max(4, w // 160), color, -1, cv2.LINE_AA)
                cv2.putText(frame, labels.get(pid, pid), (x + 6, y - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, max(0.4, w / 1600), color, 2, cv2.LINE_AA)
            writer.write(frame)
        writer.release()
        writer = None
        try:
            vap.process_video_without_audio(temp_video_path=temp, output_path=out_path)
        except Exception as exc:
            print(f"Identity video transcode failed, keeping the raw encode: {exc!r}")
            os.replace(temp, out_path)
        if os.path.exists(temp):
            os.remove(temp)
        return os.path.exists(out_path)
    except Exception as exc:
        print(f"Identity video failed: {exc!r}")
        return False
    finally:
        if writer is not None:
            writer.release()
        if cap is not None:
            cap.release()
```

- [ ] **Step 4: Run tests to verify they pass.**

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/visualization/doubles_render.py tests/test_doubles_render.py
git commit -m "feat(doubles): per-player heatmaps and identity-labelled video"
```

---

### Task 8: Wire doubles into the analysis system

**Files:**
- Modify: `badminton_analysis/system.py` (constructor ~line 327-380; `process_video` 442-535; `_process_frame` after line 1039; `_run_hit_detection` gate at line 1385; `_run_technique_analysis` / `_run_stroke_recognition` callers)
- Test: `tests/test_doubles_system.py`

**Interfaces:**
- Consumes: `DoublesCollector`, `keypoints_from_pose_data`, `remove_doubles_outputs`, `STROKES_WITHHELD` (Task 6); `write_heatmaps`, `render_identity_video` (Task 7); `self.match_format` (Task 1).
- Produces on `BadmintonAnalysisSystem`:
  - `self._capture_frames` (bool): `analyze_technique or (match_format == "doubles" and analysis_quality != "fast")`.
  - `_init_doubles(corners, fps)`: sets `self._doubles` to a `DoublesCollector` or None (never fatal).
  - `_observe_doubles(frame_count, frame, centroids)`.
  - `_run_doubles_stage(fps, analysed_frames)`: finalize + heatmaps + video; on singles calls `remove_doubles_outputs(self.save_dir)`.
  - `_withhold_singles_outputs_for_doubles()`: removes `strokes.json`, `strokes.jsonl`, `technique_summary.json` and prints the reason.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doubles_system.py
import json

import numpy as np
import pytest

from badminton_analysis import system as sysmod
from badminton_analysis.court.mapper import CourtMapper

QUAD = [[0, 0], [610, 0], [610, 1340], [0, 1340]]


def bare(tmp_path, fmt):
    s = sysmod.BadmintonAnalysisSystem.__new__(sysmod.BadmintonAnalysisSystem)
    s.match_format = fmt
    s.save_dir = str(tmp_path)
    s.video_path = str(tmp_path / "v.mp4")
    s.video_name = "v"
    s.court_corners = QUAD
    s.fps = 30.0
    s.analyze_technique = False
    s.analysis_quality = "accurate"
    return s


def test_capture_gate():
    f = sysmod.BadmintonAnalysisSystem._capture_frames_for
    assert f(False, "singles", "accurate") is False
    assert f(True, "singles", "accurate") is True
    assert f(False, "doubles", "accurate") is True
    assert f(False, "doubles", "fast") is False


def test_singles_run_removes_stale_doubles_outputs(tmp_path):
    for n in ("players.json", "tracklets.jsonl", "heatmap_P1.png", "detect_v_players.mp4"):
        (tmp_path / n).write_text("x")
    s = bare(tmp_path, "singles")
    s._run_doubles_stage(30.0, 100)
    assert not any(tmp_path.iterdir())


def test_singles_init_creates_no_collector(tmp_path):
    s = bare(tmp_path, "singles")
    s._init_doubles(QUAD, 30.0)
    assert s._doubles is None


def test_doubles_stage_writes_players_json(tmp_path, monkeypatch):
    s = bare(tmp_path, "doubles")
    s._init_doubles(QUAD, 30.0)
    assert s._doubles is not None
    frame = np.zeros((1400, 700, 3), np.uint8)
    s.player_pose_visualizer = type("V", (), {"get_current_pose_data": lambda self: None})()
    for f in range(1, 61):
        s._observe_doubles(f, frame, [(150, 1000), (450, 1100), (150, 300), (450, 200)])
    s._run_doubles_stage(30.0, 60)          # no source video on disk: the re-render is skipped
    payload = json.loads((tmp_path / "players.json").read_text(encoding="utf-8"))
    assert payload["match_format"] == "doubles"


def test_doubles_stage_never_raises(tmp_path):
    s = bare(tmp_path, "doubles")
    s._doubles = object()          # broken collector
    s._run_doubles_stage(30.0, 10)  # must not raise


def test_withhold_removes_singles_stroke_outputs(tmp_path):
    for n in ("strokes.json", "strokes.jsonl", "technique_summary.json", "hits.json"):
        (tmp_path / n).write_text("x")
    s = bare(tmp_path, "doubles")
    s._withhold_singles_outputs_for_doubles()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["hits.json"]
```

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement** (all inside `BadmintonAnalysisSystem`)

```python
    @staticmethod
    def _capture_frames_for(analyze_technique, match_format, analysis_quality):
        """Per-frame pose/racket capture: technique analysis, or doubles (not fast mode).

        Doubles needs the side-level hit list (learned model or contact rule),
        which is built from the per-frame capture, even when technique scoring
        (which doubles withholds) is off.
        """
        if analyze_technique:
            return True
        return match_format == "doubles" and analysis_quality != "fast"

    def _init_doubles(self, corners, fps):
        self._doubles = None
        if getattr(self, "match_format", "singles") != "doubles":
            return
        try:
            from .tracking.doubles import DoublesCollector
            self._doubles = DoublesCollector(CourtMapper(corners), corners, fps)
            print("Doubles collection enabled")
        except Exception as exc:
            print(f"Doubles collection unavailable: {exc!r}")

    def _observe_doubles(self, frame_count, frame, centroids):
        collector = getattr(self, "_doubles", None)
        if collector is None:
            return
        try:
            from .tracking.doubles import keypoints_from_pose_data
            pose = self.player_pose_visualizer.get_current_pose_data()
            kps = keypoints_from_pose_data(pose, len(centroids))
            collector.observe(frame_count, frame, centroids, kps)
        except Exception as exc:
            if not getattr(self, "_doubles_observe_logged", False):
                self._doubles_observe_logged = True
                print(f"Doubles observation failed (frame {frame_count}): {exc!r}")

    def _withhold_singles_outputs_for_doubles(self):
        from .tracking.doubles import STROKES_WITHHELD
        print(f"Stroke labels and technique scoring withheld: {STROKES_WITHHELD}")
        for name in ("strokes.json", "strokes.jsonl", "technique_summary.json"):
            try:
                os.remove(os.path.join(self.save_dir, name))
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"Could not remove the previous run's {name}: {exc}")

    def _run_doubles_stage(self, fps, analysed_frames):
        """Post-loop doubles analysis. Singles: only removes stale doubles outputs."""
        try:
            from .tracking import doubles as dbl
            dbl.remove_doubles_outputs(self.save_dir)
            if getattr(self, "match_format", "singles") != "doubles":
                return
            collector = getattr(self, "_doubles", None)
            if collector is None:
                print("Doubles stage skipped: no collector")
                return
            shuttle = None
            traj = getattr(self, "_shuttle_trajectory", None)
            if traj is not None:
                shuttle = {int(k): (float(v[0]), float(v[1])) for k, v in traj.items()
                           if v is not None}
            payload = collector.finalize(self.save_dir, analysed_frames, shuttle=shuttle)
            from .visualization.doubles_render import render_identity_video, write_heatmaps
            write_heatmaps(self.save_dir, payload, collector.identity_tracks)
            if os.path.exists(self.video_path):
                out = os.path.join(self.save_dir, f"detect_{self.video_name}_players.mp4")
                render_identity_video(self.video_path, out, collector.identity_tracks,
                                      payload.get("players") or [], fps=fps)
        except Exception as exc:
            print(f"Doubles stage failed: {exc!r}")
```

Check how `self._shuttle_trajectory` is keyed and valued (read `_run_shuttle_pretrack`, around line 1310) and adapt the `shuttle` dict comprehension to that shape.

Wiring changes:

1. Constructor, after `self.analysis_quality` handling and after `self.match_format` is set: replace the racket-detector condition `if self.analyze_technique:` with `if self._capture_frames:` where, right before it,
   ```python
           self._capture_frames = self._capture_frames_for(
               self.analyze_technique, self.match_format, self.analysis_quality)
   ```
   (place this after the fast-mode line that sets `self.analyze_technique = False`). Also initialise `self._doubles = None` and a counter `self._doubles_frames = 0`.
2. `process_video`: after `self._init_far_pose(...)` add `self._init_doubles(corners, fps)`. After `hits = self._run_hit_detection(fps, frame_count)` replace
   ```python
           if self.analyze_technique:
               self._run_technique_analysis(hits)

           self._run_stroke_recognition(hits)
   ```
   with
   ```python
           if self.match_format == "doubles":
               self._withhold_singles_outputs_for_doubles()
           else:
               if self.analyze_technique:
                   self._run_technique_analysis(hits)
               self._run_stroke_recognition(hits)
           self._run_doubles_stage(fps, self._doubles_frames)
   ```
3. `_process_frame`: replace `if self.analyze_technique:` (the `_capture_analysis_frame` call, line 1041) with `if getattr(self, "_capture_frames", self.analyze_technique):`.
   Appearance histograms must be computed on pixels with no overlay drawn on them, and the pose-ROI rectangle is drawn on `frame` before pose detection runs. So take a clean copy first: immediately before `if self.show_pose_roi:` add
   ```python
           clean = frame.copy() if getattr(self, "_doubles", None) is not None else None
   ```
   and directly after `self._record_rally_frame(...)` add
   ```python
           if clean is not None:
               self._doubles_frames += 1
               self._observe_doubles(frame_count, clean, centroids)
   ```
   (`centroids` at that point is the merged whole-frame + far-pass list, whose order matches `get_current_pose_data()["keypoints"]`.)
4. `_run_hit_detection` line 1385: `if not self.analyze_technique or not last_frame:` becomes `if not getattr(self, "_capture_frames", self.analyze_technique) or not last_frame:`.

- [ ] **Step 4: Run tests to verify they pass**, then the singles regression set:

Run: `... -m pytest -q -p no:cacheprovider tests/test_doubles_system.py tests/test_integration_analysis.py tests/test_bst_integration.py tests/test_hit_integration.py tests/test_player_half_classification.py tests/test_far_player_pass.py tests/test_rally_segmentation.py`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/system.py tests/test_doubles_system.py
git commit -m "feat(doubles): wire collection, withholding and post-loop stage into the system"
```

---

### Task 9: Kestrel UI (format select and Players panel)

**Files:**
- Modify: `static/kestrel.js` (`renderStepConfig` ~line 889; `startAnalysis` ~line 931; `renderResults` ~line 1045-1240)
- Test: `tests/test_doubles_kestrel.py`

**Interfaces:**
- Consumes: library video objects (`lib.videos`, `tagsOf(v)`), `wiz.video`, `escHTML`, `/api/output/<stem>/players.json`, heatmap PNGs, `detect_<stem>_players.mp4`.
- Produces: `#cfg-format` select; payload key `match_format`; a `#players-box` results section.

- [ ] **Step 1: Write the failing tests** (static checks, the project's existing pattern for kestrel.js, see `tests/test_app_rally_summary.py:183`)

```python
# tests/test_doubles_kestrel.py
from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "static" / "kestrel.js").read_text(encoding="utf-8")


def test_format_select_and_payload():
    assert "cfg-format" in JS
    assert "match_format: document.getElementById('cfg-format').value" in JS


def test_format_defaults_from_doubles_tag():
    assert "indexOf('doubles')" in JS


def test_players_panel_reads_players_json():
    assert "/players.json" in JS
    assert "players-box" in JS


def test_unvalidated_and_withheld_notes():
    assert "unvalidated" in JS.lower()
    assert "Stroke types withheld for doubles" in JS


def test_player_names_are_escaped():
    i = JS.index("/players.json")
    window = JS[i:i + 4000]
    assert "escHTML(String(p.name || p.id))" in window
    assert "escHTML(String(d.reason))" in window
```

- [ ] **Step 2: Run tests to verify they fail.**

- [ ] **Step 3: Implement**

1. In `renderStepConfig` (match branch), add after the analysis-quality row:
   ```js
        fieldRow('比赛类型','Match format', selectHTML('cfg-format', [['singles','单打','Singles'],['doubles','双打','Doubles']])) +
   ```
   After `body.innerHTML = ...` (match mode only), default from tags:
   ```js
    if (!isPosture) {
      var vv = lib.videos.filter(function (x) { return x.name === wiz.video || x.name === wiz.video.replace(/\.[^.]+$/, ''); })[0];
      if (vv && tagsOf(vv).indexOf('doubles') !== -1) document.getElementById('cfg-format').value = 'doubles';
    }
   ```
   Read lines 20-60 and 300-360 of kestrel.js first to confirm what `v.name` holds (file name or stem) and keep only the comparison that matches.
2. In `startAnalysis` match payload add `match_format: document.getElementById('cfg-format').value`.
3. In `renderResults`, add `'<div class="res-section" id="players-box" style="display:none"></div>' +` before the technique section, and after the strokes.json fetch:
   ```js
    // Doubles players panel (players.json exists only for doubles runs).
    fetch('/api/output/' + stem + '/players.json').then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
      if (!d || d.match_format !== 'doubles') return;
      var box = document.getElementById('players-box'); if (!box) return;
      var colors = { P1: '#008cff', P2: '#78dcff', P3: '#ff5000', P4: '#ffb478' };
      var status = d.status === 'ok' ? '' :
        '<p class="muted">' + (zh ? '身份识别状态：' : 'Identity status: ') + escHTML(String(d.status)) +
        (d.reason ? ' — ' + escHTML(String(d.reason)) : '') + '</p>';
      var rows = (Array.isArray(d.players) ? d.players : []).map(function (p) {
        var label = escHTML(String(p.name || p.id));
        return '<tr><td><span style="color:' + (colors[p.id] || 'inherit') + '">●</span> ' + label + '</td>' +
          '<td>' + (p.team == null ? '—' : escHTML(String(p.team))) + '</td>' +
          '<td>' + escHTML(String(p.distance_m)) + ' m</td>' +
          '<td>' + escHTML(String(p.avg_speed_mps)) + ' / ' + escHTML(String(p.max_speed_mps)) + ' m/s</td>' +
          '<td>' + escHTML(String(p.hits)) + '</td></tr>';
      }).join('');
      var heat = (Array.isArray(d.players) ? d.players : []).map(function (p) {
        return '<div class="viz-cell"><img class="res-viz" src="/api/output/' + stem + '/' + encodeURIComponent(p.heatmap) +
          '" alt="heatmap" onerror="this.parentNode.style.display=\'none\'"><div class="viz-cap mono">' + escHTML(String(p.name || p.id)) + '</div></div>';
      }).join('');
      var share = d.stats && typeof d.stats.hits_named_share === 'number' ? Math.round(d.stats.hits_named_share * 100) : 0;
      box.innerHTML = '<h2>' + (zh ? '双打球员' : 'Doubles players') + '</h2>' + status +
        '<table class="mono"><thead><tr><th>' + (zh ? '球员' : 'Player') + '</th><th>' + (zh ? '队' : 'Team') + '</th><th>' +
        (zh ? '距离' : 'Distance') + '</th><th>' + (zh ? '均速 / 最高' : 'Avg / max speed') + '</th><th>' + (zh ? '击球' : 'Hits') + '</th></tr></thead><tbody>' +
        rows + '</tbody></table>' +
        '<p class="chip">' + (zh ? '击球归属：未验证（' + share + '% 已命名）' : 'Who hit: unvalidated (' + share + '% named)') + '</p>' +
        '<p class="muted">' + (zh ? '双打不提供击球类型（BST 为单打模型）' : 'Stroke types withheld for doubles (BST is a singles model)') + '</p>' +
        '<div class="viz-row">' + heat + '</div>' +
        '<p><a href="/api/output/' + stem + '/detect_' + encodeURIComponent(stem) + '_players.mp4" target="_blank">' +
        (zh ? '球员身份视频' : 'Player identity video') + '</a></p>';
      box.style.display = '';
    }).catch(function () {});
   ```
   Confirm `zh` and `stem` are in scope at that point of `renderResults` (they are used by the neighbouring strokes.json block) and that `escHTML` is the existing helper.

- [ ] **Step 4: Run tests to verify they pass**, plus `tests/test_app_rally_summary.py tests/test_tracknet_whole_video.py tests/test_app_library.py` (other kestrel.js static checks).

- [ ] **Step 5: Commit**

```bash
git add static/kestrel.js tests/test_doubles_kestrel.py
git commit -m "feat(doubles): Kestrel match-format select and players panel"
```

---

### Final verification (controller)

- [ ] Full suite: `PYTHONUTF8=1 "D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv/Scripts/python.exe" -B -m pytest -q -p no:cacheprovider --basetemp <unique tmp>` from the worktree root. Expected: previous count (1246+ passed, 3 symlink skips) plus the new doubles tests, 0 failures.
- [ ] Update `.ai/workstreams/doubles-match-analysis.md` with changed paths, verification record (command, result, commit), and the owner-run next action (Done-means item 5).
- [ ] Commit, push `claude/doubles-match-analysis` to `origin`, open a PR against `main`.
