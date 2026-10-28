import os
import json
import tempfile
from tkinter import filedialog
import tkinter as tk
import time
import argparse
import threading

# TrackNetV3 dense pre-pass modes. 'weight' (upstream default, temporal
# ensemble) costs ~0.48 s/frame at 1080p on an RTX 4090; 'nonoverlap' ~0.18 s/frame.
# A video longer than this is not tracked by TrackNetV3 (the pre-pass is skipped and the
# yolo shuttle is used) -- but only when no hit model is configured and
# ``dense_whole_video`` is False. With either set the whole video is tracked, because the
# learned hit detector needs a dense track (learned-hit-detection spec 8.1); the cache
# makes re-runs free.
SHUTTLE_PRETRACK_MAX_FRAMES = 2000
TRACKNET_MODES = ("weight", "nonoverlap")
MATCH_FORMATS = ("singles", "doubles")

# Interval of the *pinned* gate only: when court_view_threshold is pinned the gate
# stays exactly today's full-resolution match, recomputed every N frames with the
# result held between checks (lossless within the 5-frame rally thresholds). The
# default (calibrated) gate scores a 480px downscale on every frame instead.
COURT_VIEW_CHECK_INTERVAL = 3

# C0 self-calibrating court-view cut (spec §5 C0). The gate's NCC score has a
# video-dependent absolute scale, so the cut is derived per video from a
# strided pre-scan instead of the one global 0.75 (which admitted 0.66% of the
# Axelsen match).
COURT_VIEW_SCORE_WIDTH = 480      # downscale width for the gate's NCC (§0.9 signal D)
COURT_VIEW_MAD_K = 4.0            # cut = median - k * 1.4826 * MAD (§0.6)
COURT_VIEW_MIN_MEDIAN = 0.30      # below this the template is not of this video
COURT_VIEW_FALLBACK_CUT = 0.75    # the historically shipped constant
COURT_VIEW_CALIBRATION_STRIDE_SEC = 0.5   # ~2 samples/second
COURT_VIEW_CALIBRATION_MAX_SAMPLES = 1500
# Shift tolerance of the unpinned gate score, in pixels AT THE 480px GATE WIDTH
# (~ +/-32 px at 4K): the once-downscaled template is cropped by this many pixels on
# every side and slid over the frame, taking the best NCC. Chosen to absorb the
# camera/gimbal settling drift measured on DJI 0010 (the image sits ~20 px at 4K
# left of the template frame at t=0 and settles by ~60 s); INTER_AREA makes aligned
# frames score in a tight cluster, so without it a slightly misaligned full-court
# frame fell > 4 robust SD below the median and was rejected (87.6% pass).
COURT_VIEW_SHIFT_PX = 4

# Fast mode analyzes every Nth court frame (quick look).
FAST_FRAME_STRIDE = 3

# Nearest-in-ROI-racket-detection-to-player-centroid gate for the
# both-player capture (Task 6). Deliberately dumb/untuned v1 constant, same
# convention as contact_px in stroke/events.py -- project 2 measures
# accuracy against ground truth, not this plan.
RACKET_TO_PLAYER_MAX_PX = 300.0

# Nearest-pose-person-to-player-centroid gate for the both-player capture's
# per-side pose matching (_capture_side_poses). Deliberately dumb/untuned v1
# constant in the same spirit as contact_px and RACKET_TO_PLAYER_MAX_PX --
# project 2 measures accuracy against ground truth, not this plan.
#
# Held to a TIGHTER tolerance than RACKET_TO_PLAYER_MAX_PX on purpose: a
# racket head can legitimately sit a full arm+racket extension away from its
# owner's feet, whereas the quantity compared here is a foot-midpoint against
# a tracked centroid that is itself a foot-midpoint (see
# visualization/player_pose.py::detect_players, which feeds PlayerTracker
# centroids computed as the ankle midpoint + 10px in y). For the player the
# tracker actually locked onto this frame the two agree to within ~10px; the
# slack here only covers a stale centroid held from an earlier frame while
# that side went briefly undetected. Without this gate, a frame with only one
# detected person would assign that SAME person to BOTH sides, because min()
# over a one-element list always returns it regardless of distance.
POSE_TO_PLAYER_MAX_PX = 150.0


def _sq_dist(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _assign_one_to_one(side_points, candidates, max_sq):
    """Assign at most one candidate index to each side, and each candidate to at
    most one side, pairing closest-first.

    ``side_points`` is {side: (x, y)}; ``candidates`` is a list where an entry may
    be None (unusable, e.g. a detected person with no valid ankle). Pairs further
    apart than ``max_sq`` (squared px) are not eligible at all. Returns
    {side: candidate index}, omitting sides that won nothing.

    Closest-first is what makes this one-to-one rather than two independent
    nearest-neighbour searches: with two sides competing for the same detection,
    the nearer side takes it and the other is left unmatched instead of both
    silently sharing it. Both callers (pose people, racket boxes) need exactly
    that, so the rule lives here once.
    """
    pairs = []
    for side, pt in side_points.items():
        if pt is None:
            continue
        for i, c in enumerate(candidates):
            if c is None:
                continue
            d = _sq_dist(c, pt)
            if d <= max_sq:
                pairs.append((d, side, i))
    pairs.sort(key=lambda p: p[0])
    chosen, used_sides, used_cands = {}, set(), set()
    for _d, side, i in pairs:
        if side in used_sides or i in used_cands:
            continue
        chosen[side] = i
        used_sides.add(side)
        used_cands.add(i)
    return chosen


def downscale_for_gate(gray, width=COURT_VIEW_SCORE_WIDTH):
    """Downscale a grayscale image to ``width``, preserving aspect ratio.

    The gate compares whole-frame framing, not fine court lines, so 480px
    wide is ample and far cheaper than full resolution (§0.9 signal D).
    Never upscales -- an already-small frame is returned untouched.
    """
    import cv2

    h, w = gray.shape[:2]
    if w <= width:
        return gray
    scale = width / float(w)
    return cv2.resize(gray, (width, max(1, int(round(h * scale)))),
                      interpolation=cv2.INTER_AREA)


def courtview_cut_from_scores(scores, k=COURT_VIEW_MAD_K):
    """Per-video court-view cut from a sample of whole-frame NCC scores.

    Returns the audit record written to metadata.json's ``court_view`` key.
    Pure and video-free so the branch logic is testable without decoding.

    ``method`` is always the estimator that was attempted ("median-4mad");
    ``calibration`` names what actually happened:
      * "median-4mad"                 -> normal: cut = median - k*1.4826*MAD
        (MAD collapsing to 0 uses the median itself, not median - 0)
      * "fallback_constant"           -> no finite samples; the shipped 0.75
      * "template_mismatch_pass_all"  -> median below the floor: the template
        is not of this video, so the cut is -inf and everything passes

    Non-finite scores (NaN from a constant frame, inf) are dropped first, so
    ``samples`` counts only the scores that informed the cut.
    """
    import math

    method = f"median-{float(k):g}mad"
    vals = sorted(f for f in (float(s) for s in scores) if math.isfinite(f))
    n = len(vals)
    if n == 0:
        return {"cut": COURT_VIEW_FALLBACK_CUT, "median": None, "mad": None,
                "samples": 0, "method": method,
                "calibration": "fallback_constant", "k": float(k)}

    median = vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])
    if median < COURT_VIEW_MIN_MEDIAN:
        return {"cut": float("-inf"), "median": median, "mad": None,
                "samples": n, "method": method,
                "calibration": "template_mismatch_pass_all", "k": float(k)}

    devs = sorted(abs(v - median) for v in vals)
    mad = devs[n // 2] if n % 2 else 0.5 * (devs[n // 2 - 1] + devs[n // 2])
    cut = median - float(k) * 1.4826 * mad if mad > 0 else median
    cut = max(0.0, min(0.95, cut))
    return {"cut": cut, "median": median, "mad": mad, "samples": n,
            "method": method, "calibration": method, "k": float(k)}


def cap_court_view_cut(calibration, cap=COURT_VIEW_FALLBACK_CUT):
    """Never let a calibrated cut be stricter than ``cap`` (the shipped 0.75).

    Returns a copy of ``calibration`` (from ``courtview_cut_from_scores``) with
    two audit keys added: ``capped`` and ``calibrated_cut`` (the cut before
    capping). Only the normal estimator path ("median-4mad") is capped;
    ``fallback_constant``, ``template_mismatch_pass_all`` and manual pins are
    returned unchanged with ``capped`` False and ``calibrated_cut`` None.
    """
    out = dict(calibration)
    out["capped"] = False
    out["calibrated_cut"] = None
    if str(out.get("calibration", "")).startswith("median-"):
        out["calibrated_cut"] = out["cut"]
        if out["cut"] > cap:
            out["cut"] = cap
            out["capped"] = True
    return out


def load_runtime_dependencies():
    """Load heavy runtime dependencies after argparse has handled --help."""
    global cv2, np, YOLO, CourtMapper, annotate_court, compute_expanded_roi, PlayerTracker
    global CourtTrajectoryVisualizer, ShuttlecockTracker
    global PlayerPoseVisualizer, StatsVisualizer, RTMPoseProcessor, YOLOPoseProcessor, vap
    global JsonlDetectionWriter, write_json, SCHEMA_VERSION

    yolo_config_dir = os.path.join(tempfile.gettempdir(), "good-badminton-ultralytics")
    os.makedirs(yolo_config_dir, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", yolo_config_dir)

    try:
        import cv2 as _cv2
        import numpy as _np
        from ultralytics import YOLO as _YOLO
        from .court.mapper import CourtMapper as _CourtMapper, annotate_court as _annotate_court
        from .court.mapper import compute_expanded_roi as _compute_expanded_roi
        from .tracking.player import PlayerTracker as _PlayerTracker
        from .visualization.court_trajectory import CourtTrajectoryVisualizer as _CourtTrajectoryVisualizer
        from .detection.shuttlecock import ShuttlecockTracker as _ShuttlecockTracker
        from .visualization.player_pose import PlayerPoseVisualizer as _PlayerPoseVisualizer
        from .visualization.stats import StatsVisualizer as _StatsVisualizer
        from .detection.rtmpose import RTMPoseProcessor as _RTMPoseProcessor
        from .detection.yolo_pose import YOLOPoseProcessor as _YOLOPoseProcessor
        from .media import video_audio as _vap
        from .data.writer import JsonlDetectionWriter as _JsonlDetectionWriter
        from .data.writer import write_json as _write_json
        from .data.writer import SCHEMA_VERSION as _SCHEMA_VERSION
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            f"Missing Python dependency: {exc.name}. "
            "Install dependencies with: pip install -r requirements.txt"
        ) from exc

    cv2 = _cv2
    np = _np
    YOLO = _YOLO
    CourtMapper = _CourtMapper
    annotate_court = _annotate_court
    compute_expanded_roi = _compute_expanded_roi
    PlayerTracker = _PlayerTracker
    CourtTrajectoryVisualizer = _CourtTrajectoryVisualizer
    ShuttlecockTracker = _ShuttlecockTracker
    PlayerPoseVisualizer = _PlayerPoseVisualizer
    StatsVisualizer = _StatsVisualizer
    RTMPoseProcessor = _RTMPoseProcessor
    YOLOPoseProcessor = _YOLOPoseProcessor
    vap = _vap
    JsonlDetectionWriter = _JsonlDetectionWriter
    write_json = _write_json
    SCHEMA_VERSION = _SCHEMA_VERSION

class _ProgressHeartbeat:
    """Rewrites progress.json every ``interval`` s from a background thread.

    A whole-match TrackNetV3 pass runs for hours with batches that can take longer
    than the UI's staleness window; this keeps "updated Ns ago" fresh and carries the
    latest frame count, independent of how often the tracker reports.
    """

    def __init__(self, write, stage, interval=2.0, substage=None):
        self._write = write
        self._stage = stage
        self._interval = interval
        # Passed only when set, so callers without sub-stages see the old call.
        self._extra = {"substage": substage} if substage else {}
        self.current = 0
        self._stop = threading.Event()
        self._thread = None

    def update(self, done, total=None):
        self.current = int(done)

    def __enter__(self):
        self._write(self._stage, current_frame=0, **self._extra)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self._interval):
            self._write(self._stage, current_frame=self.current, **self._extra)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._write(self._stage, current_frame=self.current, **self._extra)
        return False


