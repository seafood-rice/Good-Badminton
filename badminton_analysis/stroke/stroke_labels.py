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
