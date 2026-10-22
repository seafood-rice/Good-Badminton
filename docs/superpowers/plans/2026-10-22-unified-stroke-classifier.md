# Unified Stroke Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the BST model and the rule classifier with one learned 12-type stroke classifier
built on the hit detector's inputs, so every panel of the app shows the same label for each hit.

**Architecture:** A small temporal CNN (`stroke_model.py`) reads a −0.5 s…+1.0 s window of the hit
detector's features (`hit_features.compute_features`) around each hit, reordered hitter-first,
and outputs 12 stroke types, or `unknown` below a calibrated threshold. The match pipeline writes
schema-2 `strokes.json` from the shared hit list. Technique analysis takes its stroke type from
those labels; types with no reference ranges are labelled but not scored. A labelling sheet plus
a scoring script measure the owner's accuracy bar on DJI footage.

**Tech Stack:** Python 3.12, numpy, torch, OpenCV, Flask (`app.py`), vanilla ES modules (Kestrel
UI), pytest, `node --test`.

**Spec:** `docs/superpowers/specs/2026-10-22-unified-stroke-classifier-design.md`

## Global Constraints

- Taxonomy ids, verbatim: `short_serve`, `long_serve`, `clear`, `lift`, `smash`, `drop`,
  `net_shot`, `block`, `cross_net`, `net_rush`, `drive`, `push`, plus `unknown`.
- Groups, verbatim: `smash`, `clear`, `drop`, `net`, `lift`, `drive`, `serve`. `push` maps to
  `drive`.
- Profiles: clear→`high_clear`, smash→`smash`, drop→`drop_shot`, short_serve/long_serve→`serve`.
  Every other type, and `unknown`, is labelled but not scored.
- Done bar (DJI test split, 7-group level): overall accuracy ≥ 0.85; every group's recall ≥ 0.75;
  unknown ≤ 0.15. `unknown` counts as a miss in recall. At least 20 test hits per group.
- Window: 15 steps before the hit, 30 after (46 steps at 30 Hz).
- Too little signal = the shuttle is visible in < 50% of the 30 post-hit steps, or the hitter's
  pose is invalid at every step within ±2 of the hit → `unknown` without running the model.
- One mechanism: there is never a fallback to another classifier. Missing or incompatible weights
  make every hit `unknown`, with `classifier: null` in `strokes.json`.
- Doubles and degraded rally segmentation keep withholding stroke labels (B11 §12a-C).
- `strokes.json` uses `"schema": 2`. Any other schema is shown as "out of date — re-label",
  never as labels.
- Python tests: `PYTHONUTF8=1 <venv>/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique dir>`.
  The venv is the main checkout's: `D:/Dev/Claude/Projects/good-badminton/Good-badminton/.venv`.
  Run it from the worktree root. JS tests: `node --test tests/js/*.test.mjs`.
- Never commit `data/`, `outputs/`, `weights/` or `BirdEye Prototype.html`. Never push to `main`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A hit at the very start or end of a video**, whose window runs past the signal: the window
   is zero-padded, and the hit gets a label or `unknown`, never an IndexError (Task 3 test
   `test_window_pads_at_edges`).
2. **A result analysed before this change** (old `strokes.json` with no `schema`, labels like
   `net`/`uncertain`, `strokes.jsonl` with `high_clear`): the UI shows the "out of date" notice,
   insights treat it as having no strokes file, and relabel converts it (Task 11 test
   `test_relabel_converts_old_result`; Task 13 JS test `stale strokes file`).
3. **A label sheet the owner has hand-edited with typos** (`Smash `, `net shot`, `nethit`, an
   unknown player): parsing tolerates case and whitespace, and names the row for an unknown value
   instead of silently dropping it (Task 2 test `test_parse_rejects_unknown_stroke_with_row`).
4. **Re-running the sheet tool after labelling started** must not wipe labels: an existing
   `LABELS.md` is never overwritten without `--force` (Task 2 test `test_refuses_to_overwrite`).
5. **The tracked player's hits have a frame with no label** (the hit list and labels diverge, or
   labels are withheld): the technique report is unscored with stroke `unknown`, not a KeyError
   (Task 9 test `test_runner_unknown_when_label_missing`).

---

## File structure

| File | Responsibility |
|---|---|
| `badminton_analysis/stroke/stroke_types.py` (new) | Taxonomy: ids, groups, ShuttleSet raw map, profiles |
| `badminton_analysis/stroke/stroke_labels.py` (new) | Label-sheet format: write, parse, progress table |
| `scripts/make_stroke_label_sheet.py` (new) | CLI: sheet + burnt-in-time clip per DJI result |
| `badminton_analysis/stroke/stroke_windows.py` (new) | Per-hit window extraction, hitter-first ordering, signal check |
| `badminton_analysis/stroke/stroke_model.py` (new) | `StrokeNet`, save/load with sidecar validation |
| `badminton_analysis/stroke/stroke_metrics.py` (new) | Accuracy / recall / unknown rate / confusion matrix, bar check, threshold choice |
| `badminton_analysis/stroke/stroke_classifier.py` (new) | `classify_hits`, `unknown_labels`, `build_payload`, `load_classifier` |
| `scripts/train_stroke_classifier.py` (new) | Training on ShuttleSet + DJI train split |
| `scripts/score_strokes.py` (new) | Scores `strokes.json` against the DJI sheet |
| `badminton_analysis/stroke/relabel.py` (new) | Re-labels finished results from saved hit data |
| `badminton_analysis/system.py` (modify) | Pipeline wiring; the technique runner takes labels |
| `badminton_analysis/analysis/biomechanics.py` (modify) | Profile mapping, unscored reports, `rescore_report` |
| `badminton_analysis/analysis/technique_writer.py` (modify) | Summary counts only scored reports in averages |
| `badminton_analysis/insights/{technique,strokes,loaders}.py` (modify) | Group-level counting; schema-2 gate |
| `badminton_analysis/stroke/events.py` (modify) | Gains `hit_events` (moved from `stroke_recog/hits.py`) |
| `main.py`, `app.py` (modify) | `--stroke-model` replaces `--bst-model` |
| `static/kestrel/core/i18n.js`, `views/workspace.js`, `legacy/kestrel-legacy.js` (modify) | 12-type display, notices |
| Deleted | `badminton_analysis/stroke_recog/`, `badminton_analysis/stroke/classifier.py`, and their tests |

---

### Task 1: Stroke taxonomy module

**Files:**
- Create: `badminton_analysis/stroke/stroke_types.py`
- Test: `tests/test_stroke_types.py`

**Interfaces:**
- Produces:
  - `STROKE_TYPES: tuple[str, ...]` (12, in the spec order);
  - `UNKNOWN = "unknown"`;
  - `GROUPS: tuple[str, ...]` (7);
  - `GROUP_OF: dict[str, str]`;
  - `RAW_TO_TYPE: dict[str, str]`;
  - `PROFILES = ("high_clear", "smash", "drop_shot", "serve")`;
  - `PROFILE_OF: dict[str, str]`;
  - `PROFILE_GROUP: dict[str, str]`;
  - functions `type_of_raw(raw) -> str | None`, `group_of(stroke) -> str | None`,
    `profile_for(stroke) -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stroke_types.py
from badminton_analysis.stroke import stroke_types as st


def test_twelve_types_seven_groups():
    assert st.STROKE_TYPES == ("short_serve", "long_serve", "clear", "lift", "smash", "drop",
                               "net_shot", "block", "cross_net", "net_rush", "drive", "push")
    assert st.GROUPS == ("smash", "clear", "drop", "net", "lift", "drive", "serve")
    assert set(st.GROUP_OF) == set(st.STROKE_TYPES)
    assert set(st.GROUP_OF.values()) == set(st.GROUPS)
    assert st.group_of("push") == "drive" and st.group_of("net_rush") == "net"
    assert st.group_of("unknown") is None and st.group_of(None) is None


def test_raw_shuttleset_types_map_to_twelve():
    assert len(st.RAW_TO_TYPE) == 19          # 18 CSV spellings + the README's 過渡切球
    assert set(st.RAW_TO_TYPE.values()) == set(st.STROKE_TYPES)
    assert st.type_of_raw(" 挑球 ") == "lift" and st.type_of_raw("防守回挑") == "lift"
    assert st.type_of_raw("點扣") == "smash" and st.type_of_raw("推球") == "push"
    assert st.type_of_raw("未知球種") is None and st.type_of_raw("") is None
    assert st.type_of_raw(None) is None


def test_profiles():
    assert st.profile_for("clear") == "high_clear"
    assert st.profile_for("drop") == "drop_shot"
    assert st.profile_for("short_serve") == st.profile_for("long_serve") == "serve"
    assert st.profile_for("smash") == "smash"
    for s in ("lift", "net_shot", "block", "cross_net", "net_rush", "drive", "push", "unknown"):
        assert st.profile_for(s) is None
    # drill mode passes profile names straight through
    for p in st.PROFILES:
        assert st.profile_for(p) == p
    assert st.PROFILE_GROUP == {"high_clear": "clear", "smash": "smash", "drop_shot": "drop",
                                "serve": "serve"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONUTF8=1 $VENV/Scripts/python.exe -B -m pytest tests/test_stroke_types.py -q -p no:cacheprovider`
Expected: FAIL, `ModuleNotFoundError: badminton_analysis.stroke.stroke_types`.

- [ ] **Step 3: Implement**

```python
# badminton_analysis/stroke/stroke_types.py
"""The one stroke taxonomy (spec 2026-10-22 section 3): 12 types, 7 groups, technique profiles.

Every stroke label in the app is one of STROKE_TYPES or UNKNOWN. Groups are what the
distribution, filters and insights count; profiles are the technique reference-range sets
(analysis/reference_ranges.py) a type is scored against. A type without a profile is
labelled but not scored.
"""
STROKE_TYPES = ("short_serve", "long_serve", "clear", "lift", "smash", "drop",
                "net_shot", "block", "cross_net", "net_rush", "drive", "push")
UNKNOWN = "unknown"
GROUPS = ("smash", "clear", "drop", "net", "lift", "drive", "serve")
GROUP_OF = {
    "short_serve": "serve", "long_serve": "serve", "clear": "clear", "lift": "lift",
    "smash": "smash", "drop": "drop", "net_shot": "net", "block": "net", "cross_net": "net",
    "net_rush": "net", "drive": "drive", "push": "drive",
}
RAW_TO_TYPE = {
    "發短球": "short_serve", "發長球": "long_serve", "長球": "clear",
    "挑球": "lift", "防守回挑": "lift", "殺球": "smash", "點扣": "smash",
    # The ShuttleSet README spells this 過渡切球; the CSVs use 過度切球 (both: passive drop).
    "切球": "drop", "過度切球": "drop", "過渡切球": "drop",
    "放小球": "net_shot", "擋小球": "block", "勾球": "cross_net", "撲球": "net_rush",
    "平球": "drive", "小平球": "drive", "後場抽平球": "drive", "防守回抽": "drive",
    "推球": "push",
}
PROFILES = ("high_clear", "smash", "drop_shot", "serve")
PROFILE_OF = {"clear": "high_clear", "smash": "smash", "drop": "drop_shot",
              "short_serve": "serve", "long_serve": "serve"}
PROFILE_GROUP = {"high_clear": "clear", "smash": "smash", "drop_shot": "drop", "serve": "serve"}


def type_of_raw(raw):
    """ShuttleSet's Chinese stroke name -> stroke type, or None (unknown or unmapped)."""
    return RAW_TO_TYPE.get((raw or "").strip())


def group_of(stroke):
    return GROUP_OF.get(stroke) if isinstance(stroke, str) else None


def profile_for(stroke):
    """The reference-range profile for a stroke type (or a drill-mode profile name), or None."""
    if not isinstance(stroke, str):
        return None
    if stroke in PROFILES:
        return stroke
    return PROFILE_OF.get(stroke)
```

- [ ] **Step 4: Run the test to verify it passes.** Same command. Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/stroke_types.py tests/test_stroke_types.py
git commit -m "feat(stroke): one stroke taxonomy (12 types, 7 groups, profiles)"
```

---

### Task 2: Label sheet format and sheet tool

**Files:**
- Create: `badminton_analysis/stroke/stroke_labels.py`, `scripts/make_stroke_label_sheet.py`
- Test: `tests/test_stroke_labels.py`

**Interfaces:**
- Consumes: `stroke_types.STROKE_TYPES`, `GROUPS`, `group_of`.
- Produces:
  - `write_sheet(videos) -> str`. `videos` is a list of
    `{"name": str, "split": "train"|"test", "fps": float, "clip": str, "hits": [{"frame": int, "time_sec": float, "hitter": "lower"|"upper"}]}`.
  - `parse_sheet(text) -> dict[str, {"split": str, "fps": float|None, "rows": [{"t": float, "player": "near"|"far"|None, "stroke": str|None, "frame": int|None, "status": "labelled"|"unsure"|"not_a_hit"|"blank"}]}]`.
  - `progress(sheet) -> dict[split, dict[group, int]]`.
  - `labelled_hits(entry) -> [{"t", "frame", "hitter": "lower"|"upper"|None, "stroke"}]`: only
    `status == "labelled"` rows, with `frame` filled from `t` and the fps when the row has none.

Sheet layout (exactly what `write_sheet` emits, and what `parse_sheet` reads):

```
# DJI stroke labels — labelling sheet
<instructions block, see code>

### DJI_20261010092251_0014_D (split: test)
clip: `DJI_20261010092251_0014_D.mp4` · fps 59.9401

| hit_sec | player | stroke | frame | notes |
|---|---|---|---|---|
| 4.2209 | near |  | 254 |  |
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_labels.py
import subprocess
import sys

import pytest

from badminton_analysis.stroke import stroke_labels as sl

VIDEOS = [{"name": "Dji 2026 0007 D", "split": "test", "fps": 60.0, "clip": "Dji 2026 0007 D.mp4",
           "hits": [{"frame": 61, "time_sec": 1.0, "hitter": "lower"},
                    {"frame": 121, "time_sec": 2.0, "hitter": "upper"}]},
          {"name": "B", "split": "train", "fps": 30.0, "clip": "B.mp4", "hits": []}]


def test_round_trip_prefilled_rows():
    sheet = sl.parse_sheet(sl.write_sheet(VIDEOS))
    a = sheet["Dji 2026 0007 D"]
    assert a["split"] == "test" and a["fps"] == pytest.approx(60.0)
    assert [(r["t"], r["player"], r["frame"], r["status"]) for r in a["rows"]] == [
        (1.0, "near", 61, "blank"), (2.0, "far", 121, "blank")]
    assert sheet["B"]["rows"] == []


def _fill(text, values):
    lines, k = text.splitlines(), 0
    for i, line in enumerate(lines):
        if line.startswith("| ") and line.split("|")[1].strip().replace(".", "").isdigit():
            cells = line.split("|")
            cells[3] = f" {values[k]} "
            lines[i] = "|".join(cells)
            k += 1
    return "\n".join(lines)


def test_parse_statuses_and_case_tolerance():
    text = _fill(sl.write_sheet(VIDEOS[:1]), [" Smash ", "?"])
    text += "\n| 3.5 | far | NET_SHOT |  | added by owner |\n| 4.0 | near | not-a-hit |  |  |\n"
    rows = sl.parse_sheet(text)["Dji 2026 0007 D"]["rows"]
    assert [(r["stroke"], r["status"]) for r in rows] == [
        ("smash", "labelled"), (None, "unsure"), ("net_shot", "labelled"), (None, "not_a_hit")]
    hits = sl.labelled_hits(sl.parse_sheet(text)["Dji 2026 0007 D"])
    assert hits[1] == {"t": 3.5, "frame": 211, "hitter": "upper", "stroke": "net_shot"}


def test_parse_rejects_unknown_stroke_with_row():
    text = _fill(sl.write_sheet(VIDEOS[:1]), ["nethit", ""])
    with pytest.raises(ValueError, match=r"nethit.*Dji 2026 0007 D"):
        sl.parse_sheet(text)