class BadmintonAnalysisSystem:
    def __init__(self, video_path, show_display=True,
                 show_skeletons=True, show_player_trajectories=True,
                 show_court_trajectory=True, show_shuttlecock_trajectory=True,
                 show_player_stats=True, show_performance_stats=False,
                 save_images=False, language='zh', output_dir=None,
                 ball_model_path='weights/yolo11s-ball.pt', template_path=None,
                 pose_mode='balanced', pose_family='rtmpose',
                 yolo_pose_model='yolo11n-pose.pt', show_pose_roi=True,
                 analyze_technique=False, racket_model_path=None, dominant_hand="right",
                 stroke_model_path=None, tracknet_weights=None, inpaintnet_weights=None,
                 tracknet_mode="weight",
                 analysis_quality="accurate", court_view_threshold=None,
                 rally_signal="auto", hit_model_path=None,
                 dense_whole_video=False, match_format="singles"):
        self.video_path = video_path
        self.match_format = self._normalize_match_format(match_format)
        self.show_display = show_display
        self.language = language
        self.template_path = template_path
        self.ball_model_path = ball_model_path
        self.pose_mode = pose_mode
        self.pose_family = pose_family
        self.yolo_pose_model = yolo_pose_model
        self.show_pose_roi = show_pose_roi
        self.analyze_technique = analyze_technique
        self.analysis_quality = analysis_quality
        # C0: None -> calibrate the court-view cut per video; a number pins it
        # (recorded as method "manual") and skips calibration entirely.
        self.court_view_threshold_override = court_view_threshold
        self.court_view_cut = COURT_VIEW_FALLBACK_CUT
        self.court_view_calibration = None
        # True only for a usable manual pin: the gate then keeps today's
        # full-resolution, every-COURT_VIEW_CHECK_INTERVAL-frames behaviour.
        self.court_view_pinned = False
        self.court_view_template_small = None
        # B11: "auto" | "shuttle" | "swing" go to segment_rallies; "courtview"
        # writes the legacy in-loop court-view segments. Anything else is "auto".
        self.rally_signal = rally_signal
        self.rally_detection = None   # the detection block written to rally_segments.json
        self.hit_model_path = hit_model_path
        # True makes the TrackNetV3 pre-pass track the whole video regardless of
        # length (the hit-dataset builder needs dense shuttle on every rally).
        self.dense_whole_video = dense_whole_video
        self.rally_list = []          # the rallies written to rally_segments.json
        # Frames the court-view gate evaluated / passed, for the provenance block.
        self._gate_frames = 0
        self._gate_court_frames = 0
        if analysis_quality == "fast":
            self.analyze_technique = False  # dense analytics need every frame
        self.racket_model_path = racket_model_path
        self.dominant_hand = dominant_hand
        self.stroke_model_path = stroke_model_path
        # the owner's stroke corrections, re-applied after every strokes.json write
        self.stroke_labels_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "stroke_labels")
        self.tracknet_weights = tracknet_weights
        self.inpaintnet_weights = inpaintnet_weights
        self.tracknet_mode = self._normalize_tracknet_mode(tracknet_mode)
        self._shuttle_trajectory = None
        self._shuttle_source = "yolo"
        self._analysis_track = []   # contact detection track
        self._analysis_track_both = []  # both-player contact track (contacts and hit list)
        # Per-court-frame track for post-loop rally segmentation. Recorded on EVERY
        # run (technique analysis on or off, fast or accurate), unlike the two
        # tracks above: it is what makes rally windows computable afterwards.
        self._rally_track = []
        self._rally_track_error_logged = False
        self._analysis_frames = {}  # frame_index -> window-frame record; grows one entry per court frame (memory ~scales with video length); acceptable for typical clips
        self._racket_detector = None

        self.show_skeletons = show_skeletons
        self.show_player_trajectories = show_player_trajectories
        self.show_court_trajectory = show_court_trajectory
        self.show_shuttlecock_trajectory = show_shuttlecock_trajectory
        self.show_player_stats = show_player_stats
        self.show_performance_stats = show_performance_stats
        self.save_images = save_images  

        if not os.path.exists(self.video_path):
            raise FileNotFoundError(
                f"Input video not found: {self.video_path}\n"
                "Pass a valid video file with --video-path."
            )
        if not os.path.exists(self.ball_model_path):
            raise FileNotFoundError(
                f"Ball detection model not found: {self.ball_model_path}\n"
                "Download or train a YOLO shuttlecock model and place it at "
                "weights/yolo11s-ball.pt, or pass its path with --ball-model."
            )
        
        if self.pose_family == 'yolo-pose':
            self.rtmpose_processor = YOLOPoseProcessor(model_path=self.yolo_pose_model)
        else:
            self.rtmpose_processor = RTMPoseProcessor(mode=self.pose_mode, pose_family=self.pose_family)
        self.yolo_ball_model = YOLO(self.ball_model_path)

        self._capture_frames = self._capture_frames_for(
            self.analyze_technique, self.match_format, self.analysis_quality)
        self._doubles = None
        self._doubles_frames = 0

        if self._capture_frames:
            from .detection.racket import RacketDetector
            try:
                self._racket_detector = RacketDetector(model_path=self.racket_model_path)
            except Exception as e:
                print(f"Racket detector unavailable ({e}); using wrist inference.")
                self._racket_detector = None

        self.last_stats_update_frame = 0


        self.video_path = video_path
        self.video_name = os.path.basename(self.video_path)[:-4]
        self.save_dir = output_dir or os.path.join('outputs', self.video_name)
        os.makedirs(self.save_dir, exist_ok=True)
        self._total_frames = 0
        self._write_progress("initializing")
        self.images_save_dir = os.path.join(self.save_dir, 'detect_images')
        os.makedirs(self.images_save_dir, exist_ok=True)
        

        self.metadata_path = os.path.join(self.save_dir, "metadata.json")
        self.detections_path = os.path.join(self.save_dir, "detections.jsonl")
        self.output_video_path = os.path.join(self.save_dir, f"detect_{self.video_name}.mp4")
        self.detection_writer = None
        

        self.player_1_hand = "right"  
        self.player_2_hand = "right"  
        self.start_time = None
        self.end_time = None
        

        self.shuttlecock_tracker = ShuttlecockTracker(
            yolo_ball_model=self.yolo_ball_model,
            trajectory_length=30,
            show_trajectory=self.show_shuttlecock_trajectory,
            show_performance_stats=False
        )
        
        self.player_pose_visualizer = PlayerPoseVisualizer(
            rtmpose_processor=self.rtmpose_processor,
            show_skeletons=self.show_skeletons,
            show_player_trajectories=self.show_player_trajectories,
            show_performance_stats=False
        )
        

        self.court_trajectory_visualizer = CourtTrajectoryVisualizer()
        

        self.stats_update_interval_frames = 0
        self.cached_movement_stats = {}

        self.is_court_view_count = 0
        self.consecutive_non_court_frames = 0
        self._court_view_cached = None
        self._court_view_last_frame = -10
        self.rally_active = False
        self.rally_count = 0
        self.rally_segments = []  # [(rally_id, start_frame, end_frame), ...]
        self._current_rally_start = 0
        self.fps = 30  
        self.court_view_frames_threshold = 5
        self.non_court_frames_threshold = 5

        self.frame_width = 0
        self.frame_height = 0
        self.performance_log_interval_frames = 150
    def process_video(self):
        """Process the input video."""
        self.start_time = time.time()

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Unable to open video: {self.video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self._total_frames = total_frames
        if fps <= 0:
            raise RuntimeError(f"Unable to read FPS from video: {self.video_path}")
        video_duration = total_frames / fps
        

        self.fps = fps
        self.performance_log_interval_frames = max(1, int(fps * 5))
        

        template_path = self._get_template_path()
        template_gray, template_color, template_small = self._load_template(template_path, cap)
        self.court_view_template_small = template_small
        

        self.frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = self._setup_video_writer(self.frame_width, self.frame_height, fps)


        corners, roi_corners, mid_height = self._setup_court_annotation(template_color)
        self.court_corners = corners
        self.court_roi_corners = roi_corners
        self._write_progress("court_setup")
        progress_interval = max(1, int(fps))

        # Calibration is a 30-50 s pre-scan, so it runs only after court annotation
        # has succeeded (a failed annotation raises before any of that work) and
        # after a progress update; it must still precede _write_metadata, which
        # records the cut (R14). Never fatal.
        self._write_progress("calibrating_court_view")
        self._prepare_court_view_cut(template_small, fps)

        self._write_metadata(fps, total_frames, video_duration, template_path, corners, roi_corners, mid_height)
        self._run_shuttle_pretrack()
        self.detection_writer = JsonlDetectionWriter(self.detections_path)


        self.court_mapper = CourtMapper(corners)
        self.player_pose_visualizer.court_mapper = self.court_mapper
        self.player_tracker = PlayerTracker(corners=corners, threshold=mid_height, history_size=30,
                                          detection_writer=self.detection_writer, fps=fps)
        

        self._init_far_pose(corners, (self.frame_height, self.frame_width), fps)
        self._init_doubles(corners, fps)

        self.stats_visualizer = StatsVisualizer(
            frame_width=self.frame_width,
            frame_height=self.frame_height,
            language=self.language
        )

        frame_count = 0
        detect_frame_count = 0


        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            frame_count += 1
            if frame_count % progress_interval == 0:
                self._write_progress("analyzing", current_frame=frame_count)
            frame, detect_frame_count = self._process_frame(frame, template_gray, corners, roi_corners, frame_count, out, detect_frame_count)

        # Post-loop: segment the recorded track and write rally_segments.json.
        self._write_rally_segments(fps, frame_count)

        self.end_time = time.time()
        processing_time = self.end_time - self.start_time
        
        print(f"\n处理完成:")
        print(f"原始视频时长: {video_duration:.2f} 秒")
        print(f"处理耗时: {processing_time:.2f} 秒")
        print(f"处理速度比: {processing_time/video_duration:.2f}x")

        self._write_progress("visualizing")
        self._run_hit_detection(fps, frame_count)
        self._apply_learned_segments(fps)
        if self.match_format == "doubles":
            self._withhold_singles_outputs_for_doubles()
        else:
            labels = self._run_stroke_classification()
            if self.analyze_technique:
                self._run_technique_analysis(labels)
        self._run_doubles_stage(fps, self._doubles_frames)
        self._write_rally_outcomes()

        self._cleanup(cap)

    def _write_rally_outcomes(self):
        """Write rally_outcomes.json from the run files and the owner's labels; never fatal."""
        try:
            from .outcomes.build import build
            build(self.save_dir)
        except Exception as exc:
            print(f"rally_outcomes.json could not be written: {exc!r}")

    def _court_view_score(self, gray_frame, template_small):
        """Shift-tolerant whole-frame NCC at COURT_VIEW_SCORE_WIDTH. Shared by
        calibration (C0) and the live unpinned gate (C1), so both see one scale.

        The once-downscaled template is cropped by COURT_VIEW_SHIFT_PX on every
        side and slid over the frame; the best NCC is returned, so a frame that
        has drifted up to that many pixels (at 480 wide) still scores as a match.
        A template too small to crop falls back to the equal-size score. A
        non-finite maximum is returned as is (calibration drops it; the live
        gate treats it as not court view).
        """
        import cv2
        import numpy as np

        small = downscale_for_gate(gray_frame)
        if small.shape != template_small.shape:
            template_small = cv2.resize(template_small,
                                        (small.shape[1], small.shape[0]),
                                        interpolation=cv2.INTER_AREA)
        m = COURT_VIEW_SHIFT_PX
        h, w = template_small.shape[:2]
        if h >= 2 * m + 1 and w >= 2 * m + 1:
            template_small = template_small[m:h - m, m:w - m]
        result = cv2.matchTemplate(small, template_small, cv2.TM_CCOEFF_NORMED)
        return float(np.max(result))

    @staticmethod
    def _usable_court_view_pin(value):
        """A pin is usable only as a finite number in [0.0, 1.0]."""
        import math

        if isinstance(value, bool):
            return None
        try:
            pin = float(value)
        except (TypeError, ValueError):
            return None
        return pin if math.isfinite(pin) and 0.0 <= pin <= 1.0 else None

    def _prepare_court_view_cut(self, template_small, fps):
        """Decide this run's court-view cut; never fatal.

        ``template_small`` is the template already downscaled by _load_template
        (downscaled once, never again). A usable pinned ``court_view_threshold``
        wins and skips calibration. An unusable pin (NaN, non-numeric, outside
        [0, 1]) is warned about and ignored in favour of normal calibration.
        Calibration failure logs and falls back to the shipped constant so the
        run continues.

        Also decides the live gate's mode: only a usable pin keeps the legacy
        full-resolution gate (``court_view_pinned``).
        """
        self.court_view_pinned = False
        if self.court_view_threshold_override is not None:
            pin = self._usable_court_view_pin(self.court_view_threshold_override)
            if pin is not None:
                self.court_view_pinned = True
                self.court_view_cut = pin
                self.court_view_calibration = {
                    "cut": pin, "median": None, "mad": None, "samples": 0,
                    "method": "manual", "calibration": "manual", "k": None,
                    "capped": False, "calibrated_cut": None}
                print(f"Court-view gate: cut={pin} (manual)")
                return
            print(f"Court-view threshold {self.court_view_threshold_override!r} "
                  f"is not a finite number in [0, 1]; calibrating instead")
        try:
            self._calibrate_court_view(self.video_path, template_small, fps)
        except Exception as exc:
            print(f"Court-view calibration failed, using "
                  f"{COURT_VIEW_FALLBACK_CUT}: {exc}")
            self.court_view_calibration = cap_court_view_cut(
                courtview_cut_from_scores([]))
            self.court_view_cut = self.court_view_calibration["cut"]

    def _calibrate_court_view(self, video_path, template_small, fps):
        """Strided pre-scan deriving this video's court-view cut.

        Every frame is grabbed, and with the FFmpeg backend ``grab()`` decodes
        the frame; only the sampled frames are retrieved and scored. Measured
        cost is therefore ~31-51 s per video (17,234 grabs took ~51 s), not
        the cost of the sampled frames alone. Any failure falls back to the
        shipped constant and says so.
        """
        import cv2

        scores = []
        cap = None
        try:
            cap = cv2.VideoCapture(video_path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            stride = max(1, int(round(COURT_VIEW_CALIBRATION_STRIDE_SEC * (fps or 30))))
            if total > 0:
                # ceil, so the sample budget spans the whole video.
                stride = max(stride, -(-total // COURT_VIEW_CALIBRATION_MAX_SAMPLES))
            idx = 0
            while len(scores) < COURT_VIEW_CALIBRATION_MAX_SAMPLES:
                if not cap.grab():
                    break
                if idx % stride == 0:
                    ok, frame = cap.retrieve()
                    if ok:
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        scores.append(self._court_view_score(gray, template_small))
                idx += 1
        except Exception as exc:
            print(f"Court-view calibration failed, using {COURT_VIEW_FALLBACK_CUT}: {exc}")
            scores = []
        finally:
            if cap is not None:
                cap.release()

        self.court_view_calibration = cap_court_view_cut(
            courtview_cut_from_scores(scores))
        self.court_view_cut = self.court_view_calibration["cut"]
        capped = self.court_view_calibration["capped"]
        print(f"Court-view gate: cut={self.court_view_cut} "
              f"({self.court_view_calibration['calibration']}, "
              f"{self.court_view_calibration['samples']} samples"
              + (f", capped from {self.court_view_calibration['calibrated_cut']}"
                 if capped else "") + ")")

    @staticmethod
    def _normalize_match_format(value):
        """None -> "singles"; anything outside MATCH_FORMATS raises ValueError."""
        if value is None:
            return "singles"
        if value not in MATCH_FORMATS:
            raise ValueError(f"match_format must be one of {MATCH_FORMATS}, got {value!r}")
        return value

    def _court_view_metadata(self):
        """The ``court.court_view`` record for metadata.json.

        Non-finite numbers (a pass-all cut of -inf) become null: the browser's
        JSON parser rejects ``-Infinity``.
        """
        import math

        cal = self.court_view_calibration
        if cal is None:
            return None

        def finite_or_none(value):
            if value is None:
                return None
            value = float(value)
            return value if math.isfinite(value) else None

        return {
            "cut": finite_or_none(cal.get("cut")),
            "method": cal.get("method"),
            "calibration": cal.get("calibration"),
            "k": finite_or_none(cal.get("k")),
            "samples": int(cal.get("samples", 0)),
            "median": finite_or_none(cal.get("median")),
            "mad": finite_or_none(cal.get("mad")),
            "capped": bool(cal.get("capped", False)),
            "calibrated_cut": finite_or_none(cal.get("calibrated_cut")),
        }

    RALLY_SIGNALS = ("auto", "shuttle", "swing", "courtview")

    def _rally_court_view_block(self):
        """The ``detection.court_view`` record: the gate's cut, how it was
        chosen, and how many evaluated frames passed it. Never raises (None if
        the record cannot be built); non-finite numbers become null."""
        try:
            meta = self._court_view_metadata() or {}
            frames = int(getattr(self, "_gate_frames", 0))
            court_frames = int(getattr(self, "_gate_court_frames", 0))
            return {
                "cut": meta.get("cut"),
                "method": meta.get("method"),
                "calibration": meta.get("calibration"),
                "capped": bool(meta.get("capped", False)),
                "calibrated_cut": meta.get("calibrated_cut"),
                "pass_frac": court_frames / frames if frames > 0 else None,
                "court_frames": court_frames,
                "frames": frames,
            }
        except Exception:
            return None

    def _rally_segments_for_output(self, fps, last_frame):
        """``(rallies, detection)`` for rally_segments.json, per ``rally_signal``.

        ``"courtview"`` is the legacy behaviour: the in-loop state machine's
        segments, with a rally still open at the end of the video closed at
        ``last_frame``. Everything else is ``segment_rallies`` over the
        recorded track (an unknown value is ``"auto"``, and says so).
        """
        mode = getattr(self, "rally_signal", "auto")
        note = None
        if mode not in self.RALLY_SIGNALS:
            note = f"unknown rally_signal {mode!r} treated as 'auto'"
            mode = "auto"

        if mode == "courtview":
            pairs = [tuple(r) for r in self.rally_segments]
            if self.rally_active and last_frame is not None:
                pairs.append((self.rally_count, self._current_rally_start, last_frame))
            rallies = [{"id": r[0], "start_frame": r[1], "end_frame": r[2],
                        "start_sec": r[1] / fps, "end_sec": r[2] / fps,
                        "degraded": False} for r in pairs]
            detection = {"signal": "courtview", "attempted_signal": None,
                         "reason": "rally_signal='courtview': in-loop court-view "
                                   "state machine (legacy behaviour)",
                         "degraded": False}
            return rallies, detection

        from .stroke import rallies as rallies_mod

        segments, detection = rallies_mod.segment_rallies(
            self._rally_track, fps, signal=mode,
            quad=getattr(self, "court_corners", None))
        detection = dict(detection)
        if note:
            detection["reason"] = "; ".join(
                part for part in (detection.get("reason"), note) if part)
        rallies = [{"id": s["id"], "start_frame": s["start_frame"],
                    "end_frame": s["end_frame"], "start_sec": s["start_sec"],
                    "end_sec": s["end_sec"], "degraded": s["degraded"]}
                   for s in segments]
        return rallies, detection

    def _write_rally_segments(self, fps, last_frame=None):
        """Segment rallies from the recorded track and write rally_segments.json.

        Post-loop by design: nothing in the frame loop consumes rally segments
        live. Never fatal: if anything around the segmenter (or the write)
        raises, ``rallies`` is empty and ``detection`` is ``signal: "error"``,
        ``degraded: False`` (an error is not a degraded segmentation) with only
        the exception type as its reason; the full exception goes to the
        console once. ``self.rally_detection`` is the detection block written.
        """
        import traceback

        from .data.writer import write_json as _write_json

        path = os.path.join(self.save_dir, "rally_segments.json")
        court_view = self._rally_court_view_block()
        error = None
        try:
            rallies, detection = self._rally_segments_for_output(fps, last_frame)
            detection["court_view"] = court_view
            # Recorded, not inferred: stroke labels are withheld exactly when
            # the segmentation is degraded (an error or courtview is not).
            detection["stroke_labels_withheld"] = bool(detection.get("degraded"))
            _write_json(path, {"fps": fps, "rallies": rallies, "detection": detection})
        except Exception as exc:
            error = exc
            print(f"Rally segmentation failed, writing an empty result:\n"
                  f"{traceback.format_exc()}")
            rallies = []
            detection = {"signal": "error", "degraded": False, "error": True,
                         "reason": type(exc).__name__, "court_view": court_view,
                         "stroke_labels_withheld": False}
            try:
                _write_json(path, {"fps": fps, "rallies": rallies,
                                   "detection": detection})
            except Exception as write_exc:
                print(f"Rally segments could not be written: {write_exc!r}")

        self.rally_detection = detection
        self.rally_list = list(rallies)
        line = (f"Rally segmentation: signal={detection.get('signal')}, "
                f"{len(rallies)} segments")
        if error is not None:
            line += f", error: {detection['reason']}"
        elif detection.get("error"):
            # segment_rallies' own failure result: an error, not a degradation.
            line += f", error: {detection.get('reason')}"
        elif detection.get("degraded"):
            line += f", degraded: {detection.get('reason')}"
        print(line)

    def _rally_segmenter_path(self):
        """The learned segmenter's weights: the injected path, else ``weights/rally-segmenter.json``."""
        path = getattr(self, "rally_segmenter_path", None)
        if path:
            return path
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "weights", "rally-segmenter.json")

    def _apply_learned_segments(self, fps):
        """Replace the swing segments with the learned segmenter's, when a kept model exists.

        Post-step by design: rally_segments.json is written before the frame loop's detections
        and the hit list exist, and the segmenter reads ``hits.json`` / ``detections.jsonl`` /
        ``metadata.json`` / ``shuttle_trajectory.json`` back from the result dir (the same
        inputs training saw). So it runs right after ``_run_hit_detection`` and before the
        stroke stage, rewriting ``rally_segments.json`` and the hits' ``rally_id``. The swing
        segments still decide the shuttle-coverage check in hit detection (they existed first).
        No model, no learned hits, no segments or any failure: nothing is touched.
        """
        if getattr(self, "_hit_source", None) != "learned" or not getattr(self, "_hit_list", None):
            return
        from .outcomes.labels import write_bytes_atomic, write_pipeline_json_atomic

        seg_path = os.path.join(self.save_dir, "rally_segments.json")
        old_rallies, old_detection = list(self.rally_list or []), self.rally_detection
        try:
            from .outcomes import run_inputs, segmenter
            model = segmenter.load(self._rally_segmenter_path())
            if model is None:
                return
            writer = getattr(self, "detection_writer", None)
            if writer is not None:
                writer.flush()
            inputs = run_inputs.load(self.save_dir)
            if inputs is None or not inputs.hits:
                return
            segs = segmenter.segments(inputs, model)
            if not segs:
                return
            rallies = [{"id": s["id"], "start_frame": s["start_frame"], "end_frame": s["end_frame"],
                        "start_sec": s["start_sec"], "end_sec": s["end_sec"], "degraded": False,
                        "p_point": s["p_point"]} for s in segs]
            version = model.get("version")
            detection = dict(old_detection or {})
            detection.update({
                "signal": "learned", "attempted_signal": "learned", "degraded": False,
                "reason": f"learned segmenter {version}",
                "model": segmenter.model_detection(model),
                "stroke_labels_withheld": False,
            })
            with open(seg_path, "rb") as f:
                old_bytes = f.read()
            write_pipeline_json_atomic(seg_path, {"fps": fps, "rallies": rallies, "detection": detection})
            try:
                self._rewrite_hit_rally_ids(rallies)
            except Exception:
                write_bytes_atomic(seg_path, old_bytes)
                raise
            self.rally_detection, self.rally_list = detection, list(rallies)
            print(f"Rally segmentation: signal=learned, {len(rallies)} segments "
                  f"(segmenter {version})")
        except Exception as exc:
            self.rally_detection, self.rally_list = old_detection, old_rallies
            print(f"Learned rally segmenter not used, keeping the swing segments: {exc!r}")

    def _rewrite_hit_rally_ids(self, rallies):
        """Point every hit in hits.json at the new rally list (one atomic rewrite)."""
        from .outcomes.labels import write_pipeline_json_atomic

        path = os.path.join(self.save_dir, "hits.json")
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)

        def rally_id(frame):
            for r in rallies:
                if int(r["start_frame"]) <= frame <= int(r["end_frame"]):
                    return r["id"]
            return None

        for h in payload["hits"]:
            h["rally_id"] = rally_id(int(h["frame"]))
        write_pipeline_json_atomic(path, payload)

    def _write_metadata(self, fps, total_frames, video_duration, template_path, corners, roi_corners, mid_height):
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "match_format": getattr(self, "match_format", "singles"),
            "video": {
                "path": self.video_path,
                "name": self.video_name,
                "fps": float(fps),
                "total_frames": int(total_frames),
                "duration_sec": float(video_duration),
                "width": int(self.frame_width),
                "height": int(self.frame_height),
            },
            "models": {
                "shuttlecock": self.ball_model_path,
            },
            "court": {
                "template_path": template_path,
                "corners": corners,
                "roi_corners": roi_corners,
                "mid_height": mid_height,
                "coordinate_system": {
                    "unit": "meter",
                    "width": 6.1,
                    "length": 13.4,
                },
                "court_view": self._court_view_metadata(),
            },
            "outputs": {
                "video": self.output_video_path,
                "detections": self.detections_path,
            },
        }
        write_json(self.metadata_path, metadata)

    FAR_POSE_IMGSZ = 1280
    """Inference size for the far-court crop.

    Measured on the 0007 clip's ~1400x580 far-half crop: 1280 found the far
    player in 8 of 8 sampled frames at 19.3 ms/frame, against 0 of 8 for the
    whole-frame default and 5 of 8 for a whole-frame 2560 pass costing
    39.5 ms. Sized to the crop, not to the frame.
    """

    def _init_far_pose(self, corners, frame_shape, fps):
        """Prepare the far-court second pose pass, or disable it.

        Disabled (roi None) whenever the geometry is unusable, so a run
        without a court quad behaves exactly as it did before this existed.
        """
        self._far_roi = None
        self._far_static_filter = None
        try:
            from .court.far_roi import far_court_roi
            from .tracking.static_filter import StaticCandidateFilter

            mapper = getattr(self.player_tracker, "court_mapper", None)
            self._far_roi = far_court_roi(corners, frame_shape, mapper)
            if self._far_roi is not None:
                self._far_static_filter = StaticCandidateFilter.from_fps(fps)
                print(f"Far-court pose pass: roi={self._far_roi} "
                      f"imgsz={self.FAR_POSE_IMGSZ}")
            else:
                print("Far-court pose pass disabled: unusable court geometry")
        except Exception as exc:
            self._far_roi = None
            print(f"Far-court pose pass disabled: {exc}")

    def _detect_far_players(self, frame, frame_count, main_offset):
        """Run the far-court pass, never fatally.

        Returns empty results when the pass is disabled, so the caller merges
        nothing and the whole-frame path is untouched.
        """
        if getattr(self, "_far_roi", None) is None:
            return [], {}, {}
        try:
            mapper = getattr(self.player_tracker, "court_mapper", None)
            return self.player_pose_visualizer.detect_far_players(
                frame, self._far_roi, court_mapper=mapper,
                imgsz=self.FAR_POSE_IMGSZ,
                static_filter=self._far_static_filter,
                frame_index=frame_count, main_offset=main_offset)
        except Exception as exc:
            print(f"Far-court pose pass failed, disabling it: {exc}")
            self._far_roi = None
            return [], {}, {}

    def _rally_track_record(self, frame_count, centroids, players,
                            point_left_hands, point_right_hands, ball_position):
        """One ``_rally_track`` record, rebuilt exactly as the tracker attaches
        hands to ``detections.jsonl`` (what the §0.16 measurement read).

        ``players`` is the tracker's ``{side: centroid}``, which keeps a STALE
        last-known centroid for a side that was not selected this frame. A side
        counts as selected only if its centroid is among this frame's
        ``centroids``; only then are its hands (looked up by centroid y) and
        position recorded, otherwise wrist and pos are None.
        """
        from .stroke import rallies

        record = {"frame": frame_count, "shuttle": None}
        if ball_position is not None and len(ball_position) >= 2:
            bx, by = float(ball_position[0]), float(ball_position[1])
            if not (bx == 0.0 and by == 0.0):     # [0, 0] is "no detection"
                record["shuttle"] = (bx, by)
        for side in ("lower", "upper"):
            centroid = players.get(side)
            if centroid is not None and centroid in centroids:
                hands = (point_left_hands.get(centroid[1]),
                         point_right_hands.get(centroid[1]))
                record["wrist_" + side] = rallies.wrists_from_hands(*hands)
                record["pos_" + side] = centroid
            else:
                record["wrist_" + side] = None
                record["pos_" + side] = None
        return record

    def _record_rally_frame(self, frame_count, centroids, players,
                            point_left_hands, point_right_hands, ball_position):
        """Append this frame's rally record. Never fatal: the track is a side
        artefact of the analysis, so a failure is logged once and skips only
        this frame's record."""
        try:
            self._rally_track.append(self._rally_track_record(
                frame_count, centroids, players, point_left_hands, point_right_hands,
                ball_position))
        except Exception as exc:
            if not getattr(self, "_rally_track_error_logged", False):
                self._rally_track_error_logged = True
                print(f"Rally track recording failed (frame {frame_count}); "
                      f"skipping such frames, analysis continues: {exc!r}")

    def _analyze_this_frame(self, frame_count):
        """Gate for the heavy per-frame analysis (pose/ball/draw).

        Accurate mode analyzes every court frame (behavior-preserving). Fast mode
        strides the heavy analysis to every FAST_FRAME_STRIDE-th court frame for a
        quick overview.
        """
        if self.analysis_quality != "fast":
            return True
        return frame_count % FAST_FRAME_STRIDE == 0

    def _process_frame(self, frame, template_gray, corners, roi_corners, frame_count, out, detect_frame_count):

        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # frame = self.draw_court_roi(frame, corners, roi_corners)

        is_court = self._court_view_for_frame(gray_frame, template_gray, frame_count)

        # Gate statistics for rally_segments.json's provenance (getattr: a bare
        # instance that skipped __init__ must keep working).
        self._gate_frames = getattr(self, "_gate_frames", 0) + 1
        if is_court:
            self._gate_court_frames = getattr(self, "_gate_court_frames", 0) + 1

        if is_court:
            self.is_court_view_count += 1
            self.consecutive_non_court_frames = 0
        else:
            self.consecutive_non_court_frames += 1
            self.is_court_view_count = 0
            

        if self.is_court_view_count >= self.court_view_frames_threshold and not self.rally_active:
            self.rally_active = True

            self.rally_count += 1
            self._current_rally_start = frame_count

            self.player_tracker.start_new_rally()


        if self.consecutive_non_court_frames >= self.non_court_frames_threshold and self.rally_active:
            self.rally_active = False
            self.rally_segments.append((self.rally_count, self._current_rally_start, frame_count))

            self.shuttlecock_tracker.clear_trajectory()


        if not is_court:
            # Full-duration output: write the raw (un-annotated) frame instead of
            # dropping it, so the result video matches the input duration.
            if self.show_display:
                cv2.imshow('frame', frame)
                cv2.waitKey(1)
            out.write(frame)
            return frame, detect_frame_count

        detect_frame_count += 1

        x1, y1 = roi_corners[0]
        x2, y2 = roi_corners[1]
        roi = frame[y1:y2, x1:x2]
        # Appearance histograms need pixels with no overlay drawn on them: copy
        # before the pose-ROI rectangle, and only on frames the collector samples.
        clean = self._doubles_clean_frame(frame, frame_count)
        if self.show_pose_roi:
            cv2.rectangle(frame, roi_corners[0], roi_corners[1], (255, 0, 0), 2)
            cv2.putText(frame, "Pose ROI", (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2, cv2.LINE_AA)

        if not self._analyze_this_frame(frame_count):
            # Fast mode: this court frame is outside the analysis stride, so skip
            # the heavy pose/ball/draw work and write the raw frame through,
            # mirroring the non-court passthrough above.
            if self.show_display:
                cv2.imshow('frame', frame)
                cv2.waitKey(1)
            out.write(frame)
            return frame, detect_frame_count

        pose_t0 = time.time()
        centroids, point_left_hands, point_right_hands = self.player_pose_visualizer.detect_players(roi, x1, y1)
        far_centroids, far_left, far_right = self._detect_far_players(frame, frame_count, (x1, y1))
        centroids = centroids + far_centroids
        # Keyed by centroid y, which the far pass computes in the same
        # full-frame coordinates, so these merge without collision.
        point_left_hands.update(far_left)
        point_right_hands.update(far_right)
        pose_elapsed = time.time() - pose_t0

        ball_t0 = time.time()
        detected_ball_position = self.shuttlecock_tracker.detect_ball(frame, roi_corners=roi_corners)
        ball_elapsed = time.time() - ball_t0
        ball_position = self.shuttlecock_tracker.update_trajectory(detected_ball_position, roi_corners)
        

        shuttle_draw_t0 = time.time()
        self.shuttlecock_tracker.handle_visualization(frame)
        shuttle_draw_elapsed = time.time() - shuttle_draw_t0
        

        players = self.player_tracker.update(frame_count, centroids, ball_position,
                                             point_left_hands, point_right_hands, detect_frame_count)

        self._record_rally_frame(frame_count, centroids, players,
                                 point_left_hands, point_right_hands, ball_position)

        if getattr(self, "_doubles", None) is not None:
            self._doubles_frames += 1
            self._observe_doubles(frame_count, clean, centroids)

        if getattr(self, "_capture_frames", self.analyze_technique):
            self._capture_analysis_frame(frame_count, frame, roi_corners, ball_position)

        if frame_count == 1 or not self.cached_movement_stats:
            self.cached_movement_stats = self.player_tracker.get_player_movement_stats()
            self.stats_update_interval_frames = int(self.player_tracker.fps * 0.5)

        if frame_count - self.last_stats_update_frame >= self.stats_update_interval_frames:

            self.cached_movement_stats = self.player_tracker.get_player_movement_stats()
            self.last_stats_update_frame = frame_count


        should_log_performance = (
            self.show_performance_stats
            and self.performance_log_interval_frames > 0
            and frame_count % self.performance_log_interval_frames == 0
        )

        t0 = time.time()

        self.player_pose_visualizer.draw_players(
            frame=frame, 
            player_tracker=self.player_tracker, 
            cached_movement_stats=self.cached_movement_stats,
            stats_visualizer=self.stats_visualizer if self.show_player_stats else None,
            rally_count=self.rally_count
        )
        t1 = time.time()
        players_draw_elapsed = t1 - t0
        

        court_draw_elapsed = 0.0
        if self.show_court_trajectory:
            t0 = time.time()
            frame = self.court_trajectory_visualizer.draw_overlay(frame, self.player_tracker.court_history)
            t1 = time.time()
            court_draw_elapsed = t1 - t0

        if should_log_performance:
            print(
                f"Frame {frame_count}: pose {pose_elapsed:.2f}s, "
                f"shuttlecock {ball_elapsed:.2f}s, "
                f"shuttle draw {shuttle_draw_elapsed:.2f}s, "
                f"players draw {players_draw_elapsed:.2f}s, "
                f"court draw {court_draw_elapsed:.2f}s"
            )
        

        if frame is not None:
            if self.show_display:
                cv2.imshow('frame', frame)
                cv2.waitKey(1)
            out.write(frame)

            if self.save_images:
                cv2.imwrite(os.path.join(self.images_save_dir, f"{frame_count}.png"), frame)
        return frame, detect_frame_count

    def _side_centroids(self):
        """{side: tracked centroid} for the sides the tracker holds this frame."""
        out = {}
        for side in ("lower", "upper"):
            pt = self.player_tracker.players.get(side)
            if pt is not None:
                out[side] = (float(pt[0]), float(pt[1]))
        return out

    def _capture_side_poses(self, side_centroids, people, ox, oy):
        """{side: keypoints or None} -- each side's own pose, assigned ONE-TO-ONE.

        Two gates are load-bearing here, and they catch different failures:

        1. A distance gate (POSE_TO_PLAYER_MAX_PX). Without it, the nearest
           detected person is accepted however far away they are.
        2. **One-to-one assignment.** Without it, a frame in which the pose
           model found only ONE person (while the cheaper player tracker still
           holds centroids for BOTH halves, each within the gate -- e.g. both
           players near the projected net) hands that same person's keypoints
           to both sides, because two independent nearest-neighbour searches
           over the same list can return the same element. That fabricates a
           phantom opponent: the other player's slot in the hit signals
           becomes a near-duplicate of the hitter (worse than the honest
           zero-fill it replaced), and the duplicated racket point ties in
           stroke.events.detect_contacts_multi, whose tie-break then sends
           every such contact to "lower" -- misattributing genuine upper-side
           hits, i.e. defeating the very fix this capture path exists for.

        A side that is tracked but wins no person is reported as None, so the
        caller records "tracked but unmatched" rather than borrowing the other
        side's player.
        """
        from .analysis import joint_angles as ja

        def _foot_midpoint(kp_arr):
            pts = []
            for idx in (ja.L_ANKLE, ja.R_ANKLE):
                if ja.is_valid(kp_arr, idx):
                    pts.append((float(kp_arr[idx][0]) + ox, float(kp_arr[idx][1]) + oy))
            if pts:
                return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            return None

        feet = [_foot_midpoint(p) for p in (people or [])]
        chosen = _assign_one_to_one(side_centroids, feet, POSE_TO_PLAYER_MAX_PX ** 2)

        out = {}
        for side in side_centroids:
            i = chosen.get(side)
            if i is None:
                out[side] = None
                continue
            kp = people[i].astype(float).copy()
            mask = ~((kp[:, 0] <= 1) & (kp[:, 1] <= 1))
            kp[mask, 0] += ox
            kp[mask, 1] += oy
            out[side] = kp
        return out

    def _capture_analysis_frame(self, frame_count, frame, roi_corners, ball_position):
        from .analysis import joint_angles as ja
        pose = self.player_pose_visualizer.get_current_pose_data()
        keypoints = None
        racket_head = None
        nose = shoulder = hip = centroid = None
        elbow_angle = None
        angles_now = None
        side = "unknown"

        # centroid + side from tracked players (prefer lower court, else upper)
        for region in ("lower", "upper"):
            p = self.player_tracker.players.get(region)
            if p is not None:
                centroid = (float(p[0]), float(p[1]))
                side = region
                break

        if pose is not None and pose.get("keypoints") is not None and len(pose["keypoints"]) > 0:
            people = pose["keypoints"]
            ox, oy = pose.get("offset_x", 0), pose.get("offset_y", 0)

            def _foot_midpoint(kp_arr, ox, oy):
                pts = []
                for idx in (ja.L_ANKLE, ja.R_ANKLE):
                    if ja.is_valid(kp_arr, idx):
                        pts.append((float(kp_arr[idx][0]) + ox, float(kp_arr[idx][1]) + oy))
                if pts:
                    xs = sum(p[0] for p in pts) / len(pts)
                    ys = sum(p[1] for p in pts) / len(pts)
                    return (xs, ys)
                return None

            if centroid is not None and len(people) > 1:
                def _dist_to_centroid(pers):
                    fm = _foot_midpoint(pers, ox, oy)
                    if fm is None:
                        return float("inf")
                    return (fm[0]-centroid[0])**2 + (fm[1]-centroid[1])**2
                person = min(people, key=_dist_to_centroid)
            else:
                person = people[0]

            kp = person.astype(float).copy()
            # shift ROI-local keypoints back to full-frame coords (ignore missing <=1)
            mask = ~((kp[:, 0] <= 1) & (kp[:, 1] <= 1))
            kp[mask, 0] += ox
            kp[mask, 1] += oy
            keypoints = kp
            if ja.is_valid(kp, ja.NOSE):
                nose = (float(kp[ja.NOSE][0]), float(kp[ja.NOSE][1]))
            dom = ja.R_SHOULDER if self.dominant_hand == "right" else ja.L_SHOULDER
            dom_hip = ja.R_HIP if self.dominant_hand == "right" else ja.L_HIP
            if ja.is_valid(kp, dom):
                shoulder = (float(kp[dom][0]), float(kp[dom][1]))
            if ja.is_valid(kp, dom_hip):
                hip = (float(kp[dom_hip][0]), float(kp[dom_hip][1]))
            angles_now = ja.compute_joint_angles(kp, dominant=self.dominant_hand)
            elbow_angle = angles_now.get("elbow_extension")

        if keypoints is not None and angles_now is not None:
            from .visualization.technique_overlay import draw_technique_overlay
            draw_technique_overlay(frame, angles_now)

        # ONE racket forward pass per frame, shared by the single-player value
        # below and the both-player block further down. detect_racket_heads
        # returns exactly the same in-ROI boxes detect_racket_head chooses
        # from, sorted confidence-descending; since Python's sort is stable,
        # racket_candidates[0] is byte-identical to
        # detect_racket_head's max(boxes, key=confidence). Calling both would
        # double this frame's YOLO cost on the pipeline's hot path.
        racket_candidates = []
        if self._racket_detector is not None:
            racket_candidates = self._racket_detector.detect_racket_heads(frame, roi_corners=roi_corners)
            racket_head = racket_candidates[0] if racket_candidates else None
        if racket_head is None and keypoints is not None:
            from .analysis.joint_angles import infer_racket_head
            racket_head = infer_racket_head(keypoints, dominant=self.dominant_hand)

        shuttle = None
        if self._shuttle_trajectory is not None:
            pt = self._shuttle_trajectory.get(frame_count)
            if pt is not None:
                shuttle = (float(pt[0]), float(pt[1]))
        elif ball_position and ball_position != [0, 0]:
            shuttle = (float(ball_position[0]), float(ball_position[1]))

        self._analysis_track.append({
            "frame": frame_count, "racket_head": racket_head, "shuttle": shuttle,
        })

        # --- B1: additive both-player capture (contacts and hit list only; the
        # single-player fields above are unchanged and keep driving
        # TechniqueAnalysisRunner exactly as before) ---
        from .analysis.joint_angles import infer_racket_head as _infer_racket_head_both

        people_list = []
        p_ox = p_oy = 0
        if pose is not None and pose.get("keypoints") is not None and len(pose["keypoints"]) > 0:
            people_list = list(pose["keypoints"])
            p_ox, p_oy = pose.get("offset_x", 0), pose.get("offset_y", 0)

        # racket_candidates was already computed once above (single YOLO pass).

        # Both assignments are ONE-TO-ONE across the two sides (see
        # _assign_one_to_one): two independent nearest-neighbour searches would
        # hand a single detected person -- or a single racket box -- to BOTH
        # sides whenever both tracked centroids are in gate range, fabricating a
        # phantom opponent and a tied racket point that misattributes contacts.
        side_centroids = self._side_centroids()
        side_poses = self._capture_side_poses(side_centroids, people_list, p_ox, p_oy)
        racket_pick = _assign_one_to_one(side_centroids, racket_candidates,
                                         RACKET_TO_PLAYER_MAX_PX ** 2)

        players_data = {}
        for region in ("lower", "upper"):
            side_centroid = side_centroids.get(region)
            side_kp = side_poses.get(region)
            side_racket = None
            ri = racket_pick.get(region)
            if ri is not None:
                side_racket = (float(racket_candidates[ri][0]), float(racket_candidates[ri][1]))
            if side_racket is None and side_kp is not None:
                side_racket = _infer_racket_head_both(side_kp, dominant=self.dominant_hand)
            players_data[region] = {
                "keypoints": side_kp, "centroid": side_centroid, "racket_head": side_racket,
            }

        self._analysis_track_both.append({
            "frame": frame_count,
            "racket_lower": players_data["lower"]["racket_head"],
            "racket_upper": players_data["upper"]["racket_head"],
            "shuttle": shuttle,
        })

        self._analysis_frames[frame_count] = {
            "frame": frame_count, "keypoints": keypoints, "conf": None,
            "racket_head": racket_head, "centroid": centroid, "nose": nose,
            "shoulder": shoulder, "hip": hip, "elbow_angle": elbow_angle,
            "player_side": side, "shuttle": shuttle, "players": players_data,
        }

    @staticmethod
    def _normalize_tracknet_mode(mode):
        if mode in TRACKNET_MODES:
            return mode
        if mode is not None:
            print(f"Unknown TrackNetV3 mode {mode!r}; using 'weight'.")
        return "weight"

    def _run_shuttle_pretrack(self):
        """Offline TrackNetV3 dense-shuttle pre-pass. Never fatal.

        Populates ``self._shuttle_trajectory`` (keyed by match-loop
        ``frame_count`` = TrackNet 0-based frame + 1) and sets
        ``self._shuttle_source = 'tracknet'``. On any failure or when weights
        are absent, leaves the trajectory None so ``_capture_analysis_frame``
        falls back to the yolo shuttle (byte-identical to today). Progress stage
        ``tracking_shuttle``. A video over ``SHUTTLE_PRETRACK_MAX_FRAMES`` is
        skipped unless a hit model is configured or ``dense_whole_video`` is set,
        because the learned hit detector needs a whole-video dense track.
        """
        if not (self.tracknet_weights and getattr(self, "_capture_frames", self.analyze_technique)):
            return
        try:
            _cap = cv2.VideoCapture(self.video_path)
            n_frames = int(_cap.get(cv2.CAP_PROP_FRAME_COUNT))
            _cap.release()
        except Exception:
            n_frames = 0
        if (n_frames > SHUTTLE_PRETRACK_MAX_FRAMES
                and not getattr(self, "hit_model_path", None)
                and not getattr(self, "dense_whole_video", False)):
            print(f"TrackNetV3 pre-pass skipped: {n_frames} frames > "
                  f"{SHUTTLE_PRETRACK_MAX_FRAMES} budget (no hit model configured); "
                  f"using yolo shuttle.")
            return
        mode = getattr(self, "tracknet_mode", "weight")
        try:
            from .shuttle_track import tracknet as tnmod
            from .shuttle_track import trajectory as tjmod

            params = {"eval_mode": mode,
                      "inpaint": bool(self.inpaintnet_weights),
                      "roi": self.court_roi_corners}
            cache_path = os.path.join(self.save_dir, "shuttle_trajectory.json")
            key = tjmod.cache_key(self.video_path, params)
            traj0 = tjmod.load_cache(cache_path, key)
            if traj0 is None:
                models = tnmod.load_tracknet(self.tracknet_weights, self.inpaintnet_weights)
                with _ProgressHeartbeat(self._write_progress, "tracking_shuttle") as beat:
                    traj0 = tnmod.track_video(self.video_path, models,
                                              court_roi=self.court_roi_corners,
                                              eval_mode=mode, progress=beat.update)
                tjmod.save_cache(cache_path, key, traj0)
            # Align TrackNet 0-based frames to the match loop's 1-based frame_count.
            self._shuttle_trajectory = {f + 1: pt for f, pt in traj0.items()}
            self._shuttle_source = "tracknet"
            detected = sum(1 for pt in traj0.values() if pt is not None)
            print(f"Dense shuttle tracking ({mode}): {detected}/{len(traj0)} frames via TrackNetV3")
        except Exception as e:  # never fatal
            print(f"TrackNetV3 pre-pass unavailable ({e}); using yolo shuttle.")
            self._shuttle_trajectory = None
            self._shuttle_source = "yolo"

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
            from .court.mapper import CourtMapper
            from .tracking.doubles import DoublesCollector
            self._doubles = DoublesCollector(CourtMapper(corners), corners, fps)
            print("Doubles collection enabled")
        except Exception as exc:
            print(f"Doubles collection unavailable: {exc!r}")

    def _doubles_clean_frame(self, frame, frame_count):
        """Overlay-free copy of frame when the doubles collector will sample it, else None."""
        collector = getattr(self, "_doubles", None)
        if collector is None or not self._analyze_this_frame(frame_count):
            return None
        try:
            wanted = collector.wants_frame()
        except Exception as exc:
            if not getattr(self, "_doubles_clean_frame_logged", False):
                self._doubles_clean_frame_logged = True
                print(f"Doubles frame sampling failed (frame {frame_count}): {exc!r}")
            return None
        return frame.copy() if wanted else None

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
        # training_plan.json is generated from technique_summary.json and cached:
        # a singles run's plan would otherwise be served beside "technique withheld".
        for name in ("strokes.json", "strokes.jsonl", "technique_summary.json",
                     "training_plan.json"):
            try:
                os.remove(os.path.join(self.save_dir, name))
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"Could not remove the previous run's {name}: {exc}")

    def _doubles_constraint_frames(self):
        """In-play frames (inclusive rally spans) for the identity constraints, or None.

        Players only cross the net between rallies (end changes), so the identity
        constraints must use in-play frames. Returned only when rally detection
        ran and is trustworthy; otherwise None (no constraint). The court-view
        signal is not trusted: a fixed camera yields one whole-video window,
        which would apply the team rule through every walk-around.
        """
        detection = getattr(self, "rally_detection", None)
        if not isinstance(detection, dict):
            return None
        if (detection.get("signal") in ("error", "none", "courtview")
                or detection.get("degraded")):
            return None
        frames = set()
        for rally in getattr(self, "rally_list", None) or []:
            try:
                start, end = int(rally["start_frame"]), int(rally["end_frame"])
            except (KeyError, TypeError, ValueError):
                continue
            frames.update(range(start, end + 1))
        return frames or None

    def _doubles_shuttle(self):
        """Shuttle track as {frame: (x, y)}, skipping frames with no detection, or None.

        The dense TrackNet track when it ran; otherwise the per-frame YOLO shuttle
        points recorded in ``_analysis_track_both`` (sparser, but still evidence).
        """
        traj = getattr(self, "_shuttle_trajectory", None)
        if traj is None:
            traj = {r["frame"]: r.get("shuttle")
                    for r in getattr(self, "_analysis_track_both", None) or []}
        out = {}
        for k, v in traj.items():
            if v is None:
                continue
            x, y = float(v[0]), float(v[1])
            if x == 0.0 and y == 0.0:
                continue
            out[int(k)] = (x, y)
        return out or None

    def _run_doubles_stage(self, fps, analysed_frames):
        """Post-loop doubles analysis. Singles: only removes stale doubles outputs."""
        try:
            from .tracking import doubles as dbl
            dbl.remove_doubles_outputs(self.save_dir, getattr(self, "video_name", None))
            if getattr(self, "match_format", "singles") != "doubles":
                return
            collector = getattr(self, "_doubles", None)
            if collector is None:
                # Say why in players.json, so the UI does not just show nothing.
                print("Doubles stage skipped: no collector")
                from .data.writer import write_json as _write_json
                _write_json(os.path.join(self.save_dir, "players.json"),
                            dbl.make_payload("error", "doubles collector unavailable"))
                return
            self._write_progress("visualizing", substage="doubles_identity")
            payload = collector.finalize(self.save_dir, analysed_frames,
                                         shuttle=self._doubles_shuttle(),
                                         constraint_frames=self._doubles_constraint_frames())
            from .visualization.doubles_render import render_identity_video, write_heatmaps
            self._write_progress("visualizing", substage="doubles_heatmaps")
            write_heatmaps(self.save_dir, payload, collector.identity_tracks)
            video = {"status": "skipped", "file": None, "reason": "source video missing"}
            if os.path.exists(self.video_path):
                name = dbl.players_video_name(self.video_name)
                out = os.path.join(self.save_dir, name)
                # Re-reads and re-encodes the whole video: minutes on 4K, so beat.
                with _ProgressHeartbeat(self._write_progress, "visualizing",
                                        substage="doubles_video") as beat:
                    rendered = render_identity_video(
                        self.video_path, out, collector.identity_tracks,
                        payload.get("players") or [], fps=fps, progress=beat.update)
                if rendered:
                    video = {"status": "ok", "file": name, "reason": None}
                    print(f"Identity video: {out}")
                else:
                    video = {"status": "failed", "file": None, "reason": "render failed"}
            if video["status"] != "ok":
                print(f"Identity video skipped: {video['reason']}")
            # Recorded so consumers link the video only when it exists (spec 6.6).
            from .data.writer import write_json as _write_json
            payload["identity_video"] = video
            _write_json(os.path.join(self.save_dir, "players.json"), payload)
        except Exception as exc:
            print(f"Doubles stage failed: {exc!r}")

    _HIT_OUTPUTS = ("hits.json", "hit_signals.npz")

    def _run_hit_detection(self, fps, last_frame):
        """Build the shared hit list. Returns learned hits, or None (contact rule).

        Writes hit_signals.npz (the detector's raw input, also harvested by the
        ShuttleSet dataset builder) and hits.json. Never fatal: every failure
        falls back to today's contact rule with the reason recorded. Without
        technique analysis there are no per-frame poses, so nothing is written and
        a previous run's hit outputs are removed (they would describe another run).
        The previous run's outputs are removed first in every case, so a run that
        fails before writing leaves no stale files behind.

        Also records ``self._hit_list`` (the list written to hits.json),
        ``self._hit_signals`` and ``self._hit_source`` for the stroke classifier
        and technique analysis; each stays None when it was not produced.
        """
        self._hit_list = self._hit_signals = self._hit_source = None
        for name in self._HIT_OUTPUTS:
            try:
                os.remove(os.path.join(self.save_dir, name))
            except FileNotFoundError:
                pass
            except OSError as exc:
                print(f"Could not remove the previous run's {name}: {exc}")
        if not getattr(self, "_capture_frames", self.analyze_technique) or not last_frame:
            return None

        try:
            from .stroke import hit_detector as hd
            from .stroke import hit_signals as hs
            from .stroke.events import hit_events
        except Exception as exc:  # never fatal: fall back to the contact rule
            print(f"Hit detection unavailable, using the contact rule: {exc!r}")
            return None

        signals = None
        try:
            shuttle = (self._shuttle_trajectory if self._shuttle_trajectory is not None
                       else {r["frame"]: r["shuttle"] for r in self._analysis_track_both})
            signals = hs.build_signals(fps, last_frame, self._analysis_frames, shuttle,
                                       self.court_corners, (self.frame_width, self.frame_height))
            hs.save_signals(os.path.join(self.save_dir, "hit_signals.npz"), signals)
            self._hit_signals = signals
        except Exception as exc:
            print(f"Hit signals unavailable: {exc!r}")

        try:
            coverage = hd.shuttle_coverage(signals, self._in_play_frames()) if signals is not None else 0.0
            reason, hits, model_info = None, None, None
            if not getattr(self, "hit_model_path", None):
                reason = "no hit model"
            elif signals is None:
                reason = "hit signals unavailable"
            elif self._shuttle_source != "tracknet":
                reason = "no dense shuttle track"
            elif coverage < hd.MIN_SHUTTLE_COVERAGE:
                reason = (f"dense shuttle track covers {coverage:.0%} of in-play frames "
                          f"(needs {hd.MIN_SHUTTLE_COVERAGE:.0%})")
            else:
                try:
                    from .stroke import hit_model as hm
                    model, meta = hm.load_hit_model(self.hit_model_path)
                    trained_on = meta.get("pose_family")
                    run_family = getattr(self, "pose_family", None)
                    if trained_on and run_family and trained_on != run_family:
                        reason = (f"hit model trained on {trained_on} keypoints; "
                                  f"this run used {run_family}")
                    else:
                        hits = hd.detect_hits(signals, model, meta)
                        model_info = {"sha256": hm.file_sha256(self.hit_model_path),
                                      "schema": meta.get("schema"), "threshold": meta.get("threshold"),
                                      "min_spacing_sec": meta.get("min_spacing_sec")}
                except Exception as exc:
                    print(f"Hit detector failed, using the contact rule: {exc!r}")
                    reason = f"hit detector failed: {type(exc).__name__}"
                    hits = None

            if hits is None:
                listed = [{"frame": e["frame"], "hitter": e["hitter"], "confidence": None}
                          for e in hit_events(self._analysis_track_both)]
                source = "contact_rule"
            else:
                listed, source = hits, "learned"
            # A malformed hit would break every consumer, so reject the whole list here.
            for h in listed:
                int(h["frame"]), h["hitter"]
            self._hit_list, self._hit_source = listed, source
            self._write_hits_json(fps, source, reason, model_info, coverage, listed)
            print(f"Hit detection: {len(listed)} hits ({source}"
                  f"{'; ' + reason if reason else ''})")
            return hits
        except Exception as exc:
            print(f"Hit detection bookkeeping failed, using the contact rule: {exc!r}")
            return None

    def _in_play_frames(self):
        """Frames inside B11 rally segments; all analysed frames when there are none."""
        frames = set()
        for r in getattr(self, "rally_list", None) or []:
            frames.update(range(int(r["start_frame"]), int(r["end_frame"]) + 1))
        return sorted(frames) if frames else sorted(self._analysis_frames)

    def _write_hits_json(self, fps, source, reason, model_info, coverage, hits):
        from .data.writer import write_json as _write_json

        rallies = getattr(self, "rally_list", None) or []

        def rally_id(frame):
            for r in rallies:
                if int(r["start_frame"]) <= frame <= int(r["end_frame"]):
                    return r["id"]
            return None

        try:
            payload = {
                "source": source, "reason": reason, "model": model_info,
                "shuttle_source": self._shuttle_source,
                "shuttle_coverage_in_play": round(float(coverage), 4),
                "fps": fps,
                "pose_family": getattr(self, "pose_family", None),
                "hits": [{"frame": int(h["frame"]),
                          "time_sec": round((int(h["frame"]) - 1) / fps, 4) if fps else None,
                          "hitter": h["hitter"], "confidence": h.get("confidence"),
                          "rally_id": rally_id(int(h["frame"]))} for h in hits],
            }
            _write_json(os.path.join(self.save_dir, "hits.json"), payload)
        except Exception as exc:
            print(f"hits.json could not be written: {exc!r}")

    def _run_technique_analysis(self, labels=None):
        from .analysis.biomechanics import BiomechanicalAnalyzer
        from .analysis.technique_writer import write_stroke_reports, build_match_summary
        runner = TechniqueAnalysisRunner(
            BiomechanicalAnalyzer(dominant=self.dominant_hand),
            racket_detector=self._racket_detector,
            dominant=self.dominant_hand,
            fps=self.fps,
            court_corners=getattr(self, "court_corners", None),
        )
        hits = getattr(self, "_hit_list", None)
        if hits is not None:
            # The runner reads single-player pose fields (the frame's tracked
            # player), so it only gets that player's hits (B spec open question F).
            hits = [h for h in hits
                    if (self._analysis_frames.get(h["frame"]) or {}).get("player_side") == h["hitter"]]
        strokes = {int(l["frame"]): l for l in labels or []}
        reports, _events = runner.run(self._analysis_track, self._analysis_frames.get,
                                      hits=hits, strokes=strokes)
        strokes_path = os.path.join(self.save_dir, "strokes.jsonl")
        summary_path = os.path.join(self.save_dir, "technique_summary.json")
        write_stroke_reports(strokes_path, reports)
        write_json(summary_path, build_match_summary(reports))
        print(f"Technique analysis: {len(reports)} strokes -> {strokes_path}")
        self._rescore_owner_stroke_reports()

    def _run_stroke_classification(self):
        """Label the shared hit list with the one stroke classifier -> strokes.json (schema 2).

        Withheld (no file, a previous one removed) when rally segmentation is degraded
        (owner decision 12a-C). No hits -> no file. Without a usable model every hit is
        "unknown" and classifier is null: there is no other classifier to fall back to.
        Never fatal.

        Withheld entirely when rally segmentation is degraded (owner decision
        §12a-C): labels computed over coarse windows that do not correspond to
        rallies are close to noise, and shipping them behind a badge is weaker
        than the impression that strokes were detected. When B6 adds per-rally
        invocation, the same ``degraded`` flag skips individual segments instead
        of the whole run. A segmenter ERROR (``signal: "error"``,
        ``degraded: False``) is not a degraded segmentation and does not
        withhold; nor does a missing ``rally_detection`` (older runs, bare
        instances).
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
        labels, info, reason = None, None, None
        model_path = getattr(self, "stroke_model_path", None)
        signals = getattr(self, "_hit_signals", None)
        if not model_path:
            reason = "no stroke model"
        elif signals is None:
            reason = "hit signals unavailable"
            print("Stroke classifier not run, every hit is unknown: the hit detector's "
                  "per-frame signals are unavailable for this run.")
        else:
            try:
                model, meta, info = sc.load_classifier(model_path)
                labels = sc.classify_hits(signals, hits, model, meta)
            except Exception as exc:
                print(f"Stroke classifier unavailable, every hit is unknown: {exc!r}")
                labels, info = None, None
                reason = f"classifier failed: {type(exc).__name__}"
        if labels is None:
            labels = sc.unknown_labels(hits)
        try:
            _write_json(path, sc.build_payload(labels, info, getattr(self, "_hit_source", None),
                                               getattr(self, "_shuttle_source", None), reason=reason))
            print(f"Stroke classification: {len(labels)} hits -> {path}")
            self._apply_owner_stroke_labels()
        except Exception as exc:
            print(f"strokes.json could not be written: {exc!r}")
        return labels

    def _apply_owner_stroke_labels(self):
        """Re-apply the owner's stroke corrections to the fresh strokes.json; never fatal."""
        from .stroke import owner_labels
        labels_dir = getattr(self, "stroke_labels_dir", None)
        if not labels_dir:
            return
        try:
            result = owner_labels.apply_to_result(
                self.save_dir, labels_dir, getattr(self, "video_name", os.path.basename(self.save_dir)))
            if result.get("applied"):
                self._rescore_owner_stroke_reports()
        except Exception as exc:
            print(f"Owner stroke labels could not be applied: {exc!r}")

    def _rescore_owner_stroke_reports(self):
        """Score the posture reports with the owner's stroke types; never fatal, no-op without labels."""
        try:
            from .stroke import relabel
            doc = relabel._json(os.path.join(self.save_dir, "strokes.json"))
            if isinstance(doc, dict) and "owner_labels" in doc:
                relabel.rescore_reports(self.save_dir)
        except Exception as exc:
            print(f"Posture reports could not be re-scored with the owner's labels: {exc!r}")

    def _get_template_path(self):
        """Get the court template image path."""
        if self.template_path:
            if not os.path.exists(self.template_path):
                raise FileNotFoundError(
                    f"Court template image not found: {self.template_path}"
                )
            return self.template_path

        try:
            root = tk.Tk()
            root.withdraw()
            template_path = filedialog.askopenfilename(
                title="Select court template image",
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp")]
            )
            root.destroy()
        except Exception as exc:
            raise RuntimeError(
                "Unable to open the template picker. In headless environments, "
                "pass a court template image path with --template-path."
            ) from exc

        if not template_path:
            raise RuntimeError(
                "No court template image selected. Pass --template-path to run "
                "without the file picker."
            )
        return template_path

    def _load_template(self, template_path, cap):
        """Load and resize the court template image.

        Returns ``(template_gray, template_color, template_small)``;
        ``template_small`` is the gate's 480px template, downscaled exactly once.
        """
        import cv2

        template_gray = cv2.imread(template_path, 0)
        template_color = cv2.imread(template_path)
        if template_gray is None or template_color is None:
            raise RuntimeError(f"Unable to read court template image: {template_path}")
        
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        template_gray = cv2.resize(template_gray, (frame_width, frame_height))
        template_color = cv2.resize(template_color, (frame_width, frame_height))
        
        return template_gray, template_color, downscale_for_gate(template_gray)

    def _setup_video_writer(self, frame_width, frame_height, fps):

        self.temp_output_video_path = os.path.join(self.save_dir, f"temp_detect_{self.video_name}.mp4")
        

        self.video_writer = vap.setup_video_writer(
            frame_width=frame_width,
            frame_height=frame_height,
            fps=fps,
            temp_output_path=self.temp_output_video_path
        )
        
        return self.video_writer

    def _setup_court_annotation(self, template_color):
        """Set up court annotation."""

        if os.path.exists(os.path.join(self.save_dir, 'court_annotations.txt')):
            with open(os.path.join(self.save_dir, 'court_annotations.txt'), 'r') as f:
                corners = eval(f.readline().split('=')[1])
                f.readline()
                mid_height = eval(f.readline().split('=')[1])
                roi_corners = compute_expanded_roi(corners, template_color.shape)
        else:
            auto_preview_path = os.path.join(self.save_dir, 'auto_court_preview.png')
            corners, roi_corners, mid_height = annotate_court(
                template_color, auto_preview_path=auto_preview_path,
                interactive=bool(self.show_display))
       
        if not corners or not roi_corners or len(corners) != 4 or len(roi_corners) != 2:
            raise RuntimeError("Court annotation is incomplete: click 4 court corners in order. ROI is generated automatically.")

        with open(os.path.join(self.save_dir, 'court_annotations.txt'), 'w') as f:
            f.write(f"corners={corners}\n")
            f.write(f"roi_corners={roi_corners}\n")
            f.write(f"mid_height={mid_height}\n")
        return corners, roi_corners, mid_height

    # Per-stage fixed pct for non-analyzing stages; analyzing derives from frames.
    _PROGRESS_STAGE_PCT = {
        "initializing": 1, "court_setup": 3, "calibrating_court_view": 3,
        "tracking_shuttle": 3,
        "analyzing": None,
        "visualizing": 97, "encoding": 99, "done": 100,
    }

    def _write_progress(self, stage, current_frame=None, substage=None):
        """Write a progress heartbeat to <save_dir>/progress.json. Never fatal.

        ``substage`` names a step inside a long stage (the doubles identity solve
        and identity-video render run under "visualizing"); it is omitted when None.
        """
        try:
            total = int(getattr(self, "_total_frames", 0) or 0)
            fixed = self._PROGRESS_STAGE_PCT.get(stage)
            if fixed is not None:
                pct = fixed
            elif total > 0 and current_frame is not None:
                pct = max(3, min(96, int(current_frame / total * 100)))
            else:
                pct = 3
            payload = {
                "stage": stage,
                "current_frame": int(current_frame or 0),
                "total_frames": total,
                "pct": int(pct),
                "updated": time.time(),
            }
            if substage:
                payload["substage"] = substage
            with open(os.path.join(self.save_dir, "progress.json"), "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except Exception:
            pass

    def _cleanup(self, cap):
        """Clean up resources and merge audio when needed."""
        if self.detection_writer is not None:
            self.detection_writer.close()
            self.detection_writer = None

        if hasattr(self, 'video_writer') and self.video_writer is not None:
            self.video_writer.release()
            time.sleep(1)

        cap.release()

        if self.show_display:
            cv2.destroyAllWindows()

        self._write_progress("encoding")
        if hasattr(self, 'keep_audio') and self.keep_audio:
            vap.process_video_with_audio(
                video_path=self.video_path,
                temp_video_path=self.temp_output_video_path,
                output_path=self.output_video_path,
                save_dir=self.save_dir
            )
        else:
            vap.process_video_without_audio(
                temp_video_path=self.temp_output_video_path,
                output_path=self.output_video_path
            )

        self._write_progress("done")

    def analyze_shuttlecock(self, roi_corners, corners):
        """Hit-point analysis is currently disabled."""
        raise RuntimeError(
            "Hit-point analysis is disabled until it is migrated to detections.jsonl."
        )

    def is_court_view(self, frame, template_gray, threshold=None):
        """Full-resolution whole-frame NCC against the template (the pinned gate).

        ``threshold=None`` uses ``self.court_view_cut``; an explicit value
        overrides it. A ``-inf`` cut passes everything.
        """
        import cv2
        import numpy as np

        cut = self.court_view_cut if threshold is None else float(threshold)
        if cut == float("-inf"):
            return True
        result = cv2.matchTemplate(frame, template_gray, cv2.TM_CCOEFF_NORMED)
        return bool(np.max(result) >= cut)

    def _court_view_downscaled(self, gray_frame, template_small):
        """The default gate: NCC of a 480px downscale against ``self.court_view_cut``.

        A ``-inf`` cut (template not of this video) passes every frame without
        scoring; a NaN score (constant frame) is not court view under a finite cut.
        """
        cut = self.court_view_cut
        if cut == float("-inf"):
            return True
        return bool(self._court_view_score(gray_frame, template_small) >= cut)

    def _court_view_for_frame(self, gray_frame, template_gray, frame_count):
        """Decide whether this frame shows the court.

        Pinned (``court_view_threshold`` given, or an instance that never
        prepared a cut): today's gate exactly -- full-resolution match against
        the pinned value, recomputed every COURT_VIEW_CHECK_INTERVAL frames with
        the result held between checks (lossless within the 5-frame rally
        thresholds; boundaries may shift by <= interval-1 frames).

        Otherwise: the 480px downscale is scored against the once-downscaled
        template on every frame, using this video's calibrated cut.
        """
        # An instance that never ran __init__/_prepare_court_view_cut has no
        # calibrated cut, so it keeps the legacy gate.
        if getattr(self, "court_view_pinned", True):
            cached = getattr(self, "_court_view_cached", None)
            last_frame = getattr(self, "_court_view_last_frame", -10)
            if cached is None or frame_count - last_frame >= COURT_VIEW_CHECK_INTERVAL:
                self._court_view_cached = self.is_court_view(gray_frame, template_gray)
                self._court_view_last_frame = frame_count
            return self._court_view_cached

        template_small = getattr(self, "court_view_template_small", None)
        if template_small is None:
            template_small = downscale_for_gate(template_gray)
            self.court_view_template_small = template_small
        return self._court_view_downscaled(gray_frame, template_small)

    def draw_court_roi(self, frame, corners, roi_corners):
        self.court_mapper = CourtMapper(corners)
        overlay, mid_height_int = self.court_mapper.draw_court_overlay(frame)
        cv2.rectangle(overlay, roi_corners[0], roi_corners[1], (255, 0, 0), 2)
        return overlay


class TechniqueAnalysisRunner:
    """Post-loop orchestration: contacts -> classification -> biomechanical reports."""

    def __init__(self, analyzer, racket_detector=None, dominant="right",
                 window_pre=20, window_post=15, fps=30.0, court_corners=None):
        self.analyzer = analyzer
        self.racket_detector = racket_detector
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post
        self.fps = fps
        self.scale = self._build_scale(court_corners)

    @staticmethod
    def _build_scale(court_corners):
        """Perspective px-per-metre model for the contact gate, or None.

        A contact radius in absolute pixels is wrong across a perspective
        gradient: on this project's fixed-camera footage the scale runs from
        122 px/m at the far baseline to 625 px/m at the near one, so the
        inherited 80 px is 0.13 m near and 0.65 m far -- it accepts
        non-contacts at distance and rejects real ones up close. The metre
        radius has existed since the B11 work; this wires it into the
        technique path, which was still on the fixed pixel value.

        None (no court quad, or a degenerate one) keeps the fixed-pixel
        behaviour exactly, so callers without geometry are unaffected.
        """
        if not court_corners:
            return None
        try:
            from .court.scale import PerspectiveScale
            return PerspectiveScale.from_quad(court_corners)
        except Exception:
            return None

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
            })
        return frames

    def run(self, track, frame_lookup, hits=None, strokes=None):
        """``hits`` (optional): the shared hit list, already filtered to the player this
        runner analyses; when given it replaces the contact rule. ``strokes``
        (optional): frame -> shared label; a hit without a label is ``unknown``."""
        from .stroke.events import CONTACT_M, detect_contacts, StrokeEvent
        if hits is None:
            contacts = detect_contacts(
                track, window_pre=self.window_pre, window_post=self.window_post,
                contact_m=CONTACT_M if self.scale is not None else None,
                scale=self.scale)
        else:
            contacts = [{"contact_frame": int(h["frame"]),
                         "window_start": max(0, int(h["frame"]) - self.window_pre),
                         "window_end": int(h["frame"]) + self.window_post}
                        for h in sorted(hits, key=lambda h: int(h["frame"]))]
        reports = []
        events = []
        for c in contacts:
            label = (strokes or {}).get(c["contact_frame"]) or {}
            stroke_type = label.get("stroke") or "unknown"
            conf = label.get("confidence")
            # player_side from the contact frame's centroid if available, else "unknown"
            contact_rec = frame_lookup(c["contact_frame"]) or {}
            side = contact_rec.get("player_side", "unknown")
            event = StrokeEvent(
                stroke_type=stroke_type,
                contact_frame=c["contact_frame"],
                window_start=c["window_start"],
                window_end=c["window_end"],
                player_side=side,
                confidence=conf,
            )
            window_frames = self._window_frames(
                c["window_start"], c["window_end"], frame_lookup)
            report = self.analyzer.analyze(event, window_frames)
            reports.append(report)
            events.append(event)
        return reports, events
