"""Court-free single-player posture/technique drill analysis."""
import os
import time

from .rep_segmenter import segment_reps
from .writer import write_rep_reports, build_drill_summary


class PostureRunner:
    """Post-loop orchestration: reps -> per-rep StrokeEvent -> biomechanical reports."""

    def __init__(self, analyzer, stroke_type, dominant="right",
                 window_pre=20, window_post=15):
        self.analyzer = analyzer
        self.stroke_type = stroke_type
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post

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

    def run(self, track, frame_lookup, fps):
        from ..stroke.events import StrokeEvent
        reps = segment_reps(track, fps, pre=self.window_pre, post=self.window_post)
        reports = []
        for rep in reps:
            event = StrokeEvent(
                stroke_type=self.stroke_type,
                contact_frame=rep.peak_frame,
                window_start=rep.window_start,
                window_end=rep.window_end,
                player_side="single",
                confidence=rep.prominence,
            )
            window_frames = self._window_frames(rep.window_start, rep.window_end, frame_lookup)
            report = self.analyzer.analyze(event, window_frames)
            report["rep_id"] = rep.rep_id
            reports.append(report)
        return reports, reps


class PostureAnalysisSystem:
    """Run the full court-free pipeline over a video file."""

    def __init__(self, video_path, stroke_type, dominant_hand="right",
                 output_dir=None, ball_model_path=None, show_display=False,
                 show_overlay=True, pose_model="weights/yolo11n-pose.pt",
                 pose_family="yolo-pose", pose_mode="balanced",
                 yolo_pose_model="weights/yolo11n-pose.pt"):
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

        self.video_name = os.path.basename(video_path).rsplit(".", 1)[0]
        self.save_dir = output_dir or os.path.join("outputs", self.video_name, "posture")
        os.makedirs(self.save_dir, exist_ok=True)
        self.output_video_path = os.path.join(self.save_dir, "detect_" + self.video_name + ".mp4")

        self._track = []
        self._frames = {}

    def _build_pose_processor(self):
        if self.pose_family == "yolo-pose":
            from ..detection.yolo_pose import YOLOPoseProcessor
            return YOLOPoseProcessor(model_path=self.yolo_pose_model or self.pose_model)
        from ..detection.rtmpose import RTMPoseProcessor
        return RTMPoseProcessor(mode=self.pose_mode, pose_family=self.pose_family)

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

        pose = self._build_pose_processor()
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

        runner = PostureRunner(BiomechanicalAnalyzer(dominant=self.dominant_hand),
                               stroke_type=self.stroke_type, dominant=self.dominant_hand)
        reports, reps = runner.run(self._track, self._frames.get, fps)

        write_rep_reports(os.path.join(self.save_dir, "drill_reps.jsonl"), reports)
        write_json(os.path.join(self.save_dir, "drill_summary.json"),
                   build_drill_summary(reports, self.stroke_type))
        write_json(os.path.join(self.save_dir, "metadata.json"), {
            "video": {"path": self.video_path, "name": self.video_name,
                      "fps": float(fps), "width": width, "height": height},
            "mode": "posture", "stroke_type": self.stroke_type,
            "dominant_hand": self.dominant_hand,
            "pose_family": self.pose_family,
        })
        print("Posture analysis: " + str(len(reports)) + " reps -> " + self.save_dir)
        print("Elapsed: " + str(round(time.time() - start, 1)) + "s")
        return reports

    def _capture_frame(self, frame, frame_count, pose, ball_model, dom_wrist,
                       ja, draw_technique_overlay, draw_skeleton):
        keypoints, scores = pose.process_frame(frame)
        kp = None
        wrist = None
        racket_head = None
        centroid = None
        conf_row = None
        if keypoints is not None and len(keypoints) > 0:
            # Single-player drill: take the largest-bbox person (max keypoint spread).
            def _spread(person):
                xs = person[:, 0]
                ys = person[:, 1]
                return float((xs.max() - xs.min()) + (ys.max() - ys.min()))
            best_i = max(range(len(keypoints)), key=lambda i: _spread(keypoints[i]))
            kp = keypoints[best_i].astype(float)
            conf_row = scores[best_i] if scores is not None else None
            draw_skeleton(frame, kp, conf=conf_row)
            if ja.is_valid(kp, dom_wrist, conf_row):
                wrist = (float(kp[dom_wrist][0]), float(kp[dom_wrist][1]))
            racket_head = ja.infer_racket_head(kp, dominant=self.dominant_hand)
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
                    b = boxes.xywh.detach().cpu().numpy()[0]
                    shuttle = (float(b[0]), float(b[1]))
            except Exception:
                shuttle = None

        self._track.append({"frame": frame_count, "wrist": wrist, "shuttle": shuttle})
        self._frames[frame_count] = {
            "frame": frame_count, "keypoints": kp, "conf": conf_row,
            "racket_head": racket_head, "centroid": centroid,
        }
