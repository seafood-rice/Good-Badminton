"""Court-free single-player posture/technique drill analysis."""
import os
import time

import numpy as np

from .rep_segmenter import segment_reps
from .shuttle_pick import pick_shuttle
from .writer import write_rep_reports, build_drill_summary

# close-up drill footage: far-view-trained detector needs a lower threshold
RACKET_CONF = 0.15

ROI_MARGIN = 0.75  # racket can extend ~a racket-length beyond the body; keep the box generous

# A tracked person can't cross a quarter of the frame diagonal between two
# consecutive frames; a bigger jump means an identity switch (a different
# person was picked) or a hard cut, not real motion.
PERSON_STICKY_MAX_JUMP_FRAC = 0.25

# The quality TCN was trained on full annotated swing windows (~4.1s mean); the rep
# segmenter's peak-centered window (window_pre/window_post below) is much narrower
# (~1.2s), which starves the scorer at inference. Widen just its input window to a
# contact-centered +/-2.0s; heuristic per-metric scoring keeps the narrow rep window.
#
# On fast drills (contacts <4s apart) an unclamped +/-2s bleeds into 3-4
# neighboring strokes, scoring a multi-swing pose sequence and flattening the
# AI scores (measured: spread 8.8 across 7 reps at +/-2s vs 41-47 when the
# window is narrowed). run() therefore clamps this window to the midpoint
# between each rep's contact and its neighboring segmented reps' contacts
# (see _quality_window_frames' lo_bound/hi_bound). For slow drills those
# midpoints sit far beyond the +/-2s cap, so the cap still dominates and
# behavior is unchanged; for fast drills the midpoints bound each rep to its
# own stroke.
QUALITY_WINDOW_PRE_S = 2.0
QUALITY_WINDOW_POST_S = 2.0

# Sentinel hi_bound for a rep with no next neighbor to bound against (last rep
# in the segmented list): far beyond any real video length, so the +/-2s cap
# is the only thing that can shrink the window. _window_frames only returns
# frames frame_lookup actually has, so an over-large hi_bound is harmless.
_NO_NEXT_NEIGHBOR_HI_SENTINEL = 10 ** 9

# Overhead-swing gate: only count full overhead swings (high_clear) as reps.
# Empirically established on 50 genuine Sub05 clears: a full overhead swing
# lifts the dominant wrist above the dominant shoulder at the swing apex
# (positive elevation ratio); a soft/low return keeps the wrist at or below
# the shoulder (ratio <= 0). View-dependent (weaker on front/side camera
# angles, strong on behind-the-player views), so the threshold below is a
# documented, tunable constant and errs toward KEEPING reps.
OVERHEAD_APEX_S = 1.5            # half-window (seconds) searched for the swing apex
OVERHEAD_MIN_ELEVATION = 0.35    # min (shoulder_y - wrist_y)/torso at apex to count as
                                 # a full overhead swing. Calibrated on IMG_1270: real
                                 # overhead clears score 0.48-0.70 there while soft/
                                 # non-overhead actions score ~0.26; 0.35 sits in that
                                 # gap. (Raised from an earlier conservative 0.10 set
                                 # before that footage was available.) View-dependent
                                 # (foreshortened front/far-view cameras read lower) and
                                 # tunable.
# Overhead strokes subject to the elevation FLOOR above. The gate measurement
# (apex_overhead_elevation) is pure geometry with nothing clear-specific in it, so
# it transfers to any stroke contacted above the shoulder. NOTE the 0.35 threshold
# was calibrated on high_clear footage (IMG_1270) only: a soft drop shot could sit
# near the boundary and be filtered. Unvalidated for drop_shot until drop-shot
# footage exists; over-filtering shows up in filtered_non_overhead rather than
# silently losing reps.
OVERHEAD_GATED_STROKES = ("high_clear", "smash", "drop_shot")

# Underhand strokes subject to the same threshold as a CEILING: a rep that reaches
# overhead elevation is an overhead stroke, so it does not belong in this drill.
# Deliberately reuses OVERHEAD_MIN_ELEVATION rather than introducing a second
# constant -- 0.35 separates "overhead" from "not overhead" regardless of which side
# of it a given stroke is supposed to fall on. Unvalidated on serve footage.
UNDERHAND_GATED_STROKES = ("serve",)


