"""Court-free single-player posture/technique drill analysis."""
import os
import time

from .rep_segmenter import segment_reps
from .writer import write_rep_reports, build_drill_summary


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


class PostureRunner:
    """Post-loop orchestration: reps -> per-rep StrokeEvent -> biomechanical reports."""

    def __init__(self, analyzer, stroke_type, dominant="right",
                 window_pre=20, window_post=15, quality_scorer=None):
        self.analyzer = analyzer
        self.stroke_type = stroke_type
        self.dominant = dominant
        self.window_pre = window_pre
        self.window_post = window_post
        self.quality_scorer = quality_scorer

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
            if self.quality_scorer is not None:
                ai = self.quality_scorer.score(window_frames, dominant=self.dominant)
                if ai is not None:
                    report["ai_score"] = ai
            reports.append(report)
        return reports, reps


class PostureAnalysisSystem:
    """Run the full court-free pipeline over a video file."""

    def __init__(self, video_path, stroke_type, dominant_hand="right",
                 output_dir=None, ball_model_path=None, show_display=False,
                 show_overlay=True, pose_model="weights/yolo11n-pose.pt",
                 pose_family="yolo-pose", pose_mode="balanced",
                 yolo_pose_model="weights/yolo11n-pose.pt",
                 report_llm="off", racket_model_path=None, quality_model_path=None):
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

    def _write_reports(self, reports, summary, date=None):
        from .report_builder import build_coach_report
        from .report_render import render_html, render_pdf
        from ..data.writer import write_json
        date = date or _today()
        meta = {"date": date, "stroke_type": self.stroke_type,
                "dominant_hand": self.dominant_hand, "pose_family": getattr(self, "pose_family", "yolo-pose")}
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
                               quality_scorer=self._quality_scorer)
        reports, reps = runner.run(self._track, self._frames.get, fps)

        write_rep_reports(os.path.join(self.save_dir, "drill_reps.jsonl"), reports)
        summary = build_drill_summary(reports, self.stroke_type)
        write_json(os.path.join(self.save_dir, "drill_summary.json"), summary)
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
        })
        print(format_progress(94, "report"), flush=True)
        self._write_reports(reports, summary, date=_today())
        print("Posture analysis: " + str(len(reports)) + " reps -> " + self.save_dir)
        print("Elapsed: " + str(round(time.time() - start, 1)) + "s")
        print("Racket source: %d detected / %d inferred"
              % (self._racket_stats["detected"], self._racket_stats["inferred"]), flush=True)
        return reports

    def _build_racket_detector(self):
        """Optional trained racket detector; analysis proceeds on failure."""
        if not self.racket_model_path:
            return
        try:
            from ..detection.racket import RacketDetector
            self._racket_detector = RacketDetector(model_path=self.racket_model_path)
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

    def _resolve_racket_head(self, frame, kp, ja):
        if self._racket_detector is not None:
            head = self._racket_detector.detect_racket_head(frame)
            if head is not None:
                self._racket_stats["detected"] += 1
                return head
        if kp is None:
            return None
        self._racket_stats["inferred"] += 1
        return ja.infer_racket_head(kp, dominant=self.dominant_hand)

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
        # Detector must see the clean frame (before any overlay drawing), and runs
        # even when pose detection failed - mirroring the match pipeline.
        racket_head = self._resolve_racket_head(frame, kp, ja)
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
                    b = boxes.xywh.detach().cpu().numpy()[0]
                    shuttle = (float(b[0]), float(b[1]))
            except Exception:
                shuttle = None

        self._track.append({"frame": frame_count, "wrist": wrist, "shuttle": shuttle})
        self._frames[frame_count] = {
            "frame": frame_count, "keypoints": kp, "conf": conf_row,
            "racket_head": racket_head, "centroid": centroid,
        }
