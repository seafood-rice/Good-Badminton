from main_posture import build_parser


def test_lift_flags_defaults():
    args = build_parser().parse_args(["--video-path", "v.mp4", "--stroke-type", "smash"])
    assert args.lift_model is None
    assert args.lift_device == "auto"


def test_lift_flags_parsed():
    args = build_parser().parse_args(
        ["--video-path", "v.mp4", "--stroke-type", "smash",
         "--lift-model", "weights/motionbert.pt", "--lift-device", "cuda"])
    assert args.lift_model == "weights/motionbert.pt"
    assert args.lift_device == "cuda"
