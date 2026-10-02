"""Controller-run B11 checks. Not part of the committed suite.

Staged cheapest-first, because a full pipeline run costs hours:

  --gate    re-measure the court-view calibration and pass fraction on a video
            (minutes, no model weights)
  --replay  run segment_rallies over an ALREADY RECORDED detections.jsonl (seconds)

Production code is reused, never re-implemented: --gate drives the real
``BadmintonAnalysisSystem._load_template`` / ``_calibrate_court_view`` /
``_court_view_downscaled`` (on an instance made with ``__new__``, so no model
is loaded), and --replay drives ``rallies.wrists_from_hands`` and
``rallies.segment_rallies``. The harness only prints; it writes no files.

Usage:
    PYTHONUTF8=1 ./.venv/Scripts/python.exe -B scripts/check_rally_gate.py \
        --gate "videos/clip.mp4" --template "templates/_auto_clip.png"
    PYTHONUTF8=1 ./.venv/Scripts/python.exe -B scripts/check_rally_gate.py \
        --replay "outputs/<run>/detections.jsonl" [--fps 59.94]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _CountingCapture:
    """Delegating ``cv2.VideoCapture`` that counts grab/retrieve/read calls.

    Lets the harness report how many frames the production calibration pre-scan
    traversed (grab) and decoded (retrieve) without copying its sampling loop.
    """
    grabs = 0
    retrieves = 0

    def __init__(self, real):
        self._real = real

    def grab(self):
        ok = self._real.grab()
        if ok:
            _CountingCapture.grabs += 1
        return ok

    def retrieve(self, *args, **kwargs):
        ok, frame = self._real.retrieve(*args, **kwargs)
        if ok:
            _CountingCapture.retrieves += 1
        return ok, frame

    def __getattr__(self, name):
        return getattr(self._real, name)


def gate(video, template_path):
    import cv2
    from badminton_analysis.system import BadmintonAnalysisSystem

    # The minimum a gate method reads: no __init__, so no weights are loaded.
    # _court_view_score reads nothing; _calibrate_court_view sets
    # court_view_calibration / court_view_cut; _court_view_downscaled reads
    # court_view_cut.
    system = BadmintonAnalysisSystem.__new__(BadmintonAnalysisSystem)

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"unreadable video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    try:
        _gray, _color, template_small = system._load_template(str(template_path), cap)
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    # 1. The calibration pre-scan, timed alone.
    real_capture = cv2.VideoCapture
    cv2.VideoCapture = lambda *a, **k: _CountingCapture(real_capture(*a, **k))
    t0 = time.perf_counter()
    try:
        system._calibrate_court_view(str(video), template_small, fps)
    finally:
        cv2.VideoCapture = real_capture
    calibration_sec = time.perf_counter() - t0
    calib = system.court_view_calibration

    # 2. The per-frame gate pass over every frame, as the live loop does it
    #    (grayscale + production _court_view_downscaled). Decode time is split
    #    out so the gate's own cost is not conflated with reading the video.
    passed = decoded = 0
    decode_sec = score_sec = 0.0
    wall0 = time.perf_counter()
    while True:
        t = time.perf_counter()
        ok, frame = cap.read()
        decode_sec += time.perf_counter() - t
        if not ok:
            break
        t = time.perf_counter()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if system._court_view_downscaled(gray, template_small):
            passed += 1
        score_sec += time.perf_counter() - t
        decoded += 1
    gate_sec = time.perf_counter() - wall0
    cap.release()

    print(json.dumps({
        "video": str(video),
        "template": str(template_path),
        "fps": fps,
        "container_frame_count": total,
        "calibration": calib,
        "calibration_frames_grabbed": _CountingCapture.grabs,
        "calibration_frames_sampled_decoded": _CountingCapture.retrieves,
        "calibration_seconds": round(calibration_sec, 2),
        "gate_pass_frames_decoded": decoded,
        "gate_pass_frames_passed": passed,
        "gate_pass_frac": passed / decoded if decoded else 0.0,
        "gate_pass_seconds_total": round(gate_sec, 2),
        "gate_pass_seconds_decode": round(decode_sec, 2),
        "gate_pass_seconds_cvt_and_score": round(score_sec, 2),
    }, indent=2))


def replay(detections, fps_override=None):
    from badminton_analysis.stroke import rallies

    detections = Path(detections)
    meta_path = detections.parent / "metadata.json"
    meta = {}
    if meta_path.is_file():
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    fps = fps_override if fps_override else (meta.get("video") or {}).get("fps")
    corners = (meta.get("court") or {}).get("corners")
    if not fps:
        raise SystemExit(f"no fps: pass --fps or provide {meta_path}")

    track = []
    with open(detections, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            pl = d.get("players") or {}

            def wrist(side):
                hands = (pl.get(side) or {}).get("hands") or {}
                return rallies.wrists_from_hands(hands.get("left"), hands.get("right"))

            track.append({"frame": d.get("frame"),
                          "wrist_lower": wrist("lower"),
                          "wrist_upper": wrist("upper"),
                          "shuttle": (d.get("shuttlecock") or {}).get("image")})

    segments, prov = rallies.segment_rallies(track, fps, quad=corners)
    durations = sorted(s["end_sec"] - s["start_sec"] for s in segments)
    print(json.dumps({
        "detections": str(detections),
        "fps": fps,
        "fps_source": "--fps" if fps_override else "metadata.json",
        "quad": corners,
        "frames": len(track),
        "segments": len(segments),
        "median_duration_sec": durations[len(durations) // 2] if durations else None,
        "longest_sec": durations[-1] if durations else None,
        "shortest_sec": durations[0] if durations else None,
        "signal": prov["signal"],
        "degraded": prov["degraded"],
        "suppressed_static_shuttle": prov["suppressed_static_shuttle"],
        "shuttle_density": prov["shuttle_density"],
        "caps_source": prov["caps_source"],
        "gated_outside_court": prov["gated_outside_court"],
        "provenance": prov,
    }, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", help="video path")
    ap.add_argument("--template", help="court template PNG for --gate")
    ap.add_argument("--replay", help="path to a recorded detections.jsonl")
    ap.add_argument("--fps", type=float, default=None,
                    help="override fps (default: video.fps from the run's metadata.json)")
    args = ap.parse_args()
    if not (args.gate or args.replay):
        ap.error("give --gate and/or --replay")
    if args.gate:
        if not args.template:
            ap.error("--gate needs --template")
        gate(args.gate, args.template)
    if args.replay:
        replay(args.replay, args.fps)