def person_roi(kp):
    """Bounding box around valid keypoints, expanded by ROI_MARGIN of its larger side.

    Returns [(x1, y1), (x2, y2)] or None when kp is None or fewer than 2 joints
    are valid (x>1 and y>1 - the codebase's undetected-joint sentinel convention,
    same as badminton_analysis/quality/normalize.py's posed_frames).
    """
    if kp is None:
        return None
    kp = np.asarray(kp, dtype=float)
    valid = (kp[:, 0] > 1.0) & (kp[:, 1] > 1.0)
    if int(np.count_nonzero(valid)) < 2:
        return None
    xs = kp[valid, 0]
    ys = kp[valid, 1]
    x1, x2 = float(xs.min()), float(xs.max())
    y1, y2 = float(ys.min()), float(ys.max())
    pad = ROI_MARGIN * max(x2 - x1, y2 - y1)
    return [(x1 - pad, y1 - pad), (x2 + pad, y2 + pad)]


def apex_overhead_elevation(window_frames, dominant="right"):
    """Max over the window of (shoulder_y - wrist_y) / torso for the dominant side.

    torso = |shoulder_y - hip_y| (dominant side). Frames missing a valid dominant
    shoulder/wrist/hip (sentinel x<=1,y<=1) are skipped; torso<1px frames skipped.
    Returns a float, or None when no frame yields a valid measurement (caller treats
    None as 'cannot judge -> keep the rep').
    """
    from ..analysis import joint_angles as ja
    shoulder_idx, wrist_idx, hip_idx = (
        (ja.R_SHOULDER, ja.R_WRIST, ja.R_HIP) if dominant == "right"
        else (ja.L_SHOULDER, ja.L_WRIST, ja.L_HIP)
    )
    best = None
    for f in window_frames:
        kp = f.get("keypoints")
        if kp is None:
            continue
        conf = f.get("conf")
        if not (ja.is_valid(kp, shoulder_idx, conf) and ja.is_valid(kp, wrist_idx, conf)
                and ja.is_valid(kp, hip_idx, conf)):
            continue
        shoulder_y = float(kp[shoulder_idx][1])
        wrist_y = float(kp[wrist_idx][1])
        hip_y = float(kp[hip_idx][1])
        torso = abs(shoulder_y - hip_y)
        if torso < 1.0:
            continue
        elevation = (shoulder_y - wrist_y) / torso
        if best is None or elevation > best:
            best = elevation
    return best


def format_progress(pct, stage):
    """Progress line consumed by the web layer: 'PROGRESS <pct> <stage>'."""
    return "PROGRESS " + str(int(pct)) + " " + stage


def analyzing_pct(frame_count, total_frames):
    """Map frame progress into the 5-85 'analyzing' band."""
    frac = min(1.0, frame_count / max(total_frames, 1))
    return 5 + int(frac * 80)


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _spread(person):
    """Bounding-box footprint of one person's keypoints: (max_x-min_x) + (max_y-min_y)."""
    xs = person[:, 0]
    ys = person[:, 1]
    return float((xs.max() - xs.min()) + (ys.max() - ys.min()))


def _person_center(person):
    """Mean of a person's valid joints (x>1 and y>1 sentinel convention, same
    as person_roi), falling back to the mean of all joints if none are valid.
    """
    person = np.asarray(person, dtype=float)
    valid = (person[:, 0] > 1.0) & (person[:, 1] > 1.0)
    pts = person[valid] if np.count_nonzero(valid) > 0 else person
    return (float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1])))


def _center_dist(a, b):
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


