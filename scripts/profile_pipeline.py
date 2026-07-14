"""Profile the mean per-frame cost of the match pipeline's three heavy
per-frame operations: court-view check, pose detection, ball detection.

Usage: PYTHONUTF8=1 .venv/Scripts/python.exe scripts/profile_pipeline.py <video> [n_frames]

Mirrors the calls ``BadmintonAnalysisSystem._process_frame`` makes
(``badminton_analysis/system.py``) on each court frame:

  - ``is_court_view`` -> ``cv2.matchTemplate`` against a grayscale template
  - ``player_pose_visualizer.detect_players(roi, x1, y1)`` -> YOLO pose inference
  - ``shuttlecock_tracker.detect_ball(frame, roi_corners=...)`` -> YOLO ball inference

This harness runs headlessly (no file-picker court annotation), so the pose/ball
"ROI" is approximated as the full frame (``x1=y1=0``, ``roi_corners`` spanning the
whole frame) instead of the true annotated court crop. That is a conservative
approximation -- the real ROI is a subset of the frame, so production pose/ball
calls are likely *cheaper*, not more expensive, than these measurements -- but it
exercises the identical model call and is representative for the *relative*
per-stage cost this profiling exists to establish.

The first ``WARMUP_FRAMES`` frames are timed but discarded (CUDA context /
kernel warmup) before frames are accumulated into the reported means.
"""
import os
import sys
import time
import statistics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import badminton_analysis.system as bsys

WARMUP_FRAMES = 10


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <video> [n_frames]")
        sys.exit(1)
    video = sys.argv[1]
    n_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 250

    bsys.load_runtime_dependencies()
    cv2 = bsys.cv2
    YOLO = bsys.YOLO
    YOLOPoseProcessor = bsys.YOLOPoseProcessor
    ShuttlecockTracker = bsys.ShuttlecockTracker
    PlayerPoseVisualizer = bsys.PlayerPoseVisualizer

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError(f"Unable to open video: {video}")

    ret, first_frame = cap.read()
    if not ret:
        raise RuntimeError(f"Video has no frames: {video}")
    template_gray = cv2.cvtColor(first_frame, cv2.COLOR_BGR2GRAY)
    height, width = template_gray.shape[:2]
    roi_corners = ((0, 0), (width, height))

    print(f"Loading models (pose=weights/yolo11n-pose.pt, ball=weights/yolo11s-ball.pt)...")
    pose_processor = YOLOPoseProcessor(model_path="weights/yolo11n-pose.pt")
    ball_model = YOLO("weights/yolo11s-ball.pt")
    pose_visualizer = PlayerPoseVisualizer(rtmpose_processor=pose_processor, show_performance_stats=False)
    shuttle_tracker = ShuttlecockTracker(yolo_ball_model=ball_model, show_performance_stats=False)

    court_ms, pose_ms, ball_ms, total_ms = [], [], [], []

    frame_idx = 0
    n_target = WARMUP_FRAMES + n_frames
    while frame_idx < n_target:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1
        warming_up = frame_idx <= WARMUP_FRAMES

        t0 = time.perf_counter()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cv2.matchTemplate(gray, template_gray, cv2.TM_CCOEFF_NORMED)
        t_court = time.perf_counter() - t0

        t0 = time.perf_counter()
        pose_visualizer.detect_players(frame, 0, 0)
        t_pose = time.perf_counter() - t0

        t0 = time.perf_counter()
        shuttle_tracker.detect_ball(frame, roi_corners=roi_corners)
        t_ball = time.perf_counter() - t0

        if not warming_up:
            court_ms.append(t_court * 1000)
            pose_ms.append(t_pose * 1000)
            ball_ms.append(t_ball * 1000)
            total_ms.append((t_court + t_pose + t_ball) * 1000)

    cap.release()

    if not total_ms:
        print("No frames measured (video too short or entirely consumed by warmup).")
        sys.exit(1)

    def _row(name, values):
        return f"{name:<20} {statistics.mean(values):>10.2f} ms"

    print()
    print(f"Frames measured: {len(total_ms)} (warmup discarded: {min(WARMUP_FRAMES, frame_idx)})")
    print(f"{'stage':<20} {'mean ms/frame':>14}")
    print("-" * 36)
    print(_row("court_view_check", court_ms))
    print(_row("pose", pose_ms))
    print(_row("ball", ball_ms))
    print(_row("total", total_ms))


if __name__ == "__main__":
    main()
