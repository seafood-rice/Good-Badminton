# tests/test_tracknet_vendor.py
from badminton_analysis.shuttle_track.tracknet import vendored_imports


def test_vendored_modules_import_and_build_models():
    with vendored_imports():
        from utils.general import get_model, HEIGHT, WIDTH  # noqa: E402
        from model import TrackNet, InpaintNet  # noqa: E402

        assert (HEIGHT, WIDTH) == (288, 512)

        # bg_mode='concat' -> in_dim = (seq_len+1)*3 ; out_dim = seq_len
        tn = get_model("TrackNet", seq_len=8, bg_mode="concat")
        assert isinstance(tn, TrackNet)
        assert tn.down_block_1.conv_1.conv.in_channels == (8 + 1) * 3
        assert tn.predictor.out_channels == 8

        inp = get_model("InpaintNet")
        assert isinstance(inp, InpaintNet)


def test_dataset_module_imports_without_pycocotools():
    with vendored_imports():
        import dataset  # noqa: F401  (must not raise; no pycocotools dependency)
        from dataset import Shuttlecock_Trajectory_Dataset, Video_IterableDataset  # noqa: F401
