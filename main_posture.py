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
    parser.add_argument("--display", choices=["true", "false"], default="false")
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
    )
    system.process_video()


if __name__ == "__main__":
    main()
