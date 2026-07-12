import os
import tempfile
from tkinter import filedialog
import tkinter as tk
import time
import argparse

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
                 bst_weights=None):
        self.video_path = video_path
        self.show_display = show_display
        self.language = language
        self.template_path = template_path
        self.ball_model_path = ball_model_path
        self.pose_mode = pose_mode
        self.pose_family = pose_family
        self.yolo_pose_model = yolo_pose_model
        self.show_pose_roi = show_pose_roi
        self.analyze_technique = analyze_technique
        self.racket_model_path = racket_model_path
        self.dominant_hand = dominant_hand
        self.bst_weights = bst_weights
        self._analysis_track = []   # contact detection track
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

        if self.analyze_technique:
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
        if fps <= 0:
            raise RuntimeError(f"Unable to read FPS from video: {self.video_path}")
        video_duration = total_frames / fps
        

        self.fps = fps
        self.performance_log_interval_frames = max(1, int(fps * 5))
        

        template_path = self._get_template_path()
        template_gray, template_color = self._load_template(template_path, cap)
        

        self.frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out = self._setup_video_writer(self.frame_width, self.frame_height, fps)


        corners, roi_corners, mid_height = self._setup_court_annotation(template_color)
        self.court_corners = corners
        self.court_roi_corners = roi_corners

        self._write_metadata(fps, total_frames, video_duration, template_path, corners, roi_corners, mid_height)
        self.detection_writer = JsonlDetectionWriter(self.detections_path)
        

        self.court_mapper = CourtMapper(corners)
        self.player_pose_visualizer.court_mapper = self.court_mapper
        self.player_tracker = PlayerTracker(corners=corners, threshold=mid_height, history_size=30,
                                          detection_writer=self.detection_writer, fps=fps)
        

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
            frame, detect_frame_count = self._process_frame(frame, template_gray, corners, roi_corners, frame_count, out, detect_frame_count)

        # 视频结束时如果还在回合中，记录最后一个回合
        if self.rally_active:
            self.rally_segments.append((self.rally_count, self._current_rally_start, frame_count))

        # 保存回合分段数据
        rally_path = os.path.join(self.save_dir, "rally_segments.json")
        write_json(rally_path, {
            "fps": fps,
            "rallies": [{"id": r[0], "start_frame": r[1], "end_frame": r[2],
                         "start_sec": r[1]/fps, "end_sec": r[2]/fps}
                        for r in self.rally_segments],
        })

        self.end_time = time.time()
        processing_time = self.end_time - self.start_time
        
        print(f"\n处理完成:")
        print(f"原始视频时长: {video_duration:.2f} 秒")
        print(f"处理耗时: {processing_time:.2f} 秒")
        print(f"处理速度比: {processing_time/video_duration:.2f}x")

        if self.analyze_technique:
            self._run_technique_analysis()

        self._run_stroke_recognition()

        self._cleanup(cap)

    def _write_metadata(self, fps, total_frames, video_duration, template_path, corners, roi_corners, mid_height):
        metadata = {
            "schema_version": SCHEMA_VERSION,
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
            },
            "outputs": {
                "video": self.output_video_path,
                "detections": self.detections_path,
            },
        }
        write_json(self.metadata_path, metadata)

    def _process_frame(self, frame, template_gray, corners, roi_corners, frame_count, out, detect_frame_count):

        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # frame = self.draw_court_roi(frame, corners, roi_corners)

        is_court = self.is_court_view(gray_frame, template_gray)
        
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
            return frame, detect_frame_count

        detect_frame_count += 1

        x1, y1 = roi_corners[0]
        x2, y2 = roi_corners[1]
        roi = frame[y1:y2, x1:x2]
        if self.show_pose_roi:
            cv2.rectangle(frame, roi_corners[0], roi_corners[1], (255, 0, 0), 2)
            cv2.putText(frame, "Pose ROI", (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2, cv2.LINE_AA)


        pose_t0 = time.time()
        centroids, point_left_hands, point_right_hands = self.player_pose_visualizer.detect_players(roi, x1, y1)
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

        if self.analyze_technique:
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

        if self._racket_detector is not None:
            racket_head = self._racket_detector.detect_racket_head(frame, roi_corners=roi_corners)
        if racket_head is None and keypoints is not None:
            from .analysis.joint_angles import infer_racket_head
            racket_head = infer_racket_head(keypoints, dominant=self.dominant_hand)

        shuttle = None
        if ball_position and ball_position != [0, 0]:
            shuttle = (float(ball_position[0]), float(ball_position[1]))

        self._analysis_track.append({
            "frame": frame_count, "racket_head": racket_head, "shuttle": shuttle,
        })
        self._analysis_frames[frame_count] = {
            "frame": frame_count, "keypoints": keypoints, "conf": None,
            "racket_head": racket_head, "centroid": centroid, "nose": nose,
            "shoulder": shoulder, "hip": hip, "elbow_angle": elbow_angle,
            "player_side": side, "shuttle": shuttle,
        }

    def _run_technique_analysis(self):
        from .analysis.biomechanics import BiomechanicalAnalyzer
        from .analysis.technique_writer import write_stroke_reports, build_match_summary
        runner = TechniqueAnalysisRunner(
            BiomechanicalAnalyzer(dominant=self.dominant_hand),
            racket_detector=self._racket_detector,
            dominant=self.dominant_hand,
            fps=self.fps,
        )
        reports, _events = runner.run(self._analysis_track, self._analysis_frames.get)
        strokes_path = os.path.join(self.save_dir, "strokes.jsonl")
        summary_path = os.path.join(self.save_dir, "technique_summary.json")
        write_stroke_reports(strokes_path, reports)
        write_json(summary_path, build_match_summary(reports))
        print(f"Technique analysis: {len(reports)} strokes -> {strokes_path}")

    def _run_stroke_recognition(self):
        """Post-loop BST coarse stroke labeling -- entirely optional.

        No-ops (writes nothing) unless ``self.bst_weights`` was passed to the
        constructor, so the default behavior of the pipeline is byte-for-byte
        unchanged. ``StrokeRecognizer`` already degrades gracefully (returns
        ``[]``) if the weights fail to load, so this is safe to call
        unconditionally from ``process_video``.

        v1 limitation: ``stroke_recog.inputs.build_inputs`` (Task 3) only
        fills in the tracked hitter's own pose/position (person index 0) plus
        the shuttle; the opponent (person index 1) pose/position stay
        zero-filled every frame. This is a documented v1 simplification --
        whether coarse labels survive it on real footage is what the T9
        validation decides, not something this task attempts to fix.
        """
        if not self.bst_weights:
            return

        from collections import Counter
        from .stroke_recog.recognizer import StrokeRecognizer

        labels = StrokeRecognizer(self.bst_weights).label_rally(
            self._analysis_track, self._analysis_frames.get,
            self.court_roi_corners, (self.frame_width, self.frame_height),
        )
        strokes_path = os.path.join(self.save_dir, "strokes.json")
        distribution = dict(Counter(label["stroke"] for label in labels))
        write_json(strokes_path, {"strokes": labels, "distribution": distribution})
        print(f"Stroke recognition: {len(labels)} strokes -> {strokes_path}")

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
        """Load and resize the court template image."""
        template_gray = cv2.imread(template_path, 0)
        template_color = cv2.imread(template_path)
        if template_gray is None or template_color is None:
            raise RuntimeError(f"Unable to read court template image: {template_path}")
        
        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        template_gray = cv2.resize(template_gray, (frame_width, frame_height))
        template_color = cv2.resize(template_color, (frame_width, frame_height))
        
        return template_gray, template_color

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
            corners, roi_corners, mid_height = annotate_court(template_color, auto_preview_path=auto_preview_path)
       
        if not corners or not roi_corners or len(corners) != 4 or len(roi_corners) != 2:
            raise RuntimeError("Court annotation is incomplete: click 4 court corners in order. ROI is generated automatically.")

        with open(os.path.join(self.save_dir, 'court_annotations.txt'), 'w') as f:
            f.write(f"corners={corners}\n")
            f.write(f"roi_corners={roi_corners}\n")
            f.write(f"mid_height={mid_height}\n")
        return corners, roi_corners, mid_height

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

    def analyze_shuttlecock(self, roi_corners, corners):
        """Hit-point analysis is currently disabled."""
        raise RuntimeError(
            "Hit-point analysis is disabled until it is migrated to detections.jsonl."
        )

    def is_court_view(self, frame, template_gray, threshold=0.75):
        """Return whether the frame matches the court template."""
        result = cv2.matchTemplate(frame, template_gray, cv2.TM_CCOEFF_NORMED)
        # print("match score: ", result)
        return np.max(result) >= threshold

    def draw_court_roi(self, frame, corners, roi_corners):
        self.court_mapper = CourtMapper(corners)
        overlay, mid_height_int = self.court_mapper.draw_court_overlay(frame)
        cv2.rectangle(overlay, roi_corners[0], roi_corners[1], (255, 0, 0), 2)
        return overlay


class TechniqueAnalysisRunner:
    """Post-loop orchestration: contacts -> classification -> biomechanical reports."""

    def __init__(self, analyzer, racket_detector=None, dominant="right",
                 window_pre=20, window_post=15, fps=30.0):
        self.analyzer = analyzer
        self.racket_detector = racket_detector
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post
        self.fps = fps

    def _build_classifier_window(self, contact_frame, window_start, window_end, frame_lookup):
        racket_head, nose, shoulder, hip, elbow_angle, centroid = [], [], [], [], [], []
        contact_index = 0
        for offset, idx in enumerate(range(window_start, window_end + 1)):
            rec = frame_lookup(idx) or {}
            if idx == contact_frame:
                contact_index = offset
            racket_head.append(rec.get("racket_head"))
            nose.append(rec.get("nose"))
            shoulder.append(rec.get("shoulder"))
            hip.append(rec.get("hip"))
            elbow_angle.append(rec.get("elbow_angle"))
            centroid.append(rec.get("centroid"))
        return {
            "contact_index": contact_index, "racket_head": racket_head, "nose": nose,
            "shoulder": shoulder, "hip": hip, "elbow_angle": elbow_angle,
            "centroid": centroid, "fps": self.fps,
        }

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

    def run(self, track, frame_lookup):
        from .stroke.events import detect_contacts, StrokeEvent
        from .stroke.classifier import classify_stroke
        contacts = detect_contacts(
            track, window_pre=self.window_pre, window_post=self.window_post)
        reports = []
        events = []
        for c in contacts:
            cw = self._build_classifier_window(
                c["contact_frame"], c["window_start"], c["window_end"], frame_lookup)
            stroke_type, conf = classify_stroke(cw)
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
