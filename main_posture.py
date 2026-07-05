#!/usr/bin/env python3
"""Posture Drill analysis CLI (court-free, single player)."""
import argparse


def main():
    parser = argparse.ArgumentParser(description="Badminton posture/technique drill analysis")
    parser.add_argument("--video-path", required=True, help="Input drill video path")
    parser.add_argument("--stroke-type", required=True,
                        choices=["high_clear", "smash", "drop_shot", "serve"],
                        help="Stroke being practiced")
    parser.add_argument("--dominant-hand", default="right", choices=["right", "left"])
    parser.add_argument("--output-dir", default=None,
                        help="Output dir (default outputs/<video>/posture)")
    parser.add_argument("--ball-model", default=None,
                        help="Optional YOLO shuttlecock model for rep refinement")
    parser.add_argument("--pose-model", default="weights/yolo11n-pose.pt")
    parser.add_argument("--pose-family", default="yolo-pose",
                        choices=["yolo-pose", "rtmpose", "rtmo"],
                        help="Pose model family for skeleton detection")
    parser.add_argument("--pose-mode", default="balanced",
                        choices=["lightweight", "balanced", "performance"],
                        help="RTMPose/RTMO model tier (ignored for yolo-pose)")
    parser.add_argument("--yolo-pose-model", default="weights/yolo11n-pose.pt",
                        help="YOLO pose model path (used when pose-family=yolo-pose)")
    parser.add_argument("--display", choices=["true", "false"], default="false")
    parser.add_argument("--report-llm", default="off",
                        help="LLM polish spec 'provider:model' or 'off' (default off)")
    parser.add_argument("--racket-model", default=None,
                        help="Trained racket detector weights (optional)")
    parser.add_argument("--quality-model", default=None,
                        help="Trained AI form-score weights (optional)")
    args = parser.parse_args()

    from badminton_analysis.posture.system import PostureAnalysisSystem
    system = PostureAnalysisSystem(
        video_path=args.video_path,
        stroke_type=args.stroke_type,
        dominant_hand=args.dominant_hand,
        output_dir=args.output_dir,
        ball_model_path=args.ball_model,
        show_display=args.display == "true",
        pose_model=args.pose_model,
        pose_family=args.pose_family,
        pose_mode=args.pose_mode,
        yolo_pose_model=args.yolo_pose_model,
        report_llm=args.report_llm,
        racket_model_path=args.racket_model,
        quality_model_path=args.quality_model,
    )
    system.process_video()


if __name__ == "__main__":
    main()