class PostureRunner:
    """Post-loop orchestration: reps -> per-rep StrokeEvent -> biomechanical reports."""

    def __init__(self, analyzer, stroke_type, dominant="right",
                 window_pre=20, window_post=15, quality_scorer=None,
                 pose_lifter=None, image_size=None):
        self.analyzer = analyzer
        self.stroke_type = stroke_type
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post
        self.quality_scorer = quality_scorer
        self.pose_lifter = pose_lifter
        self.image_size = image_size

    def _window_frames(self, window_start, window_end, frame_lookup):
        frames = []
        for idx in range(window_start, window_end + 1):
            rec = frame_lookup(idx)
            if rec is None:
                continue
            frames.append({
                "frame": idx,
                "keypoints": rec.get("keypoints"),
                "conf": rec.get("conf"),
                "racket_head": rec.get("racket_head"),
                "centroid": rec.get("centroid"),
                "racket_head_detected": rec.get("racket_head_detected", False),
            })
        return frames

    def _quality_window_frames(self, contact_frame, fps, frame_lookup, lo_bound=0, hi_bound=None):
        """Wider, contact-centered window for the quality scorer only (QUALITY_WINDOW_*_S),
        clamped to `lo_bound`/`hi_bound` (the midpoints to the neighboring segmented
        reps' contacts - see run()) so it never bleeds into an adjacent stroke.

        Missing frame numbers are simply absent (see _window_frames), so this is
        naturally clamped to whatever frames are actually available; the lower
        bound is also clamped explicitly so it never asks for negative frame keys.
        """
        pre_f = round(QUALITY_WINDOW_PRE_S * fps)
        post_f = round(QUALITY_WINDOW_POST_S * fps)
        window_start = max(0, contact_frame - pre_f, lo_bound)
        window_end = contact_frame + post_f
        if hi_bound is not None:
            window_end = min(window_end, hi_bound)
        return self._window_frames(window_start, window_end, frame_lookup)

    def _apex_window_frames(self, contact_frame, fps, frame_lookup):
        """Apex-search window for the overhead-swing gate (OVERHEAD_APEX_S both sides)."""
        apex_f = round(OVERHEAD_APEX_S * fps)
        window_start = max(0, contact_frame - apex_f)
        window_end = contact_frame + apex_f
        return self._window_frames(window_start, window_end, frame_lookup)

    def run(self, track, frame_lookup, fps):
        from ..stroke.events import StrokeEvent
        # An underhand stroke's contact is not at the wrist apex (rep_segmenter's
        # apex fallback is an overhead heuristic), so serve keeps the speed peak.
        fallback = None if self.stroke_type in UNDERHAND_GATED_STROKES else "apex"
        reps = segment_reps(track, fps, pre=self.window_pre, post=self.window_post,
                            positional_fallback=fallback)
        reports = []
        reps_3d = []
        survivor_rep_ids = []
        scored_3d = 0
        overhead_gated = self.stroke_type in OVERHEAD_GATED_STROKES
        underhand_gated = self.stroke_type in UNDERHAND_GATED_STROKES
        gated = overhead_gated or underhand_gated
        filtered_non_overhead = 0
        filtered_overhead = 0
        for i, rep in enumerate(reps):
            event = StrokeEvent(
                stroke_type=self.stroke_type,
                contact_frame=rep.peak_frame,
                window_start=rep.window_start,
                window_end=rep.window_end,
                player_side="single",
                confidence=rep.prominence,
            )
            window_frames = self._window_frames(rep.window_start, rep.window_end, frame_lookup)

            # Overhead-swing gate runs BEFORE the (potentially expensive) 3D lift
            # and the biomechanical analysis: a rep the gate drops must neither be
            # lifted/counted in reps_3d/scored_3d nor analyzed, so those figures
            # never exceed gate_info["counted"] (the reports that actually survive).
            apex_frames = self._apex_window_frames(rep.peak_frame, fps, frame_lookup)
            elev = apex_overhead_elevation(apex_frames, self.dominant)
            # `elev is None` means the window yielded no valid shoulder/wrist/hip
            # measurement: cannot judge -> keep the rep, in BOTH directions.
            if elev is not None:
                if overhead_gated and elev < OVERHEAD_MIN_ELEVATION:
                    filtered_non_overhead += 1
                    continue
                if underhand_gated and elev >= OVERHEAD_MIN_ELEVATION:
                    filtered_overhead += 1
                    continue

            if self.pose_lifter is not None and getattr(self.pose_lifter, "available", False):
                lifted = self.pose_lifter.lift(window_frames, self.image_size)
                if lifted is not None:
                    kp3d, frames3d = lifted
                    by_frame = {fn: kp3d[j] for j, fn in enumerate(frames3d)}
                    for w in window_frames:
                        if w["frame"] in by_frame:
                            w["keypoints_3d"] = by_frame[w["frame"]]
                    # Keyed on this rep's PRE-renumbering id for now; rewritten
                    # below to the final 1..N id once survivors are known.
                    reps_3d.append({"rep_id": rep.rep_id, "frames": list(frames3d),
                                    "keypoints_3d": kp3d})

            report = self.analyzer.analyze(event, window_frames)
            report["rep_id"] = rep.rep_id
            report["overhead_elevation"] = None if elev is None else round(elev, 3)
            report["contact_anchor"] = rep.contact_anchor
            if report.get("feature_space") == "3d":
                scored_3d += 1

            if self.quality_scorer is not None:
                # Bound the AI window to this rep's own stroke: clamp at the
                # midpoint to each neighboring segmented rep's contact. Uses
                # the full `reps` list (not `reports`) - a physically-adjacent
                # stroke pollutes the pose sequence whether or not the
                # overhead gate later drops it.
                prev_c = reps[i - 1].peak_frame if i > 0 else None
                next_c = reps[i + 1].peak_frame if i < len(reps) - 1 else None
                lo_bound = (prev_c + rep.peak_frame) // 2 if prev_c is not None else 0
                hi_bound = ((rep.peak_frame + next_c) // 2 if next_c is not None
                            else rep.peak_frame + _NO_NEXT_NEIGHBOR_HI_SENTINEL)
                quality_frames = self._quality_window_frames(
                    rep.peak_frame, fps, frame_lookup, lo_bound=lo_bound, hi_bound=hi_bound)
                ai = self.quality_scorer.score(quality_frames, dominant=self.dominant)
                if ai is not None:
                    report["ai_score"] = ai
            reports.append(report)
            survivor_rep_ids.append(rep.rep_id)
        gate_info = {"counted": len(reports), "filtered_non_overhead": filtered_non_overhead,
                     "filtered_overhead": filtered_overhead,
                     "gated": gated, "scored_3d": scored_3d, "reps_3d": reps_3d}
        # Renumber survivors 1..N so downstream consumers (write_rep_reports,
        # build_drill_summary, the coach report table) never show gapped ids
        # left behind by reps the overhead gate dropped above. The `reps`
        # RepWindow list is not renumbered: it isn't consumed downstream.
        for new_id, report in enumerate(reports, start=1):
            report["rep_id"] = new_id
        # reps_3d only ever holds entries for survivors (the gate now runs
        # before the lift), so every entry's original id is in this map;
        # rewrite it to match the renumbered `reports`/drill_reps.jsonl ids so
        # a future consumer can join drill_reps_3d.npz to them by rep_id.
        id_map = {old_id: new_id for new_id, old_id in enumerate(survivor_rep_ids, start=1)}
        for r3d in reps_3d:
            r3d["rep_id"] = id_map[r3d["rep_id"]]
        return reports, reps, gate_info