def test_parse_rejects_bad_split_and_player():
    bad = sl.write_sheet(VIDEOS[:1]).replace("(split: test)", "(split: holdout)")
    with pytest.raises(ValueError, match="split"):
        sl.parse_sheet(bad)
    bad = sl.write_sheet(VIDEOS[:1]).replace("| near |", "| middle |", 1)
    with pytest.raises(ValueError, match="middle"):
        sl.parse_sheet(bad)


def test_progress_counts_groups_per_split():
    text = _fill(sl.write_sheet(VIDEOS[:1]), ["push", "long_serve"])
    prog = sl.progress(sl.parse_sheet(text))
    assert prog["test"]["drive"] == 1 and prog["test"]["serve"] == 1
    assert prog["train"] == {g: 0 for g in sl.GROUPS}


def test_refuses_to_overwrite(tmp_path):
    out = tmp_path / "sheet"
    out.mkdir()
    (out / "LABELS.md").write_text("owner work", encoding="utf-8")
    r = subprocess.run([sys.executable, "scripts/make_stroke_label_sheet.py",
                        "--results-dir", str(tmp_path), "--out", str(out), "--no-clips", "X"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "--force" in (r.stderr + r.stdout)
    assert (out / "LABELS.md").read_text(encoding="utf-8") == "owner work"


def test_cli_writes_sheet_from_results(tmp_path):
    import json
    res = tmp_path / "res" / "V1"
    res.mkdir(parents=True)
    (res / "hits.json").write_text(json.dumps({"fps": 30.0, "hits": [
        {"frame": 31, "time_sec": 1.0, "hitter": "lower"}]}), encoding="utf-8")
    out = tmp_path / "sheet"
    r = subprocess.run([sys.executable, "scripts/make_stroke_label_sheet.py",
                        "--results-dir", str(tmp_path / "res"), "--out", str(out),
                        "--no-clips", "--test", "V1", "V1"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    sheet = sl.parse_sheet((out / "LABELS.md").read_text(encoding="utf-8"))
    assert sheet["V1"]["split"] == "test" and sheet["V1"]["rows"][0]["frame"] == 31
```

- [ ] **Step 2: Run to verify they fail.** `pytest tests/test_stroke_labels.py`. Expected: FAIL,
  ModuleNotFoundError.

- [ ] **Step 3: Implement `stroke_labels.py`**

```python
# badminton_analysis/stroke/stroke_labels.py
"""DJI stroke-label sheet (spec 2026-10-22 section 6.1): write, parse, progress.

One "### <video> (split: train|test)" section per analysed video, one table row per hit.
Rows written from hits.json carry the native frame; rows the owner adds may leave it blank
and are converted from the time with the section's fps. Stroke cells take a stroke type
id (case and surrounding space ignored), "?" (unsure: excluded), "not-a-hit", or blank.
"""
import re

from .stroke_types import GROUPS, STROKE_TYPES, group_of

SPLITS = ("train", "test")
_HEADING = re.compile(r"^###\s+(.+?)\s+\(split:\s*([^)]*?)\s*\)\s*$")
_FPS = re.compile(r"fps\s+([\d.]+)")
_NUMBER = re.compile(r"^\d+(\.\d+)?$")
_SIDE = {"lower": "near", "upper": "far"}
_HITTER = {"near": "lower", "far": "upper"}

INSTRUCTIONS = """Fill in the **stroke** column, save, then say it is done.

- Each video has a clip in this folder with the original video time burnt into the top-left
  corner; `hit_sec` is that time. Rows are the hits the detector found.
- **stroke**: one of `""" + "`, `".join(STROKE_TYPES) + """`.
  `?` = unsure (excluded). `not-a-hit` = the detector was wrong. Leave blank = not labelled yet.
- A hit the detector missed: add a row with `hit_sec`, `player` (near/far) and `stroke`;
  leave `frame` blank.
- **player**: `near` = camera side of the net (bottom of the screen); `far` = beyond the net.
- The split of each video (train or test) is fixed; do not change it after training starts.
- Net types: `net_shot` (soft net), `block` (return of a smash/drive to the net),
  `cross_net` (cross-court net), `net_rush` (kill at the net). `push` = flat push to mid/back.
"""


def write_sheet(videos):
    out = ["# DJI stroke labels — labelling sheet", "", INSTRUCTIONS]
    for v in videos:
        if v["split"] not in SPLITS:
            raise ValueError(f"{v['name']}: split must be train or test, not {v['split']!r}")
        out += [f"### {v['name']} (split: {v['split']})",
                f"clip: `{v['clip']}` · fps {float(v['fps']):.4f}", "",
                "| hit_sec | player | stroke | frame | notes |", "|---|---|---|---|---|"]
        for h in v["hits"]:
            out.append(f"| {float(h['time_sec']):.4f} | {_SIDE.get(h['hitter'], '')} |  "
                       f"| {int(h['frame'])} |  |")
        out.append("")
    return "\n".join(out) + "\n"


def _stroke_cell(value, where):
    v = value.strip().lower().replace(" ", "_")
    if v == "":
        return None, "blank"
    if v == "?":
        return None, "unsure"
    if v in ("not-a-hit", "not_a_hit"):
        return None, "not_a_hit"
    if v in STROKE_TYPES:
        return v, "labelled"
    raise ValueError(f"unknown stroke {value.strip()!r} in {where} "
                     f"(use one of {', '.join(STROKE_TYPES)}, ?, not-a-hit)")


def parse_sheet(text):
    sheet, name = {}, None
    for line in text.splitlines():
        m = _HEADING.match(line.strip())
        if m:
            name, split = m.group(1), m.group(2).strip().lower()
            if split not in SPLITS:
                raise ValueError(f"{name}: split must be train or test, not {split!r}")
            sheet[name] = {"split": split, "fps": None, "rows": []}
            continue
        if line.startswith("#"):
            name = None
            continue
        if name is None:
            continue
        f = _FPS.search(line) if line.startswith("clip:") else None
        if f:
            sheet[name]["fps"] = float(f.group(1))
            continue
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells or not _NUMBER.match(cells[0]):
            continue                                  # header, separator or empty row
        where = f"{name} row {line.strip()}"
        player = (cells[1] if len(cells) > 1 else "").lower() or None
        if player not in (None, "near", "far"):
            raise ValueError(f"player {player!r} must be near, far or blank in {where}")
        stroke, status = _stroke_cell(cells[2] if len(cells) > 2 else "", where)
        frame = cells[3] if len(cells) > 3 else ""
        sheet[name]["rows"].append({"t": float(cells[0]), "player": player, "stroke": stroke,
                                    "frame": int(frame) if frame.isdigit() else None,
                                    "status": status})
    return sheet


def labelled_hits(entry):
    fps = entry.get("fps")
    out = []
    for r in entry["rows"]:
        if r["status"] != "labelled":
            continue
        frame = r["frame"]
        if frame is None and fps:
            frame = int(round(r["t"] * fps)) + 1      # time_sec = (frame - 1) / fps
        out.append({"t": r["t"], "frame": frame, "hitter": _HITTER.get(r["player"]),
                    "stroke": r["stroke"]})
    return out


def progress(sheet):
    counts = {s: {g: 0 for g in GROUPS} for s in SPLITS}
    for entry in sheet.values():
        for r in entry["rows"]:
            if r["status"] == "labelled":
                counts[entry["split"]][group_of(r["stroke"])] += 1
    return counts
```

- [ ] **Step 4: Implement `scripts/make_stroke_label_sheet.py`**

```python
"""Write the DJI stroke-label sheet and one burnt-in-time clip per analysed video.

Usage:
  python scripts/make_stroke_label_sheet.py [--results-dir outputs] [--videos-dir videos] \
      [--out outputs/dji-stroke-labels] --test <video> [--test <video> ...] <video> [<video> ...]
  python scripts/make_stroke_label_sheet.py --progress [--out outputs/dji-stroke-labels]

Each <video> is a result folder name under --results-dir that has hits.json. Videos named
with --test go to the test split, the rest to train. An existing LABELS.md is never
overwritten unless --force is given (it holds the owner's labels).
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402

CLIP_WIDTH = 1280


def find_video(videos_dir, name):
    for path in sorted(glob.glob(os.path.join(videos_dir, glob.escape(name) + ".*"))):
        if os.path.isfile(path):
            return path
    return None


def burn_in_clip(src, dst, width=CLIP_WIDTH):
    """Copy of src at `width` px wide with the source time (s) drawn top-left."""
    import cv2
    cap = cv2.VideoCapture(src)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    writer, k = None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, int(round(h * width / w))), interpolation=cv2.INTER_AREA)
        if writer is None:
            writer = cv2.VideoWriter(dst, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                     (frame.shape[1], frame.shape[0]))
        text = f"{k / fps:.2f}s"
        cv2.putText(frame, text, (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(frame, text, (12, 44), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 2, cv2.LINE_AA)
        writer.write(frame)
        k += 1
    cap.release()
    if writer is not None:
        writer.release()
    return k


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("videos", nargs="*")
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--videos-dir", default=os.path.join(ROOT, "videos"))
    p.add_argument("--out", default=os.path.join(ROOT, "outputs", "dji-stroke-labels"))
    p.add_argument("--test", action="append", default=[])
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-clips", action="store_true")
    p.add_argument("--progress", action="store_true")
    a = p.parse_args(argv)
    sheet_path = os.path.join(a.out, "LABELS.md")

    if a.progress:
        with open(sheet_path, encoding="utf-8") as fh:
            prog = sl.progress(sl.parse_sheet(fh.read()))
        print("group   " + "  ".join(f"{s:>5}" for s in sl.SPLITS))
        for g in sl.GROUPS:
            print(f"{g:<7} " + "  ".join(f"{prog[s][g]:>5}" for s in sl.SPLITS))
        return 0
    if os.path.exists(sheet_path) and not a.force:
        print(f"{sheet_path} exists and may hold labels; pass --force to overwrite", file=sys.stderr)
        return 2
    if not a.videos:
        p.error("name at least one analysed video")
    unknown_test = set(a.test) - set(a.videos)
    if unknown_test:
        p.error(f"--test names videos not listed: {sorted(unknown_test)}")

    os.makedirs(a.out, exist_ok=True)
    entries = []
    for name in a.videos:
        with open(os.path.join(a.results_dir, name, "hits.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        fps = float(payload["fps"])
        hits = [{"frame": int(h["frame"]), "hitter": h.get("hitter"),
                 "time_sec": h["time_sec"] if h.get("time_sec") is not None else (int(h["frame"]) - 1) / fps}
                for h in payload.get("hits", [])]
        clip = f"{name}.mp4"
        if not a.no_clips:
            src = find_video(a.videos_dir, name)
            if src is None:
                raise SystemExit(f"source video for {name!r} not found in {a.videos_dir}")
            n = burn_in_clip(src, os.path.join(a.out, clip))
            print(f"{name}: {n} frames -> {clip}")
        entries.append({"name": name, "split": "test" if name in a.test else "train",
                        "fps": fps, "clip": clip, "hits": hits})
    with open(sheet_path, "w", encoding="utf-8") as fh:
        fh.write(sl.write_sheet(entries))
    print(f"wrote {sheet_path}: {sum(len(e['hits']) for e in entries)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass.** `pytest tests/test_stroke_labels.py`. Expected:
  7 passed.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/stroke/stroke_labels.py scripts/make_stroke_label_sheet.py tests/test_stroke_labels.py
git commit -m "feat(stroke): DJI stroke-label sheet and sheet tool"
```

---

### Task 3: Per-hit windows

**Files:**
- Create: `badminton_analysis/stroke/stroke_windows.py`
- Test: `tests/test_stroke_windows.py`

**Interfaces:**
- Consumes: `hit_features.compute_features`, `FEATURE_DIM`, `SHUTTLE_DIM`, `PLAYER_DIM`;
  `hit_signals.RawSignals`.
- Produces:
  - `PRE_STEPS = 15`, `POST_STEPS = 30`, `WINDOW_STEPS = 46`;
  - `INPUT_DIM = FEATURE_DIM + 1` (the last channel is the hitter flag, 1.0 = far player);
  - `nearest_step(idx, native) -> int`;
  - `window(feats, step, hitter) -> np.ndarray (46, INPUT_DIM) float32`;
  - `has_signal(win) -> bool`;
  - `extract(sig, hits, idx=None) -> (X (N, 46, INPUT_DIM) float32, ok (N,) bool)`. `hits` items
    have `frame` (native, 1-based) and `hitter` (`"lower"`/`"upper"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_windows.py
import numpy as np

from badminton_analysis.stroke import stroke_windows as sw
from badminton_analysis.stroke.hit_features import FEATURE_DIM, PLAYER_DIM, SHUTTLE_DIM
from badminton_analysis.stroke.hit_signals import RawSignals

CORNERS = np.array([[400, 200], [880, 200], [1180, 700], [100, 700]], np.float32)


def _sig(n=300, fps=30.0, shuttle=True, pose=True):
    sh = np.full((n, 2), np.nan, np.float32)
    if shuttle:
        sh[:, 0] = np.linspace(300, 900, n)
        sh[:, 1] = 400 + 100 * np.sin(np.linspace(0, 6, n))
    kp = np.full((n, 2, 17, 2), np.nan, np.float32)
    if pose:
        base = np.stack(np.meshgrid(np.arange(17) * 3.0, [0.0]), -1)[0]
        kp[:, 0] = base + [640, 600]
        kp[:, 1] = base + [640, 250]
        kp[:, :, 5:7, 1] -= 60      # shoulders above hips -> valid torso
    return RawSignals(fps, 1, sh, kp, CORNERS, (1280, 720))


def test_window_shape_and_flag():
    X, ok = sw.extract(_sig(), [{"frame": 150, "hitter": "upper"}, {"frame": 150, "hitter": "lower"}])
    assert X.shape == (2, sw.WINDOW_STEPS, sw.INPUT_DIM) and X.dtype == np.float32
    assert X[0, :, -1].min() == 1.0 and X[1, :, -1].max() == 0.0
    assert ok.tolist() == [True, True]


def test_hitter_first_swaps_player_blocks():
    X, _ = sw.extract(_sig(), [{"frame": 150, "hitter": "upper"}, {"frame": 150, "hitter": "lower"}])
    a, b = SHUTTLE_DIM, SHUTTLE_DIM + PLAYER_DIM
    np.testing.assert_array_equal(X[0, :, a:b], X[1, :, b:FEATURE_DIM])
    np.testing.assert_array_equal(X[0, :, :SHUTTLE_DIM], X[1, :, :SHUTTLE_DIM])


def test_window_pads_at_edges():
    X, ok = sw.extract(_sig(n=40), [{"frame": 1, "hitter": "lower"}, {"frame": 40, "hitter": "lower"}])
    assert X.shape[0] == 2
    assert np.all(X[0, :sw.PRE_STEPS, :FEATURE_DIM] == 0)       # before the video starts
    assert not ok[1]                                             # no post-hit shuttle steps


def test_no_signal_without_shuttle_or_pose():
    _, ok = sw.extract(_sig(shuttle=False), [{"frame": 150, "hitter": "lower"}])
    assert not ok[0]
    _, ok = sw.extract(_sig(pose=False), [{"frame": 150, "hitter": "lower"}])
    assert not ok[0]


def test_resamples_60fps_to_30hz():
    X, ok = sw.extract(_sig(n=600, fps=60.0), [{"frame": 300, "hitter": "lower"}])
    assert X.shape == (1, sw.WINDOW_STEPS, sw.INPUT_DIM) and ok[0]


def test_empty_hits():
    X, ok = sw.extract(_sig(), [])
    assert X.shape == (0, sw.WINDOW_STEPS, sw.INPUT_DIM) and ok.shape == (0,)
```

- [ ] **Step 2: Run to verify they fail.** Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement**

```python
# badminton_analysis/stroke/stroke_windows.py
"""Per-hit input windows for the stroke classifier (spec 2026-10-22 section 4.1).

A window is PRE_STEPS before to POST_STEPS after the hit at TARGET_HZ, cut from the hit
detector's features. Player blocks are reordered hitter-first, so one model serves near
and far hits; a final channel flags a far-player (upper) hitter. Steps outside the signal
are zero (every feature's "missing" value).
"""
import numpy as np

from .hit_features import FEATURE_DIM, PLAYER_DIM, SHUTTLE_DIM, compute_features

PRE_STEPS = 15
POST_STEPS = 30
WINDOW_STEPS = PRE_STEPS + 1 + POST_STEPS
INPUT_DIM = FEATURE_DIM + 1
MIN_POST_SHUTTLE = 0.5
POSE_RADIUS = 2
_VISIBLE = SHUTTLE_DIM - 1                   # shuttle "visible" flag column
_P0 = slice(SHUTTLE_DIM, SHUTTLE_DIM + PLAYER_DIM)
_P1 = slice(SHUTTLE_DIM + PLAYER_DIM, FEATURE_DIM)
_HITTER_VALID = SHUTTLE_DIM + PLAYER_DIM - 1  # block 0's pose-valid flag, after reordering


def nearest_step(idx, native):
    """Index of the TARGET_HZ step whose native frame index is nearest ``native``."""
    idx = np.asarray(idx)
    pos = int(np.clip(np.searchsorted(idx, native), 0, len(idx) - 1))
    if pos > 0 and abs(int(idx[pos - 1]) - native) <= abs(int(idx[pos]) - native):
        pos -= 1
    return pos


def window(feats, step, hitter):
    out = np.zeros((WINDOW_STEPS, INPUT_DIM), np.float32)
    lo, hi = step - PRE_STEPS, step + POST_STEPS + 1
    src_lo, src_hi = max(0, lo), min(len(feats), hi)
    if src_lo < src_hi:
        out[src_lo - lo:src_hi - lo, :FEATURE_DIM] = feats[src_lo:src_hi]
    if hitter == "upper":
        p0 = out[:, _P0].copy()
        out[:, _P0] = out[:, _P1]
        out[:, _P1] = p0
        out[:, -1] = 1.0
    return out


def has_signal(win):
    post = win[PRE_STEPS + 1:, _VISIBLE]
    if post.mean() < MIN_POST_SHUTTLE:
        return False
    near = win[PRE_STEPS - POSE_RADIUS:PRE_STEPS + POSE_RADIUS + 1, _HITTER_VALID]
    return bool(near.max() > 0)


def extract(sig, hits, idx=None):
    hits = list(hits)
    X = np.zeros((len(hits), WINDOW_STEPS, INPUT_DIM), np.float32)
    ok = np.zeros(len(hits), bool)
    if not hits:
        return X, ok
    feats, idx = compute_features(sig, idx)
    if len(idx) == 0:
        return X, ok
    for k, h in enumerate(hits):
        step = nearest_step(idx, int(h["frame"]) - sig.first_frame)
        X[k] = window(feats, step, h.get("hitter"))
        ok[k] = has_signal(X[k])
    return X, ok
```

- [ ] **Step 4: Run the tests to verify they pass.** Expected: 6 passed. If `test_window_pads_at_edges`
  fails because the hit at frame 40 still sees shuttle steps, check that the post window is
  zero past the signal end.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/stroke_windows.py tests/test_stroke_windows.py
git commit -m "feat(stroke): hitter-first per-hit feature windows"
```

---

### Task 4: Stroke model and sidecar

**Files:**
- Create: `badminton_analysis/stroke/stroke_model.py`
- Test: `tests/test_stroke_model.py`

**Interfaces:**
- Consumes: `hit_model._Block`, `hit_model.meta_path`, `hit_model.file_sha256`;
  `stroke_windows.INPUT_DIM`, `PRE_STEPS`, `POST_STEPS`; `hit_features.FEATURE_SCHEMA`,
  `TARGET_HZ`; `stroke_types.STROKE_TYPES`.
- Produces:
  - `MODEL_SCHEMA = 1`, `StrokeModelError(Exception)`;
  - `StrokeNet(in_ch, n_classes=12, hidden=64, levels=3, dropout=0.1)`; `forward(x (B,T,C)) -> logits (B,12)`;
  - `save_stroke_model(model, meta, path)`;
  - `load_stroke_model(path, device="cpu") -> (model, meta)`;
  - `predict_probs(model, X) -> np.ndarray (N, 12)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_model.py
import json

import numpy as np
import pytest
import torch

from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.stroke_types import STROKE_TYPES
from badminton_analysis.stroke.stroke_windows import INPUT_DIM, WINDOW_STEPS


def _meta(**over):
    meta = smod.base_meta(hidden=16, levels=2, unknown_threshold=0.4)
    meta.update(over)
    return meta


def test_forward_shape():
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).eval()
    out = m(torch.zeros(3, WINDOW_STEPS, INPUT_DIM))
    assert out.shape == (3, len(STROKE_TYPES))


def test_save_load_round_trip(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).eval()
    path = tmp_path / "stroke-classifier.pt"
    smod.save_stroke_model(m, _meta(), path)
    m2, meta = smod.load_stroke_model(path)
    x = np.random.default_rng(0).normal(size=(4, WINDOW_STEPS, INPUT_DIM)).astype(np.float32)
    np.testing.assert_allclose(smod.predict_probs(m, x), smod.predict_probs(m2, x), atol=1e-6)
    assert smod.predict_probs(m2, x).sum(axis=1) == pytest.approx(np.ones(4), abs=1e-5)
    assert meta["unknown_threshold"] == 0.4


@pytest.mark.parametrize("field,value", [("stroke_types", ["a"]), ("feature_schema", 99),
                                         ("window_pre_steps", 3), ("schema", 7)])
def test_load_rejects_incompatible_sidecar(tmp_path, field, value):
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2)
    path = tmp_path / "s.pt"
    smod.save_stroke_model(m, _meta(**{field: value}), path)
    with pytest.raises(smod.StrokeModelError):
        smod.load_stroke_model(path)


def test_load_rejects_missing_sidecar(tmp_path):
    path = tmp_path / "s.pt"
    torch.save(smod.StrokeNet(INPUT_DIM, hidden=16, levels=2).state_dict(), path)
    with pytest.raises(smod.StrokeModelError, match="sidecar"):
        smod.load_stroke_model(path)


def test_predict_probs_empty():
    m = smod.StrokeNet(INPUT_DIM, hidden=16, levels=2)
    assert smod.predict_probs(m, np.zeros((0, WINDOW_STEPS, INPUT_DIM), np.float32)).shape == (0, 12)
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/stroke/stroke_model.py
"""Stroke classifier network: a small dilated temporal CNN over a per-hit window.

Same building blocks and file conventions as hit_model.py: input normalisation lives in
buffers, weights are a state dict plus a JSON sidecar, loaded with weights_only=True.
Output: logits over stroke_types.STROKE_TYPES (12). "unknown" is not a class; it is
decided by the sidecar's unknown_threshold on the top softmax probability.
"""
import json
import os

import numpy as np
import torch
from torch import nn

from .hit_features import FEATURE_SCHEMA, TARGET_HZ
from .hit_model import _Block, file_sha256, meta_path  # noqa: F401  (file_sha256 re-exported)
from .stroke_types import STROKE_TYPES
from .stroke_windows import INPUT_DIM, POST_STEPS, PRE_STEPS

MODEL_SCHEMA = 1
_REQUIRED = ("schema", "feature_schema", "input_dim", "target_hz", "window_pre_steps",
             "window_post_steps", "stroke_types", "hidden", "levels", "unknown_threshold")


class StrokeModelError(Exception):
    """The stroke-model weights or sidecar are missing or incompatible."""


class StrokeNet(nn.Module):
    def __init__(self, in_ch, n_classes=len(STROKE_TYPES), hidden=64, levels=3, dropout=0.1):
        super().__init__()
        self.register_buffer("feat_mean", torch.zeros(in_ch))
        self.register_buffer("feat_std", torch.ones(in_ch))
        self.inp = nn.Conv1d(in_ch, hidden, 1)
        self.blocks = nn.Sequential(*[_Block(hidden, 2 ** i, dropout) for i in range(levels)])
        self.drop = nn.Dropout(dropout)
        self.head = nn.Linear(2 * hidden, n_classes)

    def forward(self, x):
        x = (x - self.feat_mean) / self.feat_std.clamp_min(1e-6)
        h = self.blocks(torch.relu(self.inp(x.transpose(1, 2))))
        pooled = torch.cat([h.mean(dim=2), h.amax(dim=2)], dim=1)
        return self.head(self.drop(pooled))


def base_meta(hidden, levels, unknown_threshold):
    return {"schema": MODEL_SCHEMA, "feature_schema": FEATURE_SCHEMA, "input_dim": INPUT_DIM,
            "target_hz": TARGET_HZ, "window_pre_steps": PRE_STEPS,
            "window_post_steps": POST_STEPS, "stroke_types": list(STROKE_TYPES),
            "hidden": hidden, "levels": levels, "unknown_threshold": float(unknown_threshold),
            "validated_on_owner_footage": False}


def save_stroke_model(model, meta, path):
    torch.save(model.state_dict(), str(path))
    with open(meta_path(path), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)


def load_stroke_model(path, device="cpu"):
    sidecar = meta_path(path)
    if not os.path.isfile(sidecar):
        raise StrokeModelError(f"stroke-model sidecar not found: {sidecar}")
    with open(sidecar, encoding="utf-8") as fh:
        meta = json.load(fh)
    missing = [k for k in _REQUIRED if k not in meta]
    if missing:
        raise StrokeModelError(f"stroke-model sidecar lacks {missing}")
    expected = base_meta(meta["hidden"], meta["levels"], meta["unknown_threshold"])
    for key in ("schema", "feature_schema", "input_dim", "target_hz", "window_pre_steps",
                "window_post_steps", "stroke_types"):
        if meta[key] != expected[key]:
            raise StrokeModelError(f"incompatible stroke model: {key} is {meta[key]!r}, "
                                   f"this code needs {expected[key]!r}")
    model = StrokeNet(INPUT_DIM, hidden=int(meta["hidden"]), levels=int(meta["levels"]), dropout=0.0)
    try:
        model.load_state_dict(torch.load(str(path), map_location=device, weights_only=True))
    except Exception as exc:
        raise StrokeModelError(f"stroke-model weights unreadable: {exc}") from exc
    return model.to(device).eval(), meta


def predict_probs(model, X, batch=256):
    X = np.asarray(X, np.float32)
    if len(X) == 0:
        return np.zeros((0, len(STROKE_TYPES)), np.float64)
    device = next(model.parameters()).device
    model.eval()
    out = []
    with torch.no_grad():
        for s in range(0, len(X), batch):
            logits = model(torch.from_numpy(X[s:s + batch]).to(device))
            out.append(torch.softmax(logits, dim=1).cpu().numpy())
    return np.concatenate(out).astype(np.float64)
```

- [ ] **Step 4: Run the tests to verify they pass.** Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/stroke_model.py tests/test_stroke_model.py
git commit -m "feat(stroke): stroke classifier network with validated sidecar"
```

---

### Task 5: Metrics, bar check and threshold choice

**Files:**
- Create: `badminton_analysis/stroke/stroke_metrics.py`
- Test: `tests/test_stroke_metrics.py`

**Interfaces:**
- Consumes: `stroke_types`.
- Produces:
  - `BAR = {"accuracy": 0.85, "recall": 0.75, "unknown": 0.15}`, `MIN_GROUP_HITS = 20`;
  - `score(pairs) -> dict`. `pairs` is a list of `(truth_type, predicted)`, where `predicted` is
    a type or `"unknown"`. The dict has keys `matched`, `confident`, `unknown_rate`, `accuracy`,
    `type_accuracy`, `recall` (`{group: {"n", "correct", "recall"}}`) and `confusion`
    (`{truth_type: {pred: n}}`);
  - `check_bar(metrics) -> (passed: bool, reasons: list[str])`;
  - `predict_labels(probs, threshold) -> list[str]`;
  - `choose_threshold(probs, truth_types, max_unknown=0.15) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_metrics.py
import numpy as np
import pytest

from badminton_analysis.stroke import stroke_metrics as sm
from badminton_analysis.stroke.stroke_types import GROUPS, STROKE_TYPES


def test_score_counts_groups_and_unknown_as_miss():
    pairs = [("smash", "smash"), ("smash", "unknown"), ("net_shot", "block"),
             ("push", "drive"), ("clear", "lift")]
    m = sm.score(pairs)
    assert m["matched"] == 5 and m["confident"] == 4
    assert m["unknown_rate"] == pytest.approx(0.2)
    assert m["accuracy"] == pytest.approx(3 / 4)          # block->net and push->drive count
    assert m["type_accuracy"] == pytest.approx(1 / 4)
    assert m["recall"]["smash"] == {"n": 2, "correct": 1, "recall": 0.5}
    assert m["recall"]["net"]["recall"] == 1.0 and m["recall"]["clear"]["recall"] == 0.0
    assert m["recall"]["serve"] == {"n": 0, "correct": 0, "recall": None}
    assert m["confusion"]["net_shot"] == {"block": 1}


def test_score_empty():
    m = sm.score([])
    assert m["matched"] == 0 and m["accuracy"] is None and m["unknown_rate"] is None


def test_check_bar():
    good = [(t, t) for t in STROKE_TYPES for _ in range(20)]
    ok, reasons = sm.check_bar(sm.score(good))
    assert ok and reasons == []
    # 60 unknown pushes: unknown rate 60/300 = 0.2 > 0.15, drive recall 40/100 = 0.4 < 0.75
    bad = good + [("push", "unknown")] * 60
    ok, reasons = sm.check_bar(sm.score(bad))
    assert not ok and any("unknown" in r for r in reasons) and any(r.startswith("drive") for r in reasons)
    few = [("smash", "smash")] * 5
    ok, reasons = sm.check_bar(sm.score(few))
    assert not ok and any("fewer than 20" in r for r in reasons)


def test_predict_labels_threshold():
    probs = np.zeros((2, 12))
    probs[0, 4] = 0.9
    probs[1, 0] = 0.3
    assert sm.predict_labels(probs, 0.5) == ["smash", "unknown"]


def test_choose_threshold_respects_unknown_cap():
    rng = np.random.default_rng(0)
    truth = [STROKE_TYPES[i % 12] for i in range(240)]
    probs = rng.dirichlet(np.ones(12), size=240)
    for k, t in enumerate(truth):
        probs[k] *= 0.3
        probs[k, STROKE_TYPES.index(t)] += 0.7 * (k % 3 != 0)
        probs[k] /= probs[k].sum()
    thr = sm.choose_threshold(probs, truth, max_unknown=0.15)
    labels = sm.predict_labels(probs, thr)
    assert labels.count("unknown") / len(labels) <= 0.15
    assert 0.0 <= thr < 1.0
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/stroke/stroke_metrics.py
"""Stroke-classification metrics and the owner's done bar (spec 2026-10-22 section 6.2).

Accuracy is over confident predictions at the 7-group level; recall per group counts an
"unknown" prediction as a miss, so abstaining cannot raise it; the unknown rate is over all
matched hits. 12-type accuracy and the confusion matrix are informational.
"""
from collections import defaultdict

import numpy as np

from .stroke_types import GROUPS, STROKE_TYPES, UNKNOWN, group_of

BAR = {"accuracy": 0.85, "recall": 0.75, "unknown": 0.15}
MIN_GROUP_HITS = 20


def score(pairs):
    pairs = list(pairs)
    confident = [(t, p) for t, p in pairs if p != UNKNOWN]
    recall = {}
    for g in GROUPS:
        rows = [(t, p) for t, p in pairs if group_of(t) == g]
        correct = sum(1 for t, p in rows if group_of(p) == g)
        recall[g] = {"n": len(rows), "correct": correct,
                     "recall": round(correct / len(rows), 4) if rows else None}
    confusion = defaultdict(lambda: defaultdict(int))
    for t, p in pairs:
        if p != t:
            confusion[t][p] += 1
    return {
        "matched": len(pairs),
        "confident": len(confident),
        "unknown_rate": round((len(pairs) - len(confident)) / len(pairs), 4) if pairs else None,
        "accuracy": (round(sum(group_of(t) == group_of(p) for t, p in confident) / len(confident), 4)
                     if confident else None),
        "type_accuracy": (round(sum(t == p for t, p in confident) / len(confident), 4)
                          if confident else None),
        "recall": recall,
        "confusion": {t: dict(v) for t, v in confusion.items()},
    }


def check_bar(m):
    reasons = []
    if m["accuracy"] is None or m["accuracy"] < BAR["accuracy"]:
        reasons.append(f"accuracy {m['accuracy']} < {BAR['accuracy']}")
    if m["unknown_rate"] is None or m["unknown_rate"] > BAR["unknown"]:
        reasons.append(f"unknown rate {m['unknown_rate']} > {BAR['unknown']}")
    for g, r in m["recall"].items():
        if r["n"] < MIN_GROUP_HITS:
            reasons.append(f"{g}: fewer than {MIN_GROUP_HITS} test hits ({r['n']})")
        elif r["recall"] < BAR["recall"]:
            reasons.append(f"{g}: recall {r['recall']} < {BAR['recall']}")
    return not reasons, reasons


def predict_labels(probs, threshold):
    probs = np.asarray(probs)
    if len(probs) == 0:
        return []
    best = probs.argmax(axis=1)
    return [STROKE_TYPES[b] if probs[k, b] >= threshold else UNKNOWN for k, b in enumerate(best)]


def choose_threshold(probs, truth_types, max_unknown=BAR["unknown"]):
    """Threshold maximising the worst group's recall, then accuracy, with unknown <= cap."""
    best_key, best_thr = None, 0.0
    for thr in np.round(np.arange(0.0, 0.96, 0.01), 2):
        m = score(zip(truth_types, predict_labels(probs, thr)))
        if m["unknown_rate"] is None or m["unknown_rate"] > max_unknown:
            continue
        recalls = [r["recall"] for r in m["recall"].values() if r["recall"] is not None]
        key = (min(recalls) if recalls else 0.0, m["accuracy"] or 0.0, -thr)
        if best_key is None or key > best_key:
            best_key, best_thr = key, float(thr)
    return best_thr
```

- [ ] **Step 4: Run the tests to verify they pass.** Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/stroke_metrics.py tests/test_stroke_metrics.py
git commit -m "feat(stroke): stroke metrics, owner bar check, unknown threshold"
```

---

### Task 6: Classifier API and schema-2 payload

**Files:**
- Create: `badminton_analysis/stroke/stroke_classifier.py`
- Test: `tests/test_stroke_classifier.py`

**Interfaces:**
- Consumes: `stroke_windows.extract`, `stroke_model.load_stroke_model/predict_probs/file_sha256`,
  `stroke_types`.
- Produces:
  - `STROKES_SCHEMA = 2`;
  - `load_classifier(path) -> (model, meta, info)`. `info = {"version": str, "weights_sha256": str, "validated_on_owner_footage": bool, "unknown_threshold": float}`.
    It raises `StrokeModelError`.
  - `classify_hits(sig, hits, model, meta) -> list[label]`. Each label is
    `{"frame": int, "hitter": str, "stroke": str, "group": str|None, "confidence": float|None}`,
    plus `"hit_confidence"` when the hit carried a `confidence`. Labels are sorted by frame.
  - `unknown_labels(hits) -> list[label]` (confidence `None`);
  - `build_payload(labels, info, hit_source, shuttle_source) -> dict`;
  - `is_current(payload) -> bool` (`schema == 2`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_classifier.py
import numpy as np
import pytest
import torch

from badminton_analysis.stroke import stroke_classifier as sc
from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.stroke_windows import INPUT_DIM
from tests.test_stroke_windows import _sig


def _model_biased_to(index, tmp_path, threshold=0.5):
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1).eval()
    with torch.no_grad():
        m.head.weight.zero_()
        m.head.bias.zero_()
        m.head.bias[index] = 10.0
    path = tmp_path / "stroke-classifier.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, threshold), path)
    return path


def test_classify_hits_labels_and_order(tmp_path):
    model, meta, info = sc.load_classifier(_model_biased_to(4, tmp_path))   # smash
    hits = [{"frame": 200, "hitter": "upper", "confidence": 0.8}, {"frame": 120, "hitter": "lower"}]
    labels = sc.classify_hits(_sig(), hits, model, meta)
    assert [l["frame"] for l in labels] == [120, 200]
    assert all(l["stroke"] == "smash" and l["group"] == "smash" for l in labels)
    assert labels[1]["hit_confidence"] == 0.8 and "hit_confidence" not in labels[0]
    assert 0.99 < labels[0]["confidence"] <= 1.0
    assert info["validated_on_owner_footage"] is False and len(info["weights_sha256"]) == 64


def test_below_threshold_is_unknown(tmp_path):
    model, meta, _ = sc.load_classifier(_model_biased_to(4, tmp_path, threshold=1.01))
    labels = sc.classify_hits(_sig(), [{"frame": 150, "hitter": "lower"}], model, meta)
    assert labels[0]["stroke"] == "unknown" and labels[0]["group"] is None
    assert labels[0]["confidence"] > 0.99


def test_no_signal_is_unknown_with_zero_confidence(tmp_path):
    model, meta, _ = sc.load_classifier(_model_biased_to(4, tmp_path))
    labels = sc.classify_hits(_sig(shuttle=False), [{"frame": 150, "hitter": "lower"}], model, meta)
    assert labels[0] == {"frame": 150, "hitter": "lower", "stroke": "unknown", "group": None,
                         "confidence": 0.0}


def test_unknown_labels_and_payload():
    labels = sc.unknown_labels([{"frame": 9, "hitter": "lower", "confidence": 0.7}])
    assert labels == [{"frame": 9, "hitter": "lower", "stroke": "unknown", "group": None,
                       "confidence": None, "hit_confidence": 0.7}]
    p = sc.build_payload(labels, None, "learned", "tracknet")
    assert p["schema"] == 2 and p["classifier"] is None
    assert p["distribution"] == {"unknown": 1} and p["groups"] == {}
    assert p["hit_source"] == "learned" and p["shuttle_source"] == "tracknet"
    assert sc.is_current(p) and not sc.is_current({"strokes": []}) and not sc.is_current(None)


def test_payload_counts_types_and_groups():
    labels = [{"frame": 1, "hitter": "lower", "stroke": "push", "group": "drive", "confidence": 0.9},
              {"frame": 2, "hitter": "upper", "stroke": "drive", "group": "drive", "confidence": 0.9}]
    p = sc.build_payload(labels, {"version": "v"}, "contact_rule", "yolo")
    assert p["distribution"] == {"push": 1, "drive": 1} and p["groups"] == {"drive": 2}


def test_load_classifier_missing_raises(tmp_path):
    with pytest.raises(smod.StrokeModelError):
        sc.load_classifier(tmp_path / "nope.pt")
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

```python
# badminton_analysis/stroke/stroke_classifier.py
"""The app's one stroke-identification mechanism (spec 2026-10-22 section 4.2).

classify_hits labels every hit of the shared hit list with a stroke type or "unknown";
build_payload makes the schema-2 strokes.json every consumer reads. There is no fallback
classifier: without a usable model every hit is "unknown" and classifier is null.
"""
from collections import Counter

from .stroke_model import StrokeModelError, file_sha256, load_stroke_model, predict_probs  # noqa: F401
from .stroke_types import STROKE_TYPES, UNKNOWN, group_of
from .stroke_windows import extract

STROKES_SCHEMA = 2


def load_classifier(path):
    model, meta = load_stroke_model(path)
    info = {"version": meta.get("created_utc") or "unversioned",
            "weights_sha256": file_sha256(path),
            "validated_on_owner_footage": bool(meta.get("validated_on_owner_footage")),
            "unknown_threshold": float(meta["unknown_threshold"])}
    return model, meta, info


def _label(hit, stroke, confidence):
    out = {"frame": int(hit["frame"]), "hitter": hit["hitter"], "stroke": stroke,
           "group": group_of(stroke), "confidence": confidence}
    if hit.get("confidence") is not None:
        out["hit_confidence"] = float(hit["confidence"])
    return out


def unknown_labels(hits):
    return sorted((_label(h, UNKNOWN, None) for h in hits), key=lambda l: l["frame"])


def classify_hits(sig, hits, model, meta):
    hits = list(hits)
    X, ok = extract(sig, hits)
    probs = predict_probs(model, X[ok]) if ok.any() else None
    thr = float(meta["unknown_threshold"])
    labels, j = [], 0
    for k, h in enumerate(hits):
        if not ok[k]:
            labels.append(_label(h, UNKNOWN, 0.0))
            continue
        p = probs[j]
        j += 1
        best = int(p.argmax())
        conf = round(float(p[best]), 4)
        labels.append(_label(h, STROKE_TYPES[best] if conf >= thr else UNKNOWN, conf))
    return sorted(labels, key=lambda l: l["frame"])


def build_payload(labels, info, hit_source, shuttle_source):
    return {"schema": STROKES_SCHEMA, "classifier": info, "strokes": labels,
            "distribution": dict(Counter(l["stroke"] for l in labels)),
            "groups": dict(Counter(l["group"] for l in labels if l["group"])),
            "hit_source": hit_source, "shuttle_source": shuttle_source}


def is_current(payload):
    return isinstance(payload, dict) and payload.get("schema") == STROKES_SCHEMA
```

- [ ] **Step 4: Run the tests to verify they pass.** Expected: 6 passed. (`tests/` must be
  importable as a package for `from tests.test_stroke_windows import _sig`. If it isn't, check
  for `tests/__init__.py`. If it's missing, move `_sig` into `tests/stroke_fixtures.py` and import
  it from there in both test files.)

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/stroke/stroke_classifier.py tests/test_stroke_classifier.py
git commit -m "feat(stroke): classify_hits and the schema-2 strokes.json payload"
```

---

### Task 7: Training script

**Files:**
- Create: `scripts/train_stroke_classifier.py`
- Test: `tests/test_stroke_training.py`

**Interfaces:**
- Consumes:
  - `stroke_types.type_of_raw`, `STROKE_TYPES`;
  - `stroke_labels.parse_sheet`, `labelled_hits`;
  - `stroke_windows.extract`;
  - `stroke_model.*`;
  - `stroke_metrics.score`, `choose_threshold`, `predict_labels`;
  - `hit_augment.augment`, `jittered_index`; `hit_signals.load_signals`, `RawSignals`.
- Produces:
  - `Rally = namedtuple("Rally", "sig hits match")`, where `hits = [{"frame", "hitter", "stroke"}]`;
  - `load_shuttleset(dataset_dir) -> list[Rally]`;
  - `load_dji(sheet_path, results_dir, split="train") -> list[Rally]`;
  - `train(train, val, epochs, hidden, levels, seed, device, log) -> (model, meta)`;
  - CLI `--dataset DIR` (repeatable), `--val-match NAME`, `--dji-labels PATH`, `--results-dir DIR`,
    `--calib-video NAME` (repeatable: DJI train-split videos held out of gradient steps and used
    with the val match for threshold tuning), `--epochs`, `--device`, `--out`.
  - The CLI refuses to load test-split videos.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_training.py
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import train_stroke_classifier as tsc  # noqa: E402

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke import stroke_model as smod  # noqa: E402
from badminton_analysis.stroke.hit_signals import save_signals  # noqa: E402
from tests.test_stroke_windows import _sig  # noqa: E402


def _npz(path, match, raw):
    s = _sig()
    np.savez_compressed(path, fps=s.fps, first_frame=s.first_frame, shuttle=s.shuttle,
                        keypoints=s.keypoints, court_corners=s.court_corners,
                        image_size=np.array(s.image_size), match=match, set=1, rally=1,
                        hit_frames=np.array([60, 120, 180]), hitter=np.array([0, 1, -1]),
                        stroke7=np.array([0, 0, 0]), stroke_raw=np.array(raw))


def test_load_shuttleset_maps_raw_and_drops_unusable(tmp_path):
    d = tmp_path / "ds" / "M1"
    d.mkdir(parents=True)
    _npz(d / "s1_r1.npz", "M1", ["殺球", "未知球種", "推球"])
    (d / "s1_r2.part.npz").write_bytes(b"")
    rallies = tsc.load_shuttleset(str(tmp_path / "ds"))
    assert len(rallies) == 1 and rallies[0].match == "M1"
    # 未知球種 is dropped; hitter -1 is dropped
    assert rallies[0].hits == [{"frame": 60, "hitter": "lower", "stroke": "smash"}]


def test_load_dji_train_split_only(tmp_path):
    res = tmp_path / "res"
    for name in ("A", "B"):
        (res / name).mkdir(parents=True)
        save_signals(res / name / "hit_signals.npz", _sig())
    text = sl.write_sheet([
        {"name": "A", "split": "train", "fps": 30.0, "clip": "A.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"}]},
        {"name": "B", "split": "test", "fps": 30.0, "clip": "B.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"}]}]).replace("|  | 61 |", "| lift | 61 |")
    sheet = tmp_path / "LABELS.md"
    sheet.write_text(text, encoding="utf-8")
    rallies = tsc.load_dji(str(sheet), str(res))
    assert [r.match for r in rallies] == ["dji:A"]
    assert rallies[0].hits == [{"frame": 61, "hitter": "lower", "stroke": "lift"}]
    with pytest.raises(ValueError, match="test"):
        tsc.load_dji(str(sheet), str(res), split="test")


def test_train_smoke_writes_loadable_model(tmp_path):
    rallies = [tsc.Rally(_sig(), [{"frame": f, "hitter": "lower", "stroke": s}
                                  for f, s in ((60, "smash"), (120, "lift"), (180, "net_shot"))],
                         f"M{k}") for k in range(3)]
    model, meta = tsc.train(rallies[:2], rallies[2:], epochs=2, hidden=8, levels=1, seed=0,
                            device="cpu", log=lambda *_: None)
    path = tmp_path / "s.pt"
    smod.save_stroke_model(model, meta, path)
    _, loaded = smod.load_stroke_model(path)
    assert 0.0 <= loaded["unknown_threshold"] < 1.0
    assert loaded["train_counts"]["smash"] == 2 and loaded["val_matches"] == ["M2"]
    assert "val_metrics" in loaded and loaded["validated_on_owner_footage"] is False
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement `scripts/train_stroke_classifier.py`**

```python
"""Train the stroke classifier (spec 2026-10-22 section 4.1).

Usage:
  python scripts/train_stroke_classifier.py --dataset data/hit_dataset \
      --val-match Pusarla_V._Sindhu_Pornpawee_Chochuwong_HSBC_BWF_WORLD_TOUR_FINALS_2020_QuarterFinals \
      [--dji-labels outputs/dji-stroke-labels/LABELS.md --results-dir outputs [--calib-video NAME]] \
      [--epochs 60] [--device cuda] [--out weights/stroke-classifier.pt]

Training rallies come from the hit dataset (ShuttleSet stroke_raw mapped to the 12 types)
plus the DJI sheet's train split. The DJI test split is never loaded. The unknown
threshold is tuned on the validation match plus any --calib-video DJI videos, which are
held out of training.
"""
import argparse
import glob
import os
import sys
import time
from collections import Counter, namedtuple

import numpy as np
import torch
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke import stroke_model as smod  # noqa: E402
from badminton_analysis.stroke.hit_augment import augment, jittered_index  # noqa: E402
from badminton_analysis.stroke.hit_signals import RawSignals, load_signals  # noqa: E402
from badminton_analysis.stroke.stroke_metrics import (  # noqa: E402
    choose_threshold, predict_labels, score)
from badminton_analysis.stroke.stroke_types import STROKE_TYPES, type_of_raw  # noqa: E402
from badminton_analysis.stroke.stroke_windows import INPUT_DIM, extract  # noqa: E402

Rally = namedtuple("Rally", "sig hits match")
_SIDES = {0: "lower", 1: "upper"}


def load_shuttleset(dataset_dir):
    rallies = []
    for path in sorted(glob.glob(os.path.join(dataset_dir, "*", "*.npz"))):
        if path.endswith(".part.npz"):
            continue
        with np.load(path) as z:
            if "stroke_raw" not in z.files:
                continue
            sig = RawSignals(float(z["fps"]), int(z["first_frame"]), z["shuttle"].astype(np.float32),
                             z["keypoints"].astype(np.float32), z["court_corners"].astype(np.float32),
                             tuple(int(v) for v in z["image_size"]))
            hits = []
            for f, who, raw in zip(z["hit_frames"], z["hitter"], z["stroke_raw"]):
                stroke = type_of_raw(str(raw))
                if stroke is None or int(who) not in _SIDES:
                    continue
                hits.append({"frame": int(f), "hitter": _SIDES[int(who)], "stroke": stroke})
            if hits:
                rallies.append(Rally(sig, hits, str(z["match"])))
    return rallies


def load_dji(sheet_path, results_dir, split="train", only=None):
    if split != "train":
        raise ValueError("the DJI test split is scored by score_strokes.py, never trained on")
    with open(sheet_path, encoding="utf-8") as fh:
        sheet = sl.parse_sheet(fh.read())
    rallies = []
    for name, entry in sheet.items():
        if entry["split"] != "train" or (only is not None and name not in only):
            continue
        hits = [{"frame": h["frame"], "hitter": h["hitter"], "stroke": h["stroke"]}
                for h in sl.labelled_hits(entry) if h["hitter"] and h["frame"]]
        if hits:
            sig = load_signals(os.path.join(results_dir, name, "hit_signals.npz"))
            rallies.append(Rally(sig, hits, f"dji:{name}"))
    return rallies


def _windows(rally, rng=None):
    sig, idx = rally.sig, None
    if rng is not None:
        sig = augment(sig, rng)
        idx = jittered_index(sig.n_frames, sig.fps, rng)
    X, ok = extract(sig, rally.hits, idx)
    y = np.array([STROKE_TYPES.index(h["stroke"]) for h in rally.hits], np.int64)
    return X[ok], y[ok]


def _stack(rallies, rng=None):
    parts = [_windows(r, rng) for r in rallies]
    X = np.concatenate([p[0] for p in parts]) if parts else np.zeros((0, 1, INPUT_DIM), np.float32)
    y = np.concatenate([p[1] for p in parts]) if parts else np.zeros(0, np.int64)
    return X, y


def _evaluate(model, X, y, threshold):
    probs = smod.predict_probs(model, X)
    truth = [STROKE_TYPES[i] for i in y]
    return probs, truth, score(zip(truth, predict_labels(probs, threshold)))


def train(train_rallies, val_rallies, epochs=60, hidden=64, levels=3, batch=64, lr=1e-3,
          seed=0, device=None, log=print):
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    clean_X, clean_y = _stack(train_rallies)
    val_X, val_y = _stack(val_rallies)
    model = smod.StrokeNet(INPUT_DIM, hidden=hidden, levels=levels).to(device)
    flat = clean_X.reshape(-1, INPUT_DIM)
    model.feat_mean.copy_(torch.from_numpy(flat.mean(0)))
    model.feat_std.copy_(torch.from_numpy(flat.std(0) + 1e-3))
    counts = np.bincount(clean_y, minlength=len(STROKE_TYPES)).astype(np.float64)
    weight = torch.tensor(1.0 / np.sqrt(np.maximum(counts, 1.0)), dtype=torch.float32, device=device)
    weight = weight / weight.mean()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, epochs))
    best, best_state = None, None
    for epoch in range(1, epochs + 1):
        model.train()
        X, y = _stack(train_rallies, rng)            # fresh augmentation every epoch
        order = rng.permutation(len(X))
        losses = []
        for s in range(0, len(order), batch):
            b = order[s:s + batch]
            logits = model(torch.from_numpy(X[b]).to(device))
            loss = F.cross_entropy(logits, torch.from_numpy(y[b]).to(device), weight=weight)
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss))
        sched.step()
        model.eval()
        _, _, m = _evaluate(model, val_X, val_y, 0.0)
        recalls = [r["recall"] for r in m["recall"].values() if r["recall"] is not None]
        key = (min(recalls) if recalls else 0.0, m["accuracy"] or 0.0)
        log(f"epoch {epoch}: loss {np.mean(losses) if losses else float('nan'):.4f} "
            f"val group acc {m['accuracy']} worst recall {key[0]:.3f}")
        if best is None or key > best:
            best, best_state = key, {k: v.detach().clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()
    probs, truth, _ = _evaluate(model, val_X, val_y, 0.0)
    threshold = choose_threshold(probs, truth) if len(truth) else 0.5
    _, _, val_metrics = _evaluate(model, val_X, val_y, threshold)
    meta = smod.base_meta(hidden, levels, threshold)
    meta.update({
        "train_matches": sorted({r.match for r in train_rallies}),
        "val_matches": sorted({r.match for r in val_rallies}),
        "train_counts": {STROKE_TYPES[i]: int(c) for i, c in enumerate(counts)},
        "val_metrics": val_metrics, "epochs": epochs, "seed": seed,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    return model.cpu(), meta


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dataset", action="append", default=[])
    p.add_argument("--val-match", required=True)
    p.add_argument("--dji-labels")
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--calib-video", action="append", default=[])
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--device")
    p.add_argument("--out", default=os.path.join(ROOT, "weights", "stroke-classifier.pt"))
    a = p.parse_args(argv)
    shuttle = [r for d in a.dataset for r in load_shuttleset(d)]
    dji = load_dji(a.dji_labels, a.results_dir) if a.dji_labels else []
    calib = {f"dji:{v}" for v in a.calib_video}
    train_r = [r for r in shuttle if r.match != a.val_match] + [r for r in dji if r.match not in calib]
    val_r = [r for r in shuttle if r.match == a.val_match] + [r for r in dji if r.match in calib]
    if not train_r or not val_r:
        raise SystemExit(f"need training and validation data (val match {a.val_match!r})")
    print(f"{sum(len(r.hits) for r in train_r)} training hits, {sum(len(r.hits) for r in val_r)} validation hits")
    model, meta = train(train_r, val_r, epochs=a.epochs, device=a.device)
    smod.save_stroke_model(model, meta, a.out)
    print(f"saved {a.out}: threshold {meta['unknown_threshold']}, val {meta['val_metrics']['accuracy']} "
          f"group accuracy, unknown {meta['val_metrics']['unknown_rate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass.** Expected: 3 passed (the smoke test trains on the
  CPU in a few seconds).

- [ ] **Step 5: Commit**

```bash
git add scripts/train_stroke_classifier.py tests/test_stroke_training.py
git commit -m "feat(stroke): stroke classifier training script"
```

---

### Task 8: Scoring script

**Files:**
- Create: `scripts/score_strokes.py`
- Test: `tests/test_score_strokes.py`

**Interfaces:**
- Consumes: `stroke_labels.parse_sheet`, `labelled_hits`; `stroke_metrics.score`, `check_bar`;
  `stroke_classifier.is_current`.
- Produces:
  - `match(labelled, predicted, fps, tol=0.2) -> (pairs, missed)`. `labelled` is from
    `labelled_hits`; `predicted` is `strokes.json["strokes"]`. Each label is matched to the
    nearest unused prediction within `tol` seconds, preferring the same hitter.
  - CLI `--labels`, `--results-dir`, `--split test|train`, `--tol`, `--json`. It prints the
    metrics and `PASS`/`FAIL` with reasons, exits 0 on pass and 1 on fail, and exits 2 for a
    stale or missing `strokes.json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_score_strokes.py
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import score_strokes as ss  # noqa: E402

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402


def test_match_prefers_same_hitter_and_reports_missed():
    labelled = [{"t": 1.0, "frame": 31, "hitter": "lower", "stroke": "smash"},
                {"t": 5.0, "frame": 151, "hitter": "upper", "stroke": "lift"}]
    predicted = [{"frame": 32, "hitter": "upper", "stroke": "clear"},
                 {"frame": 33, "hitter": "lower", "stroke": "smash"}]
    pairs, missed = ss.match(labelled, predicted, fps=30.0)
    assert pairs == [("smash", "smash")] and missed == 1


def _result(tmp_path, name, strokes, schema=2):
    d = tmp_path / "res" / name
    d.mkdir(parents=True)
    (d / "strokes.json").write_text(json.dumps({"schema": schema, "strokes": strokes}), encoding="utf-8")
    (d / "hits.json").write_text(json.dumps({"fps": 30.0, "hits": []}), encoding="utf-8")


def _sheet(tmp_path, stroke="smash"):
    text = sl.write_sheet([{"name": "V", "split": "test", "fps": 30.0, "clip": "V.mp4",
                            "hits": [{"frame": 31, "time_sec": 1.0, "hitter": "lower"}]}])
    text = text.replace("|  | 31 |", f"| {stroke} | 31 |")
    p = tmp_path / "LABELS.md"
    p.write_text(text, encoding="utf-8")
    return p


def _run(tmp_path, *extra):
    return subprocess.run([sys.executable, "scripts/score_strokes.py", "--labels", str(_sheet(tmp_path)),
                           "--results-dir", str(tmp_path / "res"), *extra],
                          capture_output=True, text=True, cwd=ROOT)


def test_cli_fails_bar_on_tiny_set(tmp_path):
    _result(tmp_path, "V", [{"frame": 31, "hitter": "lower", "stroke": "smash"}])
    out = tmp_path / "m.json"
    r = _run(tmp_path, "--json", str(out))
    assert r.returncode == 1 and "FAIL" in r.stdout and "fewer than 20" in r.stdout
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["metrics"]["accuracy"] == 1.0 and m["passed"] is False


def test_cli_rejects_stale_strokes(tmp_path):
    _result(tmp_path, "V", [{"frame": 31, "hitter": "lower", "stroke": "net"}], schema=None)
    r = _run(tmp_path)
    assert r.returncode == 2 and "re-label" in (r.stdout + r.stderr)
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

```python
"""Score strokes.json against the DJI stroke-label sheet (spec 2026-10-22 section 6.2).

Usage:
  python scripts/score_strokes.py --labels outputs/dji-stroke-labels/LABELS.md \
      [--results-dir outputs] [--split test] [--tol 0.2] [--json result.json]

Exit 0 = the owner's bar is met, 1 = not met, 2 = a result's strokes.json is missing or
from the old classifier (re-label it first).
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke.stroke_classifier import is_current  # noqa: E402
from badminton_analysis.stroke.stroke_metrics import check_bar, score  # noqa: E402


def match(labelled, predicted, fps, tol=0.2):
    used, pairs, missed = set(), [], 0
    for lab in labelled:
        best = None
        for k, p in enumerate(predicted):
            if k in used:
                continue
            dt = abs((int(p["frame"]) - int(lab["frame"])) / fps)
            if dt > tol:
                continue
            key = (p.get("hitter") != lab["hitter"], dt)
            if best is None or key < best[0]:
                best = (key, k)
        if best is None:
            missed += 1
            continue
        used.add(best[1])
        pairs.append((lab["stroke"], predicted[best[1]]["stroke"]))
    return pairs, missed


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--labels", required=True)
    p.add_argument("--results-dir", default=os.path.join(ROOT, "outputs"))
    p.add_argument("--split", default="test", choices=["test", "train"])
    p.add_argument("--tol", type=float, default=0.2)
    p.add_argument("--json")
    a = p.parse_args(argv)
    with open(a.labels, encoding="utf-8") as fh:
        sheet = sl.parse_sheet(fh.read())
    pairs, missed, videos = [], 0, []
    for name, entry in sheet.items():
        if entry["split"] != a.split:
            continue
        path = os.path.join(a.results_dir, name, "strokes.json")
        try:
            with open(path, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, ValueError):
            payload = None
        if not is_current(payload):
            print(f"{name}: strokes.json missing or from the old classifier; re-label it first")
            return 2
        with open(os.path.join(a.results_dir, name, "hits.json"), encoding="utf-8") as fh:
            fps = float(json.load(fh)["fps"])
        pv, mv = match(sl.labelled_hits(entry), payload["strokes"], fps, a.tol)
        pairs += pv
        missed += mv
        videos.append(name)
    metrics = score(pairs)
    passed, reasons = check_bar(metrics)
    print(f"videos ({a.split}): {', '.join(videos) or 'none'}")
    print(f"matched {metrics['matched']} labelled hits; {missed} not found by the hit detector")
    print(f"group accuracy {metrics['accuracy']}  unknown rate {metrics['unknown_rate']}  "
          f"12-type accuracy {metrics['type_accuracy']}")
    for g, r in metrics["recall"].items():
        print(f"  {g:<6} recall {r['recall']} ({r['correct']}/{r['n']})")
    print("PASS" if passed else "FAIL: " + "; ".join(reasons))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"split": a.split, "videos": videos, "missed_by_hit_detector": missed,
                       "metrics": metrics, "passed": passed, "reasons": reasons}, fh, indent=2)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass.** Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/score_strokes.py tests/test_score_strokes.py
git commit -m "feat(stroke): score strokes.json against the DJI label sheet"
```

---

### Task 9: Technique analysis takes the shared labels

**Files:**
- Modify: `badminton_analysis/analysis/biomechanics.py` (`analyze`, lines 63–145)
- Modify: `badminton_analysis/analysis/technique_writer.py` (`build_match_summary`)
- Modify: `badminton_analysis/system.py` (`TechniqueAnalysisRunner.run`, lines 2068–2105)
- Test: `tests/test_technique_labels.py` (new); update `tests/test_biomechanics.py`,
  `tests/test_technique_writer.py`, `tests/test_integration_analysis.py` where they assert the
  report shape.

**Interfaces:**
- Consumes: `stroke_types.profile_for`, `group_of`, `PROFILE_GROUP`, `UNKNOWN`.
- Produces:
  - The `BiomechanicalAnalyzer.analyze(event, frames)` report gains:
    - `"measured": {metric: value}`, always present;
    - `"profile": str|None`;
    - `"group": str|None`;
    - `"scored": bool`.

    When `profile` is None: `overall_score None`, `per_metric {}`, `weaknesses []`,
    `strengths []`.
  - Module function `rescore_report(report, stroke_type) -> dict` in `biomechanics.py`: the same
    report re-scored from `report["measured"]` (falling back to `per_metric[*]["measured"]`)
    under the new type's profile, using the 3D ranges when `report["feature_space"] == "3d"`.
  - `TechniqueAnalysisRunner.run(track, frame_lookup, hits=None, strokes=None)`, where
    `strokes: dict[int frame -> label]`. The stroke type is `label["stroke"]` (or `"unknown"` when
    the frame has no label), and the confidence is `label["confidence"]` (or None).
  - `build_match_summary(reports)`: `stroke_count` = all reports, plus a new `scored_count`;
    `by_type[type]["avg_score"]` only from scored reports; weaknesses and strengths only from
    scored reports.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_technique_labels.py
from badminton_analysis.analysis import biomechanics as bio
from badminton_analysis.analysis.technique_writer import build_match_summary
from badminton_analysis.stroke.events import StrokeEvent


def _report(stroke_type):
    a = bio.BiomechanicalAnalyzer()
    ev = StrokeEvent(stroke_type=stroke_type, contact_frame=10, window_start=0, window_end=20,
                     player_side="lower", confidence=0.9)
    return a.analyze(ev, [])          # no frames: metrics are all None, shape still complete


def test_profiled_type_is_scored_shape():
    r = _report("clear")
    assert r["stroke_type"] == "clear" and r["profile"] == "high_clear" and r["group"] == "clear"
    assert r["scored"] is True and set(r["per_metric"]) and "measured" in r


def test_unprofiled_type_is_labelled_not_scored():
    for t in ("lift", "net_shot", "push", "unknown"):
        r = _report(t)
        assert r["scored"] is False and r["profile"] is None
        assert r["overall_score"] is None and r["per_metric"] == {} and r["weaknesses"] == []
        assert "measured" in r


def test_drill_profile_names_still_score():
    r = _report("high_clear")
    assert r["scored"] is True and r["profile"] == "high_clear" and r["group"] == "clear"


def test_rescore_report_switches_profile():
    r = _report("lift")
    r["measured"] = {"elbow_extension": 150.0, "trunk_rotation": 30.0, "wrist_flexion": 90.0,
                     "knee_flexion": 156.0, "hip_shoulder_separation": 25.0, "weight_transfer": 0.35}
    r["feature_space"] = "2d"
    s = bio.rescore_report(r, "clear")
    assert s["scored"] is True and s["stroke_type"] == "clear" and s["overall_score"] == 100.0
    back = bio.rescore_report(s, "drive")
    assert back["scored"] is False and back["measured"] == r["measured"]


def test_summary_counts_only_scored_in_averages():
    reports = [{"stroke_type": "clear", "scored": True, "overall_score": 80.0,
                "weaknesses": [{"metric": "trunk_rotation"}], "strengths": []},
               {"stroke_type": "lift", "scored": False, "overall_score": None,
                "weaknesses": [], "strengths": []}]
    s = build_match_summary(reports)
    assert s["stroke_count"] == 2 and s["scored_count"] == 1
    assert s["by_type"]["lift"] == {"count": 1, "avg_score": None}
    assert s["by_type"]["clear"]["avg_score"] == 80.0


def test_runner_unknown_when_label_missing():
    from badminton_analysis.system import TechniqueAnalysisRunner
    runner = TechniqueAnalysisRunner(bio.BiomechanicalAnalyzer())
    hits = [{"frame": 30, "hitter": "lower"}, {"frame": 60, "hitter": "lower"}]
    strokes = {30: {"stroke": "smash", "confidence": 0.9}}
    reports, events = runner.run([], lambda f: {"player_side": "lower"}, hits=hits, strokes=strokes)
    assert [r["stroke_type"] for r in reports] == ["smash", "unknown"]
    assert reports[0]["scored"] is True and reports[1]["scored"] is False
    assert events[0].confidence == 0.9 and events[1].confidence is None
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

  1. **`biomechanics.py`:** add `from ..stroke.stroke_types import PROFILE_GROUP, group_of, profile_for`.
     Split `analyze` in two: `analyze` keeps the metric measurement (everything up to and including
     `metrics["weight_transfer"] = wt`) and then returns `build_report(...)`. The new module-level
     function holds the scoring half of today's `analyze` (`score_stroke`, the per-metric
     feature-space labels and the weaknesses list):

```python
def build_report(stroke_type, contact_frame, player_side, confidence, metrics,
                 feature_space, shadow_2d=None):
    """Technique report for one stroke. A type without a reference profile is labelled, not scored."""
    profile = profile_for(stroke_type)
    base = {"stroke_type": stroke_type, "profile": profile,
            "group": group_of(stroke_type) or PROFILE_GROUP.get(stroke_type),
            "contact_frame": contact_frame, "player_side": player_side,
            "confidence": confidence, "measured": dict(metrics), "feature_space": feature_space}
    if profile is None:
        return {**base, "scored": False, "overall_score": None, "per_metric": {},
                "weaknesses": [], "strengths": []}
    ranges = REFERENCE_RANGES_3D if feature_space == "3d" else REFERENCE_RANGES
    scored = score_stroke(metrics, profile, ranges=ranges)
    for name, entry in scored["per_metric"].items():
        if feature_space == "3d" and name in ja.METRICS_3D_CAPABLE:
            entry["feature_space"] = "3d"
            entry["measured_shadow_2d"] = (shadow_2d or {}).get(name)
        else:
            entry["feature_space"] = "2d"
    weaknesses = []
    for metric in scored["weaknesses"]:
        entry = scored["per_metric"][metric]
        weaknesses.append({
            "metric": metric, "measured": entry["measured"], "ideal_range": entry["ideal_range"],
            "direction": entry["direction"], "severity": entry["severity"],
            "description": _describe(metric, entry), "feature_space": entry.get("feature_space"),
            "measured_shadow_2d": entry.get("measured_shadow_2d")})
    return {**base, "scored": True, "overall_score": scored["overall"],
            "per_metric": scored["per_metric"], "weaknesses": weaknesses,
            "strengths": scored["strengths"]}


def rescore_report(report, stroke_type):
    metrics = report.get("measured")
    if not isinstance(metrics, dict):
        metrics = {k: v.get("measured") for k, v in (report.get("per_metric") or {}).items()}
    shadow = {k: v.get("measured_shadow_2d") for k, v in (report.get("per_metric") or {}).items()}
    return build_report(stroke_type, report.get("contact_frame"), report.get("player_side"),
                        report.get("confidence"), metrics, report.get("feature_space") or "2d", shadow)
```

     In `analyze`, replace everything from `scored = score_stroke(...)` to the end with
     `return build_report(stroke_event.stroke_type, stroke_event.contact_frame, stroke_event.player_side, stroke_event.confidence, metrics, "3d" if used_3d else "2d", metrics2d if used_3d else None)`.
     The 2D/3D range choice (`ranges = ...`) inside `analyze` is then dead; delete it. Check that
     `metrics2d` is defined on the 2D path too. If it isn't, pass `None` there.

  2. **`technique_writer.build_match_summary`:** count every report in `by_type_count`, but append
     to `by_type_scores` and count weaknesses and strengths only when `r.get("scored", True)` is
     truthy and the score is not None. Add `"scored_count": sum(1 for r in reports if r.get("scored", True) and r.get("overall_score") is not None)`.

  3. **`system.py` `TechniqueAnalysisRunner.run`:** change the signature to
     `run(self, track, frame_lookup, hits=None, strokes=None)`. Remove the import and call of
     `classify_stroke` and `_build_classifier_window` (delete the method). Per contact:

```python
            label = (strokes or {}).get(c["contact_frame"]) or {}
            stroke_type = label.get("stroke") or "unknown"
            conf = label.get("confidence")
```

     and build the `StrokeEvent` with `stroke_type=stroke_type, confidence=conf`.

- [ ] **Step 4: Run the new tests and the touched suites**

  Run: `pytest tests/test_technique_labels.py tests/test_biomechanics.py tests/test_biomechanics_3d.py tests/test_technique_writer.py tests/test_scoring.py tests/test_integration_analysis.py tests/test_report_3d.py`

  If `analyze(ev, [])` or frames without keypoints raise inside the metric measurement (a
  pre-existing limit, not part of this change), build the window in `_report` and in
  `test_runner_unknown_when_label_missing` from the minimal valid frame fixture that
  `tests/test_biomechanics.py` already uses, instead of changing the measurement code.

  Expected: the new tests pass. Existing tests that assert the exact report keys need the new
  keys (`profile`, `group`, `scored`, `measured`) added to their expected dicts. Update those
  assertions; do not weaken them. Tests that built a `StrokeEvent` with `high_clear` keep passing
  unchanged (drill names score as before).

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/analysis/biomechanics.py badminton_analysis/analysis/technique_writer.py badminton_analysis/system.py tests/
git commit -m "feat(technique): take stroke types from the shared labels; unprofiled types are unscored"
```

---

### Task 10: Pipeline wiring; remove BST and the rule classifier

**Files:**
- Modify: `badminton_analysis/system.py`:
  - constructor `bst_weights` → `stroke_model_path`, lines 293 and 335;
  - `process_video` post-loop, lines 539–546;
  - `_run_hit_detection`, lines 1553–1638;
  - `_run_technique_analysis`, line 1674;
  - replace `_run_stroke_recognition`, lines 1696–1772.
- Modify: `badminton_analysis/stroke/events.py` (add `hit_events`).
- Modify: `main.py` (lines 29–34, 79), `app.py` (`_bst_weights` lines 245–249, `/api/models`
  line 693, the command at lines 895–897).
- Delete:
  - `badminton_analysis/stroke_recog/` (whole package);
  - `badminton_analysis/stroke/classifier.py`;
  - the tests `tests/test_bst_classes.py`, `test_bst_inputs.py`, `test_bst_model.py`,
    `test_bst_recognizer.py`, `test_bst_integration.py`, `test_classifier.py`.
- Move: the tests in `tests/test_bst_hits.py` to `tests/test_hit_events.py`, importing
  `from badminton_analysis.stroke.events import hit_events`.
- Test: `tests/test_stroke_pipeline.py` (new); update `tests/test_hit_integration.py`,
  `tests/test_rally_segmentation.py`, `tests/test_doubles_system.py`, `tests/test_app_lift_flags.py`
  and any other test referencing `bst_weights`, `_run_stroke_recognition`, `StrokeRecognizer`,
  `--bst-model` or `'bst'`.

**Interfaces:**
- Consumes: `stroke_classifier.*` (Task 6); `TechniqueAnalysisRunner.run(..., strokes=)` (Task 9).
- Produces:
  - `BadmintonAnalysisSystem(..., stroke_model_path=None, ...)`;
  - attributes `self._hit_list` (the list written to `hits.json`, or None), `self._hit_signals`
    (`RawSignals` or None) and `self._hit_source` (`"learned"`/`"contact_rule"`/None), all
    initialised to None at the top of `_run_hit_detection`;
  - `_run_stroke_classification() -> list[label]` (`[]` when withheld or there are no hits);
  - `_run_technique_analysis(labels)`;
  - CLI `--stroke-model PATH`;
  - `app._stroke_weights(base=None) -> str|None` (`weights/stroke-classifier.pt` when both it and
    its sidecar exist);
  - `/api/models` key `"stroke"` (replacing `"bst"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stroke_pipeline.py
import json

from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.stroke_windows import INPUT_DIM
from tests.test_stroke_windows import _sig


def _bare(tmp_path, model_path=None, detection=None):
    from badminton_analysis.system import BadmintonAnalysisSystem
    s = BadmintonAnalysisSystem.__new__(BadmintonAnalysisSystem)
    s.save_dir = str(tmp_path)
    s.stroke_model_path = model_path
    s.rally_detection = detection or {}
    s._shuttle_source = "tracknet"
    s._hit_signals = _sig()
    s._hit_source = "learned"
    s._hit_list = [{"frame": 150, "hitter": "lower", "confidence": 0.9}]
    return s


def _read(tmp_path):
    return json.loads((tmp_path / "strokes.json").read_text(encoding="utf-8"))


def test_no_model_writes_unknown_with_null_classifier(tmp_path):
    labels = _bare(tmp_path)._run_stroke_classification()
    p = _read(tmp_path)
    assert p["schema"] == 2 and p["classifier"] is None
    assert [l["stroke"] for l in labels] == ["unknown"] == [s["stroke"] for s in p["strokes"]]


def test_model_labels_hits(tmp_path):
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1)
    path = tmp_path / "w.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, 0.0), path)
    _bare(tmp_path, str(path))._run_stroke_classification()
    p = _read(tmp_path)
    assert p["classifier"]["weights_sha256"] and p["strokes"][0]["stroke"] != "unknown"


def test_incompatible_model_is_unknown_not_fatal(tmp_path):
    bad = tmp_path / "w.pt"
    bad.write_bytes(b"not a model")
    _bare(tmp_path, str(bad))._run_stroke_classification()
    assert _read(tmp_path)["classifier"] is None


def test_withheld_removes_stale_file(tmp_path):
    (tmp_path / "strokes.json").write_text("{}", encoding="utf-8")
    labels = _bare(tmp_path, detection={"stroke_labels_withheld": True})._run_stroke_classification()
    assert labels == [] and not (tmp_path / "strokes.json").exists()


def test_no_hits_writes_nothing(tmp_path):
    s = _bare(tmp_path)
    s._hit_list = None
    assert s._run_stroke_classification() == [] and not (tmp_path / "strokes.json").exists()


def test_stroke_recog_package_is_gone():
    import importlib.util
    assert importlib.util.find_spec("badminton_analysis.stroke_recog") is None
    assert importlib.util.find_spec("badminton_analysis.stroke.classifier") is None
```

  Also add to `tests/test_app_lift_flags.py` (or the `/api/models` test file):

```python
def test_models_reports_stroke_classifier(client, tmp_path, monkeypatch):
    import app as app_mod
    monkeypatch.setattr(app_mod, "_stroke_weights", lambda base=None: "w.pt")
    body = client.get("/api/models").get_json()
    assert body["stroke"] is True and "bst" not in body
```

  (Use the existing `client` fixture in that file. If the file has none, create the client the
  same way its neighbours do.)

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

  1. **`events.py`:** append `hit_events(track)`, copied verbatim from `stroke_recog/hits.py`
     (it calls `detect_contacts_multi`, which is in the same module).
  2. **`system.py` `_run_hit_detection`:**
     - Start with `self._hit_list = self._hit_signals = self._hit_source = None`.
     - Import `hit_events` from `.stroke.events` (not `.stroke_recog.hits`).
     - After `hs.save_signals(...)`, set `self._hit_signals = signals`.
     - After the malformed-hit check, set `self._hit_list, self._hit_source = listed, source`.
     - The return value is unchanged.
  3. **`system.py`:** replace `_run_stroke_recognition` with the following, keeping its withheld
     docstring paragraph:

```python
    def _run_stroke_classification(self):
        """Label the shared hit list with the one stroke classifier -> strokes.json (schema 2).

        Withheld (no file, a previous one removed) when rally segmentation is degraded
        (owner decision 12a-C). No hits -> no file. Without a usable model every hit is
        "unknown" and classifier is null: there is no other classifier to fall back to.
        Never fatal.
        """
        from .data.writer import write_json as _write_json
        from .stroke import stroke_classifier as sc
        path = os.path.join(self.save_dir, "strokes.json")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"Could not remove the previous run's stroke labels {path}: {exc}")
        detection = getattr(self, "rally_detection", None) or {}
        if detection.get("stroke_labels_withheld", bool(detection.get("degraded"))):
            print("Stroke labels withheld: segmentation unreliable -- "
                  f"{detection.get('reason') or 'no reason recorded'}.")
            return []
        hits = getattr(self, "_hit_list", None)
        if not hits:
            return []
        labels, info = None, None
        model_path = getattr(self, "stroke_model_path", None)
        signals = getattr(self, "_hit_signals", None)
        if model_path and signals is not None:
            try:
                model, meta, info = sc.load_classifier(model_path)
                labels = sc.classify_hits(signals, hits, model, meta)
            except Exception as exc:
                print(f"Stroke classifier unavailable, every hit is unknown: {exc!r}")
                labels, info = None, None
        if labels is None:
            labels = sc.unknown_labels(hits)
        try:
            _write_json(path, sc.build_payload(labels, info, getattr(self, "_hit_source", None),
                                               getattr(self, "_shuttle_source", None)))
            print(f"Stroke classification: {len(labels)} hits -> {path}")
        except Exception as exc:
            print(f"strokes.json could not be written: {exc!r}")
        return labels
```

  4. **`process_video` post-loop:** replace lines 543–545 with:

```python
            labels = self._run_stroke_classification()
            if self.analyze_technique:
                self._run_technique_analysis(labels)
```

     and keep `hits = self._run_hit_detection(...)` (`hits` itself is no longer used after that,
     so drop the variable).
  5. **`_run_technique_analysis(self, labels=None)`:**
     - Use `hits = getattr(self, "_hit_list", None)`. Filter it to the tracked player exactly as
       today's `if hits is not None:` block does.
     - Build `strokes = {int(l["frame"]): l for l in labels or []}`.
     - Call `runner.run(self._analysis_track, self._analysis_frames.get, hits=hits, strokes=strokes)`.
  6. **Constructor:** rename the `bst_weights=None` parameter to `stroke_model_path=None` and
     `self.bst_weights` to `self.stroke_model_path`. Grep `system.py` for `bst_weights`; nothing
     may remain.
  7. **`main.py`:** replace the `--bst-model` argument with
     `parser.add_argument('--stroke-model', default=None, type=str, help='击球类型识别模型路径（可选）/ stroke classifier weights (optional)')`
     and pass `stroke_model_path=args.stroke_model`.
  8. **`app.py`:**
     - Rename `_bst_weights` → `_stroke_weights`, returning `str(p)` for
       `base / 'stroke-classifier.pt'` when both `p` and `p.with_suffix('.json')` exist.
     - In `/api/models`, use `'stroke': _stroke_weights() is not None` instead of `'bst'`.
     - In the job command, use `--stroke-model`.
     - Update the docstring at line 690.
  9. **Delete:** `git rm -r badminton_analysis/stroke_recog badminton_analysis/stroke/classifier.py tests/test_bst_classes.py tests/test_bst_inputs.py tests/test_bst_model.py tests/test_bst_recognizer.py tests/test_bst_integration.py tests/test_classifier.py`,
     then `git mv tests/test_bst_hits.py tests/test_hit_events.py` and fix its import.
  10. Run `grep -rn "stroke_recog\|bst_weights\|bst-model\|classify_stroke\|StrokeRecognizer\|_run_stroke_recognition" --include=*.py .`
      across the repo (excluding `docs/` and `third_party/`). Update every remaining test
      reference:
      - `_run_stroke_recognition(hits)` → set `s._hit_list`, `s._hit_signals`, `s._hit_source`
        on the bare system, then call `_run_stroke_classification()`;
      - assertions on BST labels (`"net"`, `"uncertain"`) → schema-2 labels (`"unknown"`).

- [ ] **Step 4: Run the touched suites**

  Run: `pytest tests/test_stroke_pipeline.py tests/test_hit_events.py tests/test_hit_integration.py tests/test_rally_segmentation.py tests/test_doubles_system.py tests/test_app_lift_flags.py tests/test_integration_analysis.py`
  Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A badminton_analysis main.py app.py tests
git commit -m "feat(stroke): one stroke classifier in the pipeline; remove BST and the rule classifier"
```

---

### Task 11: Insights, library route and the relabel command

**Files:**
- Modify: `badminton_analysis/insights/loaders.py` (`_load_strokes`, lines 181–194),
  `insights/strokes.py`, `insights/technique.py`.
- Create: `badminton_analysis/stroke/relabel.py`.
- Test: `tests/test_relabel.py` (new); update `tests/insights_fixtures.py`,
  `tests/test_insights_basic.py`, `tests/test_insights_aggregate.py` and
  `tests/test_insights_sidecar.py` where their stroke fixtures use old labels.

**Interfaces:**
- Consumes: `stroke_classifier.is_current/classify_hits/load_classifier/unknown_labels/build_payload`;
  `biomechanics.rescore_report`; `technique_writer.write_stroke_reports/build_match_summary`;
  `hit_signals.load_signals`.
- Produces:
  - `_load_strokes`: a document that is not `is_current` returns `(None, meta)`, with the note
    `"strokes.json: from the old classifier; re-label"`.
  - `strokes_insight`: counts `distribution` and `by_hitter[*].distribution` by **group** (the
    label's `group`). A stroke is uncertain when its `stroke == "unknown"` or it has no string
    `group`.
  - `technique_insight`: the technique of a report is
    `r.get("group") or stroke_types.group_of(t) or PROFILE_GROUP.get(t)`, counted only when
    `r.get("scored", True)` is truthy.
  - `python -m badminton_analysis.stroke.relabel [--weights weights/stroke-classifier.pt] <result_dir>...`
    and `relabel(result_dir, weights) -> dict summary`. It:
    - reads `hits.json` and `hit_signals.npz`;
    - writes the schema-2 `strokes.json` (unknown labels when the weights are unusable);
    - re-scores `strokes.jsonl` reports matched by `contact_frame` and `player_side`, rewriting
      `strokes.jsonl` and `technique_summary.json`;
    - removes `training_plan.json`, a cache regenerated from the summary;
    - skips doubles results (`metadata.json` `match_format == "doubles"`) and results whose
      `rally_segments.json` withholds labels.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_relabel.py
import json

from badminton_analysis.stroke import relabel
from badminton_analysis.stroke import stroke_model as smod
from badminton_analysis.stroke.hit_signals import save_signals
from badminton_analysis.stroke.stroke_windows import INPUT_DIM
from tests.test_stroke_windows import _sig


def _old_result(d):
    d.mkdir(parents=True, exist_ok=True)
    save_signals(d / "hit_signals.npz", _sig())
    (d / "hits.json").write_text(json.dumps({"source": "learned", "fps": 30.0, "shuttle_source": "tracknet",
        "hits": [{"frame": 150, "time_sec": 4.97, "hitter": "lower", "confidence": 0.8}]}), encoding="utf-8")
    (d / "strokes.json").write_text(json.dumps({"strokes": [{"frame": 150, "stroke": "net",
                                                             "uncertain": False}]}), encoding="utf-8")
    measured = {"elbow_extension": 150.0, "trunk_rotation": 30.0, "wrist_flexion": 90.0,
                "knee_flexion": 156.0, "hip_shoulder_separation": 25.0, "weight_transfer": 0.35}
    report = {"stroke_type": "high_clear", "contact_frame": 150, "player_side": "lower",
              "confidence": 0.2, "overall_score": 100.0, "feature_space": "2d",
              "per_metric": {k: {"measured": v} for k, v in measured.items()},
              "weaknesses": [], "strengths": []}
    (d / "strokes.jsonl").write_text(json.dumps(report) + "\n", encoding="utf-8")
    (d / "training_plan.json").write_text("{}", encoding="utf-8")


def test_relabel_converts_old_result(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    m = smod.StrokeNet(INPUT_DIM, hidden=8, levels=1)
    w = tmp_path / "w.pt"
    smod.save_stroke_model(m, smod.base_meta(8, 1, 0.0), w)
    summary = relabel.relabel(str(d), str(w))
    strokes = json.loads((d / "strokes.json").read_text(encoding="utf-8"))
    assert strokes["schema"] == 2 and strokes["strokes"][0]["stroke"] != "unknown"
    rep = json.loads((d / "strokes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rep["stroke_type"] == strokes["strokes"][0]["stroke"]
    assert json.loads((d / "technique_summary.json").read_text(encoding="utf-8"))["stroke_count"] == 1
    assert not (d / "training_plan.json").exists()
    assert summary["labelled"] == 1 and summary["reports"] == 1


def test_relabel_without_weights_marks_unknown(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    relabel.relabel(str(d), str(tmp_path / "missing.pt"))
    s = json.loads((d / "strokes.json").read_text(encoding="utf-8"))
    assert s["classifier"] is None and s["strokes"][0]["stroke"] == "unknown"
    rep = json.loads((d / "strokes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rep["scored"] is False


def test_relabel_skips_doubles(tmp_path):
    d = tmp_path / "V"
    _old_result(d)
    (d / "metadata.json").write_text(json.dumps({"match_format": "doubles"}), encoding="utf-8")
    assert relabel.relabel(str(d), None)["skipped"] == "doubles"


def test_insights_ignore_old_strokes_and_count_groups(tmp_path):
    from badminton_analysis.insights import loaders
    from badminton_analysis.insights.strokes import strokes_insight
    d = tmp_path / "V"
    _old_result(d)
    notes = []
    strokes, _ = loaders._load_strokes(str(d), notes)
    assert strokes is None and any("re-label" in n for n in notes)

    class Run:
        strokes = [{"stroke": "push", "group": "drive", "hitter": "lower"},
                   {"stroke": "drive", "group": "drive", "hitter": "lower"},
                   {"stroke": "unknown", "group": None, "hitter": "upper"}]
    v = strokes_insight(Run())["value"]
    assert v["distribution"] == {"drive": 2} and v["uncertain"] == 1
```

  (Check the `strokes_insight` return shape against `insights/envelope.py`. If the envelope key is
  not `"value"`, use the one it defines.)

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**
  1. **`loaders._load_strokes`:** after loading `doc`, add
     `if not is_current(doc): notes.append("strokes.json: from the old classifier; re-label"); return None, {k: v for k, v in doc.items() if k != "strokes"}`,
     importing `from ..stroke.stroke_classifier import is_current`.
  2. **`insights/strokes.py`:** `_is_uncertain(s)` returns True when `s.get("stroke") == "unknown"`
     or `not isinstance(s.get("group"), str)` or `not s.get("group")`. Count by `s["group"]`
     wherever the code used `s["stroke"]`. Update the module docstring.
  3. **`insights/technique.py`:**
     - Delete `REPORT_TO_TECH`.
     - Import `group_of` and `PROFILE_GROUP`.
     - Skip reports with `r.get("scored") is False`.
     - Use `tech = r.get("group") if isinstance(r.get("group"), str) else (group_of(stroke_type) or PROFILE_GROUP.get(stroke_type))`.
  4. **`relabel.py`:**

```python
"""Re-label a finished result with the current stroke classifier (spec 2026-10-22 section 4.6).

  python -m badminton_analysis.stroke.relabel [--weights weights/stroke-classifier.pt] <result_dir>...

Uses the saved hit inputs (hit_signals.npz, hits.json), so the video is not re-processed.
Rewrites strokes.json (schema 2), re-scores strokes.jsonl / technique_summary.json from
each report's measured metrics, and removes the cached training_plan.json.
"""
import argparse
import json
import os
import sys

from ..analysis.biomechanics import rescore_report
from ..analysis.technique_writer import build_match_summary, write_stroke_reports
from ..data.writer import write_json
from . import stroke_classifier as sc
from .hit_signals import load_signals

DEFAULT_WEIGHTS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "weights", "stroke-classifier.pt")


def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def relabel(result_dir, weights):
    meta = _json(os.path.join(result_dir, "metadata.json")) or {}
    if meta.get("match_format") == "doubles":
        return {"result": result_dir, "skipped": "doubles"}
    det = (_json(os.path.join(result_dir, "rally_segments.json")) or {}).get("detection") or {}
    if det.get("stroke_labels_withheld", bool(det.get("degraded"))):
        return {"result": result_dir, "skipped": "labels withheld"}
    hits_doc = _json(os.path.join(result_dir, "hits.json"))
    if not hits_doc or not hits_doc.get("hits"):
        return {"result": result_dir, "skipped": "no hits.json"}
    hits = [{"frame": int(h["frame"]), "hitter": h["hitter"], "confidence": h.get("confidence")}
            for h in hits_doc["hits"]]
    labels, info = None, None
    if weights and os.path.isfile(weights):
        try:
            model, mmeta, info = sc.load_classifier(weights)
            labels = sc.classify_hits(load_signals(os.path.join(result_dir, "hit_signals.npz")),
                                      hits, model, mmeta)
        except Exception as exc:
            print(f"{result_dir}: classifier unavailable ({exc!r}); every hit is unknown")
            labels, info = None, None
    if labels is None:
        labels = sc.unknown_labels(hits)
    write_json(os.path.join(result_dir, "strokes.json"),
               sc.build_payload(labels, info, hits_doc.get("source"), hits_doc.get("shuttle_source")))
    by_key = {(l["frame"], l["hitter"]): l for l in labels}
    reports_path = os.path.join(result_dir, "strokes.jsonl")
    n_reports = 0
    if os.path.isfile(reports_path):
        with open(reports_path, encoding="utf-8") as fh:
            reports = [json.loads(line) for line in fh if line.strip()]
        new = []
        for r in reports:
            label = by_key.get((r.get("contact_frame"), r.get("player_side"))) or {}
            out = rescore_report(r, label.get("stroke") or "unknown")
            out["confidence"] = label.get("confidence")
            new.append(out)
        write_stroke_reports(reports_path, new)
        write_json(os.path.join(result_dir, "technique_summary.json"), build_match_summary(new))
        n_reports = len(new)
    try:
        os.remove(os.path.join(result_dir, "training_plan.json"))
    except FileNotFoundError:
        pass
    return {"result": result_dir, "labelled": len(labels), "reports": n_reports,
            "classifier": info is not None}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1].strip())
    p.add_argument("results", nargs="+")
    p.add_argument("--weights", default=DEFAULT_WEIGHTS)
    a = p.parse_args(argv)
    for d in a.results:
        print(json.dumps(relabel(d, a.weights)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

  5. **Insights test fixtures:** in `tests/insights_fixtures.py` and the insights tests, change
     the `strokes.json` fixtures to schema 2: add `"schema": 2`, and give each stroke a `group`
     (for example `{"stroke": "net_shot", "group": "net"}`; uncertain becomes
     `{"stroke": "unknown", "group": None}`). Keep each test's intent; only the vocabulary changes.

- [ ] **Step 4: Run** `pytest tests/test_relabel.py tests/test_insights_basic.py tests/test_insights_aggregate.py tests/test_insights_sidecar.py tests/test_app_library.py`.
  Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add badminton_analysis/insights badminton_analysis/stroke/relabel.py tests
git commit -m "feat(stroke): insights count groups, old stroke files are stale, relabel command"
```

---

### Task 12: Kestrel UI (new views)

**Files:**
- Modify: `static/kestrel/core/i18n.js` (exports at line 4; zh strings near 157 and 534; en
  strings near 807 and 1184), `static/kestrel/views/workspace.js` (lines 16, 103–121, 152–185,
  284–292, 1062–1066, plus every use of a stroke's `.label`).
- Test: `tests/js/views_model.test.mjs` (update and extend), `tests/js/widgets_smoke.test.mjs`
  (only if it breaks).

**Interfaces:**
- Produces (in `i18n.js`):
  - `export const STROKE_TYPE_IDS = Object.freeze([...12 ids])`;
  - `export const STROKE_GROUP = Object.freeze({short_serve:'serve', ..., push:'drive'})`;
  - strings `stroke.<id>` (zh/en) for all 12, plus `ws.stroke_unverified`, `ws.stroke_stale`,
    `ws.stroke_no_model` and `ws.no_reference`.
- `buildWorkspaceModel` stroke items gain `type` (one of the 12 ids, or `'uncertain'`), keep
  `label` (the 7-group id or `'uncertain'`, used by filters, colours and the radar), and the
  model's `summary` gains `strokeNotice` (`'stale'|'no_model'|'unverified'|null`).

Strings:

| id | zh | en |
|---|---|---|
| short_serve | 发短球 | Short serve |
| long_serve | 发长球 | Long serve |
| clear | 高远球 | Clear |
| lift | 挑球 | Lift |
| smash | 杀球 | Smash |
| drop | 吊球 | Drop |
| net_shot | 放网 | Net shot |
| block | 挡网 | Block |
| cross_net | 勾对角 | Cross-court net |
| net_rush | 扑球 | Net rush |
| drive | 平抽球 | Drive |
| push | 推球 | Push |

| key | zh | en |
|---|---|---|
| ws.stroke_unverified | 击球类型识别尚未在你的视频上验证准确率。 | Stroke accuracy not yet verified on your footage. |
| ws.stroke_stale | 击球标签来自旧版识别器，请重新标注。 | Stroke labels out of date — re-label this result. |
| ws.stroke_no_model | 未安装击球识别模型。 | Stroke model not installed. |
| ws.no_reference | 暂无参考范围，未评分。 | No reference ranges yet — not scored. |

- [ ] **Step 1: Write the failing JS tests.** Add to `tests/js/views_model.test.mjs`:

```js
test('stroke types and groups come straight from schema-2 strokes.json', () => {
  const strokes = { schema: 2, classifier: { validated_on_owner_footage: true }, strokes: [
    { frame: 30, hitter: 'lower', stroke: 'push', group: 'drive', confidence: 0.9 },
    { frame: 60, hitter: 'upper', stroke: 'unknown', group: null, confidence: 0.2 }] };
  const m = buildWorkspaceModel({ strokes, fps: 30 });
  assert.deepEqual(m.strokes.map((s) => [s.type, s.label, s.uncertain]),
    [['push', 'drive', false], ['uncertain', 'uncertain', true]]);
  assert.equal(m.summary.strokeNotice, null);
});

test('stale strokes file shows no labels and a re-label notice', () => {
  const m = buildWorkspaceModel({ strokes: { strokes: [{ frame: 30, hitter: 'lower', stroke: 'net' }] }, fps: 30 });
  assert.equal(m.strokes[0].label, 'uncertain');
  assert.equal(m.summary.strokeNotice, 'stale');
});

test('unverified and missing-model notices', () => {
  const base = { schema: 2, strokes: [{ frame: 1, hitter: 'lower', stroke: 'smash', group: 'smash' }] };
  assert.equal(buildWorkspaceModel({ strokes: { ...base, classifier: null } }).summary.strokeNotice, 'no_model');
  assert.equal(buildWorkspaceModel({ strokes: { ...base, classifier: {} } }).summary.strokeNotice, 'unverified');
});

test('technique report joins with the same stroke type', () => {
  const strokes = { schema: 2, classifier: {}, strokes: [{ frame: 30, hitter: 'lower', stroke: 'clear', group: 'clear' }] };
  const techReports = [{ contact_frame: 30, player_side: 'lower', stroke_type: 'clear', group: 'clear', scored: true, overall_score: 70 }];
  const m = buildWorkspaceModel({ strokes, techReports, fps: 30 });
  assert.equal(m.strokes[0].report.technique, 'clear');
  assert.equal(m.strokes[0].report.rawType, 'clear');
});
```

  Also update the existing tests in that file that feed BST-style strokes (`stroke: 'net'`,
  `uncertain: true`, no schema) and still expect labels: give their fixtures `schema: 2` and
  `group`.

- [ ] **Step 2: Run** `node --test tests/js/views_model.test.mjs`. Expected: the new tests fail.

- [ ] **Step 3: Implement**
  1. **`i18n.js`:** add the two exports after `TECHNIQUE_IDS`, the `stroke.*` and `ws.*` strings
     to both the zh and en tables (tables above), and `export function strokeTypeLabel(id, lang)`,
     which returns `t('stroke.' + id, lang)`.
  2. **`workspace.js`:**
     - Import `STROKE_TYPE_IDS` and `STROKE_GROUP`, and delete `STROKE_MAP`.
     - In `buildWorkspaceModel`:

```js
  const sdoc = Array.isArray(strokes) ? null : obj(strokes);
  const current = sdoc !== null && sdoc.schema === 2;
  const strokeNotice = rawStrokes.length === 0 ? null : !current ? 'stale'
    : sdoc.classifier === null ? 'no_model'
    : obj(sdoc.classifier).validated_on_owner_footage === true ? null : 'unverified';
```

       A bare array counts as current (test helpers pass arrays).
     - Per stroke:

```js
    const raw = typeof s.stroke === 'string' ? s.stroke : '';
    const known = current !== false && STROKE_TYPE_IDS.includes(raw);
    const group = known ? (typeof s.group === 'string' ? s.group : STROKE_GROUP[raw]) : null;
    const uncertain = withheld || !known || !TECHNIQUE_IDS.includes(group);
```

       with `label: uncertain ? 'uncertain' : group, type: uncertain ? 'uncertain' : raw`. For a
       bare array, `current` is true.
     - `reportModel`: `technique` = `rep.group` if it is in `TECHNIQUE_IDS`, otherwise
       `STROKE_GROUP[raw]`, otherwise the legacy profile map `{high_clear:'clear', drop_shot:'drop', smash:'smash', serve:'serve'}`;
       add `scored: rep.scored !== false`.
     - Add `strokeNotice` to the returned `summary`.
     - Display: everywhere a stroke's name is shown (the stroke strip, the list rows, the
       inspector title), use `s.type === 'uncertain' ? t('ws.uncertain', lang) : t('stroke.' + s.type, lang)`.
       Leave `techLabel` for group names (filters, radar). `techCode` should use the group.
     - Render `summary.strokeNotice` as a muted line `t('ws.stroke_' + notice, lang)` under the
       stroke-list heading (the `ws-sec-head` at line 1056).
     - In the inspector, when `report.scored === false`, show `t('ws.no_reference', lang)`
       instead of the score.

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add static/kestrel/core/i18n.js static/kestrel/views/workspace.js tests/js
git commit -m "feat(kestrel): show the 12 stroke types from the one classifier; stroke notices"
```

---

### Task 13: Legacy results view (the page in the bug report)

**Files:**
- Modify: `static/kestrel/legacy/kestrel-legacy.js` (lines 1061–1076, 1190–1224, 1280, 1286–1308);
  `static/kestrel/core/i18n.js` line 665 and line 1315 (`dbl.strokes_withheld`: drop "BST is a
  singles model").
- Test: `tests/js/legacy_strokes.test.mjs` (new) for the pure helpers.

**Interfaces:**
- Produces, inside the legacy IIFE: `STROKE_LABELS_12` (the 12 ids → `[zh, en]`, the same strings
  as Task 12) and `GROUP_LABELS` (the 7 groups → `[zh, en]`, reusing the existing zh/en words:
  serve 发球, clear 高远球, smash 杀球, drop 吊球, drive 平抽球, net 网前球, lift 挑球). It also
  exposes `window.__kestrelLegacyStrokes = { strokeNotice, timelineItems, distributionText }` for
  the test, the same way other legacy helpers are exposed. If none are, export the pure helpers
  from a small new module `static/kestrel/legacy/strokes.js` that `kestrel-legacy.js` loads first.

- [ ] **Step 1: Write the failing test**

```js
// tests/js/legacy_strokes.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { strokeNotice, timelineItems, distributionText } from '../../static/kestrel/legacy/strokes.js';

test('schema-2 strokes render 12-type names and group distribution', () => {
  const d = { schema: 2, classifier: { validated_on_owner_footage: true },
    strokes: [{ stroke: 'push', group: 'drive' }, { stroke: 'unknown', group: null }],
    groups: { drive: 1 } };
  assert.deepEqual(timelineItems(d, 'en'), [{ text: 'Push', muted: false }, { text: 'Uncertain', muted: true }]);
  assert.equal(distributionText(d, 'zh'), '平抽球 1');
  assert.equal(strokeNotice(d), null);
});

test('old strokes.json is stale: no labels, re-label notice', () => {
  const d = { strokes: [{ stroke: 'net' }], distribution: { net: 1 } };
  assert.deepEqual(timelineItems(d, 'en'), []);
  assert.equal(distributionText(d, 'en'), '');
  assert.equal(strokeNotice(d), 'stale');
});

test('notices', () => {
  assert.equal(strokeNotice({ schema: 2, classifier: null, strokes: [] }), 'no_model');
  assert.equal(strokeNotice({ schema: 2, classifier: {}, strokes: [] }), 'unverified');
});
```

- [ ] **Step 2: Run** `node --test tests/js/legacy_strokes.test.mjs`. Expected: FAIL (module
  missing).

- [ ] **Step 3: Implement**
  1. Create `static/kestrel/legacy/strokes.js` as an ES module. It exports `STROKE_LABELS_12`,
     `GROUP_LABELS`, `GROUP_ORDER = ['serve','clear','lift','smash','drop','drive','net']`, and:
     - `strokeLabel(id, lang)`, which gives `'不确定'`/`'Uncertain'` for `unknown`;
     - `strokeNotice(d)`: `'stale'` when `d.schema !== 2`; `'no_model'` when
       `d.classifier === null`; `'unverified'` unless `d.classifier.validated_on_owner_footage === true`;
       otherwise null;
     - `timelineItems(d, lang)`: `[]` when stale; otherwise
       `{ text: strokeLabel(s.stroke, lang), muted: s.stroke === 'unknown' }` per stroke;
     - `distributionText(d, lang)`: `''` when stale; otherwise
       `GROUP_ORDER.filter(g => d.groups?.[g] > 0).map(g => label + ' ' + n).join(' · ')`;
     - `noticeText(kind, lang)`, using the four strings from Task 12;
     - `noReferenceText(lang)`.
  2. **`kestrel-legacy.js`:**
     - It is a classic script. Check how `kestrel.html` loads it. If it is loaded as a plain
       `<script>`, change that tag to `type="module"`, provided the file has no top-level
       `this`/sloppy-mode dependencies (it is an IIFE, so it should be safe). Then add
       `import * as LS from './strokes.js';` at the top. If module loading is not workable,
       expose the helpers on `window.KestrelLegacyStrokes` from `strokes.js`, load it with a
       module script before `kestrel-legacy.js`, and read `window.KestrelLegacyStrokes` lazily
       inside `renderMatchResults`.
     - Replace `STROKE_COARSE_LABELS`/`STROKE_COARSE_ORDER`/`strokeCoarseLabel` and the
       timeline/distribution code at 1203–1223 with `LS.timelineItems` and
       `LS.distributionText`.
     - When `LS.strokeNotice(d)` is non-null, insert
       `<p class="muted" id="stroke-notice">` + `textContent = LS.noticeText(...)` into the
       timeline box, and show the box even when the timeline is empty.
     - Technique chips (line 1292) and rows (line 1299): label with
       `LS.strokeLabel(k, state.lang)` when `k` is one of the 12 ids, falling back to
       `strokeTypeLabel(k)` (drill names).
     - Rows whose `st.scored === false` show `LS.noReferenceText(lang)` in place of the score
       chip.
     - Keep `STROKE_TYPE_LABELS` for the drill-mode select (lines 872 and 921).
     - Line 1280: drop "（BST 为单打模型）" / "(BST is a singles model)".

- [ ] **Step 4: Run** `node --test tests/js/*.test.mjs`. Expected: all pass. Then load the legacy
  page in the browser pane against a local `python app.py` (see Task 15) and confirm the console
  shows no module or syntax errors.

- [ ] **Step 5: Commit**

```bash
git add static/kestrel/legacy static/kestrel/core/i18n.js kestrel.html tests/js
git commit -m "feat(kestrel-legacy): results timeline and technique use the one stroke classifier"
```

(Add `templates/kestrel.html` or wherever `kestrel.html` lives, and only if it changed.)

---

### Task 14: Docs and state

**Files:**
- Modify: `README.md` line 126 and `README_EN.md` line 421 (replace "BST" with "stroke
  classifier"), plus each README's stroke-recognition section, which must say the model comes from
  `scripts/train_stroke_classifier.py` and is installed as `weights/stroke-classifier.pt` +
  `.json`.
- Modify: `.ai/STATE.md` (this feature's row: what is built, the verification, the next action);
  `third_party/bst/CONTRACT.md` (one line at the top: "Unused since 2026-10-22; kept vendored
  pending a removal decision.").

- [ ] **Step 1:** Make the edits. Find every README stroke mention with `grep -n -i "bst\|stroke" README.md README_EN.md`.
- [ ] **Step 2: Commit**

```bash
git add README.md README_EN.md .ai/STATE.md third_party/bst/CONTRACT.md
git commit -m "docs: the stroke classifier replaces BST"
```

---

### Task 15: Train the first model, relabel the DJI results, verify end to end

This task runs real tools on the owner's machine. Nothing here is committed except the STATE row.

- [ ] **Step 1: Full test suites**

```bash
PYTHONUTF8=1 $VENV/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp "$SCRATCH/pt-final"
node --test tests/js/*.test.mjs
```

Expected: everything passes except the 3 known symlink skips (or the 2 noted in STATE). Record
the counts.

- [ ] **Step 2: Train on ShuttleSet** (GPU if available; run it in the background and log to a
  file):

```bash
$VENV/Scripts/python.exe scripts/train_stroke_classifier.py \
  --dataset D:/Dev/Claude/Projects/good-badminton/Good-badminton/data/hit_dataset \
  --val-match Pusarla_V._Sindhu_Pornpawee_Chochuwong_HSBC_BWF_WORLD_TOUR_FINALS_2020_QuarterFinals \
  --epochs 60 --out D:/Dev/Claude/Projects/good-badminton/Good-badminton/weights/stroke-classifier.pt
```

Record the sidecar's `val_metrics`: group accuracy, worst-group recall, unknown rate,
12-type accuracy. This is the pro-footage check, not the owner's bar.

- [ ] **Step 3: Relabel the three DJI results**

```bash
$VENV/Scripts/python.exe -m badminton_analysis.stroke.relabel \
  --weights D:/Dev/Claude/Projects/good-badminton/Good-badminton/weights/stroke-classifier.pt \
  D:/Dev/Claude/Projects/good-badminton/Good-badminton/outputs/DJI_20261010090046_0010_D \
  D:/Dev/Claude/Projects/good-badminton/Good-badminton/outputs/DJI_20261010090508_0011_D \
  D:/Dev/Claude/Projects/good-badminton/Good-badminton/outputs/DJI_20261010092251_0014_D
```

- [ ] **Step 4: Live check of the branch's UI.** Start this worktree's app on another port (add
  a `.claude/launch.json` entry `python app.py --port 5051` if `app.py` supports `--port`;
  otherwise use its documented flag) and open
  `#/results/DJI_20261010092251_0014_D?mode=match`. Check that:
  - every shared frame has the same name in the timeline and in Technical Analysis (script it in
    the page with `javascript_tool`: fetch `strokes.json` and `/api/technique/...`, join on frame,
    count disagreements; expected 0);
  - the "not yet verified" notice shows;
  - unscored rows say "no reference ranges yet";
  - the workspace view (`#/matches/<stem>`) shows the same labels.

  Take a screenshot. The container on 5050 is built from `origin/main` only, so it will not show
  this branch until merged.

- [ ] **Step 5: Make the labelling sheet for the owner.** Video 0014 is the test video (the one
  in the bug report); 0010 and 0011 are train. Order: 0010, then 0011, then 0014.

```bash
$VENV/Scripts/python.exe scripts/make_stroke_label_sheet.py \
  --results-dir D:/Dev/Claude/Projects/good-badminton/Good-badminton/outputs \
  --videos-dir D:/Dev/Claude/Projects/good-badminton/Good-badminton/videos \
  --out D:/Dev/Claude/Projects/good-badminton/Good-badminton/outputs/dji-stroke-labels \
  --test DJI_20261010092251_0014_D \
  DJI_20261010090046_0010_D DJI_20261010090508_0011_D DJI_20261010092251_0014_D
```

- [ ] **Step 6:** Update the `.ai/STATE.md` row with the verification (commands, counts, commit)
  and the next action: the owner labels the sheet; then retrain with
  `--dji-labels ... --calib-video DJI_20261010090508_0011_D`, relabel, and run
  `scripts/score_strokes.py --labels ... --split test`. Commit, then push the branch and open the
  PR against the fork's `main` (owner merges).

---

## Self-review notes

- **Spec coverage:**
  - §3 taxonomy → T1;
  - §4.1 model and training → T3, T4, T7;
  - §4.2 pipeline → T6, T10;
  - §4.3 profiles → T9;
  - §4.4 removal → T10, T13;
  - §4.5 UI → T12, T13;
  - §4.6 relabel and stale results → T11–T13;
  - §5 errors → T6, T10;
  - §6.1 sheet → T2;
  - §6.2 scoring → T5, T8;
  - §7 order → the tasks follow it (the sheet tool comes first; the DJI round depends on the
    owner);
  - §9 tests → each task;
  - §10 coordination → `learned-hit-detection` is fully merged into `main`, so there is nothing
    to rebase.
- **Deliberate behaviour change:** with no hit model, technique analysis now uses the shared
  contact-rule hit list (`hits.json`) instead of its own single-player contact rule, so both
  panels see the same hits. When rally segmentation is degraded, technique reports become unscored
  (stroke `unknown`), because the labels are withheld.
- **Owner-dependent:** the bar can only be checked after the owner labels the sheet; Task 15 ends
  by handing that over.