class PostureAnalysisSystem:
    """Run the full court-free pipeline over a video file."""

    def __init__(self, video_path, stroke_type, dominant_hand="right",
                 output_dir=None, ball_model_path=None, show_display=False,
                 show_overlay=True, pose_model="weights/yolo11n-pose.pt",
                 pose_family="yolo-pose", pose_mode="balanced",
                 yolo_pose_model="weights/yolo11n-pose.pt",
                 report_llm="off", racket_model_path=None, quality_model_path=None,
                 lift_model_path=None, lift_device="auto"):
        if not os.path.exists(video_path):
            raise FileNotFoundError("Input video not found: " + video_path)
        self.video_path = video_path
        self.stroke_type = stroke_type
        self.dominant_hand = dominant_hand
        self.show_display = show_display
        self.show_overlay = show_overlay
        self.ball_model_path = ball_model_path
        self.pose_model = pose_model
        self.pose_family = pose_family
        self.pose_mode = pose_mode
        self.yolo_pose_model = yolo_pose_model
        self.report_llm = report_llm
        self.racket_model_path = racket_model_path
        self._racket_detector = None
        self._racket_stats = {"detected": 0, "inferred": 0}
        self.quality_model_path = quality_model_path
        self._quality_scorer = None
        self.lift_model_path = lift_model_path
        self.lift_device = lift_device
        self._pose_lifter = None

        self.video_name = os.path.basename(video_path).rsplit(".", 1)[0]
        self.save_dir = output_dir or os.path.join("outputs", self.video_name, "posture")
        os.makedirs(self.save_dir, exist_ok=True)
        self.output_video_path = os.path.join(self.save_dir, "detect_" + self.video_name + ".mp4")

        self._track = []
        self._frames = {}
        self._person_anchor = None

    def _build_pose_processor(self):
        if self.pose_family == "yolo-pose":
            from ..detection.yolo_pose import YOLOPoseProcessor
            return YOLOPoseProcessor(model_path=self.yolo_pose_model or self.pose_model)
        from ..detection.rtmpose import RTMPoseProcessor
        return RTMPoseProcessor(mode=self.pose_mode, pose_family=self.pose_family)

    def _write_reps_3d_sidecar(self, gate_info):
        """Write the per-rep 3D feature sidecar when this run produced lifts,
        else remove a stale one left over from an earlier run in this
        save_dir (e.g. weights were available then, absent or unproductive
        now) so a 2D-only run never leaves an out-of-date 3D file -- with
        rep IDs/frames that no longer match this run's reports -- behind."""
        reps_3d_path = os.path.join(self.save_dir, "drill_reps_3d.npz")
        if gate_info.get("reps_3d"):
            from ..analysis.pose3d_io import write_reps_3d
            write_reps_3d(
                reps_3d_path,
                gate_info["reps_3d"],
                {"model": self.lift_model_path, "joint_format": "h36m-17",
                 "normalization": "screen", "stroke_type": self.stroke_type,
                 "dominant_hand": self.dominant_hand},
            )
        elif os.path.exists(reps_3d_path):
            os.remove(reps_3d_path)

    def _write_reports(self, reports, summary, date=None):
        from .report_builder import build_coach_report
        from .report_render import render_html, render_pdf
        from ..data.writer import write_json
        date = date or _today()
        lifted_count = sum(1 for r in reports if r.get("feature_space") == "3d")
        if lifted_count == 0:
            overall_feature_space = "2d"
        elif lifted_count == len(reports):
            overall_feature_space = "3d"
        else:
            overall_feature_space = "mixed"
        meta = {"date": date, "stroke_type": self.stroke_type,
                "dominant_hand": self.dominant_hand,
                "pose_family": getattr(self, "pose_family", "yolo-pose"),
                "feature_space": overall_feature_space}
        by_lang = build_coach_report(reports, summary, meta)
        if getattr(self, "report_llm", "off") not in (None, "off"):
            from .report_llm import polish
            for lang in by_lang:
                by_lang[lang] = polish(by_lang[lang], lang, spec=self.report_llm)
        written = {}
        for lang, report in by_lang.items():
            json_path = os.path.join(self.save_dir, "coach_report_" + lang + ".json")
            html_path = os.path.join(self.save_dir, "coach_report_" + lang + ".html")
            pdf_path = os.path.join(self.save_dir, "coach_report_" + lang + ".pdf")
            write_json(json_path, report)
            html = render_html(report)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            try:
                if not render_pdf(html, pdf_path):
                    print("coach report PDF skipped for " + lang + " (renderer unavailable)")
            except Exception as exc:
                # PDF is best-effort: never let a renderer failure abort the run.
                print("coach report PDF failed for " + lang + ": " + str(exc))
            written[lang] = json_path
        return written

    def process_video(self):
        import cv2
        from ..analysis import joint_angles as ja
        from ..analysis.biomechanics import BiomechanicalAnalyzer
        from ..visualization.technique_overlay import draw_technique_overlay
        from ..visualization.skeleton import draw_skeleton
        from ..media import video_audio as vap
        from ..data.writer import write_json

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError("Unable to open video: " + self.video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0

        pose = self._build_pose_processor()
        self._build_racket_detector()
        self._build_quality_scorer()
        self._build_pose_lifter()
        print(format_progress(5, "loading"), flush=True)
        ball_model = None
        if self.ball_model_path and os.path.exists(self.ball_model_path):
            from ultralytics import YOLO
            ball_model = YOLO(self.ball_model_path)

        temp_path = os.path.join(self.save_dir, "temp_detect_" + self.video_name + ".mp4")
        writer = vap.setup_video_writer(width, height, fps, temp_path)

        dom_wrist = ja.R_WRIST if self.dominant_hand == "right" else ja.L_WRIST
        frame_count = 0
        start = time.time()
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            if frame_count % 15 == 0:
                print(format_progress(analyzing_pct(frame_count, total_frames), "analyzing"), flush=True)
            self._capture_frame(frame, frame_count, pose, ball_model, dom_wrist,
                                ja, draw_technique_overlay, draw_skeleton)
            writer.write(frame)
            if self.show_display:
                cv2.imshow("posture", frame)
                cv2.waitKey(1)

        writer.release()
        time.sleep(0.5)
        cap.release()
        if self.show_display:
            cv2.destroyAllWindows()
        vap.process_video_without_audio(temp_path, self.output_video_path)

        print(format_progress(88, "scoring"), flush=True)
        runner = PostureRunner(BiomechanicalAnalyzer(dominant=self.dominant_hand),
                               stroke_type=self.stroke_type, dominant=self.dominant_hand,
                               quality_scorer=self._quality_scorer,
                               pose_lifter=self._pose_lifter, image_size=(width, height))
        reports, reps, gate_info = runner.run(self._track, self._frames.get, fps)

        write_rep_reports(os.path.join(self.save_dir, "drill_reps.jsonl"), reports)
        summary = build_drill_summary(reports, self.stroke_type)
        write_json(os.path.join(self.save_dir, "drill_summary.json"), summary)
        self._write_reps_3d_sidecar(gate_info)
        write_json(os.path.join(self.save_dir, "metadata.json"), {
            "video": {"path": self.video_path, "name": self.video_name,
                      "fps": float(fps), "width": width, "height": height},
            "mode": "posture", "stroke_type": self.stroke_type,
            "dominant_hand": self.dominant_hand,
            "pose_family": self.pose_family,
            "racket": {"detected_frames": self._racket_stats["detected"],
                       "inferred_frames": self._racket_stats["inferred"],
                       "model": self.racket_model_path},
            "quality": {"model": self.quality_model_path if self._quality_scorer else None,
                        "scored_reps": sum(1 for r in reports if "ai_score" in r)},
            "lift": {"model": self.lift_model_path if self._pose_lifter else None,
                     "scored_3d": gate_info.get("scored_3d", 0)},
            "reps": {"counted": gate_info["counted"],
                     "filtered_non_overhead": gate_info["filtered_non_overhead"],
                     "gated": gate_info["gated"],
                     "scored_3d": gate_info.get("scored_3d", 0)},
        })
        print(format_progress(94, "report"), flush=True)
        self._write_reports(reports, summary, date=_today())
        print("Posture analysis: " + str(len(reports)) + " reps -> " + self.save_dir)
        print("Elapsed: " + str(round(time.time() - start, 1)) + "s")
        print("Racket source: %d detected / %d inferred"
              % (self._racket_stats["detected"], self._racket_stats["inferred"]), flush=True)
        print("Overhead gate: %d counted, %d non-overhead excluded (stroke=%s)"
              % (gate_info["counted"], gate_info["filtered_non_overhead"], self.stroke_type), flush=True)
        return reports

    def _build_racket_detector(self):
        """Optional trained racket detector; analysis proceeds on failure."""
        if not self.racket_model_path:
            return
        try:
            from ..detection.racket import RacketDetector
            self._racket_detector = RacketDetector(model_path=self.racket_model_path, conf=RACKET_CONF)
        except Exception as e:
            print("Racket detector unavailable (" + str(e) + "); using wrist inference.")
            self._racket_detector = None

    def _build_quality_scorer(self):
        """Optional learned form scorer; analysis proceeds on failure."""
        if not self.quality_model_path or self.stroke_type != "high_clear":
            return
        try:
            from ..quality.scorer import QualityScorer
            scorer = QualityScorer(model_path=self.quality_model_path,
                                   stroke_type=self.stroke_type)
            self._quality_scorer = scorer if scorer.available else None
        except Exception as e:
            print("Quality scorer unavailable (" + str(e) + "); rule-based only.")
            self._quality_scorer = None

    def _build_pose_lifter(self):
        """Optional MotionBERT 3D lifter; analysis proceeds on failure."""
        if not self.lift_model_path:
            return
        try:
            from ..detection.pose_lift import PoseLifter
            lifter = PoseLifter(model_path=self.lift_model_path, device=self.lift_device)
            self._pose_lifter = lifter if lifter.available else None
        except Exception as e:
            print("Pose lifter unavailable (" + str(e) + "); 2D angles only.")
            self._pose_lifter = None

    def _resolve_racket_head(self, frame, kp, ja):
        # No pose this frame: there is no person bbox to anchor the ROI to (and no
        # wrist for the kinematic fallback), so skip detection entirely rather than
        # accept an ungated detection that could land on a racket-lookalike such as a
        # wall fan. Such frames are never contact frames, so this only keeps the
        # detected/inferred stats honest.
        if kp is None:
            return None, False
        if self._racket_detector is not None:
            head = self._racket_detector.detect_racket_head(frame, roi_corners=person_roi(kp))
            if head is not None:
                self._racket_stats["detected"] += 1
                return head, True
        self._racket_stats["inferred"] += 1
        return ja.infer_racket_head(kp, dominant=self.dominant_hand), False

    def _select_person(self, keypoints, frame_shape):
        """Pick which detected person to track this frame.

        Re-picking the largest keypoint spread independently every frame
        flips the pick in multi-person scenes (e.g. a feeder vs. the
        drilling player); each flip teleports the tracked wrist by hundreds
        of px. Instead, stay locked onto whoever is nearest the previous
        pick's center unless that jump is implausibly large (see
        PERSON_STICKY_MAX_JUMP_FRAC), in which case re-acquire via
        largest-spread (initial pick, track loss, or a hard cut).
        """
        centers = [_person_center(p) for p in keypoints]
        if self._person_anchor is not None:
            height, width = frame_shape[0], frame_shape[1]
            max_jump = PERSON_STICKY_MAX_JUMP_FRAC * float(np.hypot(width, height))
            nearest_i = min(range(len(centers)),
                            key=lambda i: _center_dist(centers[i], self._person_anchor))
            if _center_dist(centers[nearest_i], self._person_anchor) <= max_jump:
                self._person_anchor = centers[nearest_i]
                return nearest_i
        best_i = max(range(len(keypoints)), key=lambda i: _spread(keypoints[i]))
        self._person_anchor = centers[best_i]
        return best_i

    def _capture_frame(self, frame, frame_count, pose, ball_model, dom_wrist,
                       ja, draw_technique_overlay, draw_skeleton):
        keypoints, scores = pose.process_frame(frame)
        kp = None
        wrist = None
        racket_head = None
        centroid = None
        conf_row = None
        if keypoints is not None and len(keypoints) > 0:
            # Single-player drill: sticky selection avoids flipping between
            # people frame to frame (see _select_person).
            best_i = self._select_person(keypoints, frame.shape)
            kp = keypoints[best_i].astype(float)
            conf_row = scores[best_i] if scores is not None else None
        # Detector must see the clean frame (before any overlay drawing), and runs
        # even when pose detection failed - mirroring the match pipeline.
        racket_head, racket_detected = self._resolve_racket_head(frame, kp, ja)
        if kp is not None:
            draw_skeleton(frame, kp, conf=conf_row)
            if ja.is_valid(kp, dom_wrist, conf_row):
                wrist = (float(kp[dom_wrist][0]), float(kp[dom_wrist][1]))
            # centroid = hip midpoint when available, else mean of valid points
            if ja.is_valid(kp, ja.L_HIP, conf_row) and ja.is_valid(kp, ja.R_HIP, conf_row):
                centroid = (float((kp[ja.L_HIP][0] + kp[ja.R_HIP][0]) / 2),
                            float((kp[ja.L_HIP][1] + kp[ja.R_HIP][1]) / 2))
            if self.show_overlay:
                angles = ja.compute_joint_angles(kp, racket_head=racket_head,
                                                 dominant=self.dominant_hand, conf=conf_row)
                draw_technique_overlay(frame, angles)

        shuttle = None
        if ball_model is not None:
            try:
                res = ball_model(frame, conf=0.18, verbose=False)[0]
                boxes = getattr(res, "boxes", None)
                if boxes is not None and boxes.xywh.shape[0] > 0:
                    # Nearest the dominant wrist, not whichever box came back first:
                    # this detection is the primary contact anchor downstream.
                    shuttle = pick_shuttle(boxes.xywh.detach().cpu().numpy(), wrist)
            except Exception:
                shuttle = None

        self._track.append({"frame": frame_count, "wrist": wrist, "shuttle": shuttle})
        self._frames[frame_count] = {
            "frame": frame_count, "keypoints": kp, "conf": conf_row,
            "racket_head": racket_head, "centroid": centroid,
            "racket_head_detected": racket_detected,
        }
