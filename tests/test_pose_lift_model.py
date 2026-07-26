"""Real MotionBERT adapter tests.

``test_real_model_lifts_to_3d`` needs the downloaded checkpoint (see
``docs/motionbert-weights.md``) and skips without it. The remaining tests are
weight-free: they build a tiny randomly-initialised DSTformer, save it in the
same layout as an official checkpoint, and drive the real
``PoseLifter._load()`` adapter through it. That exercises architecture
recovery, device placement, the confidence channel, ``crop_scale``
normalization, root-relativisation and the long-window resample path without
needing any download.
"""
import os
from functools import partial

import numpy as np
import pytest
import torch
import torch.nn as nn

from badminton_analysis.detection.pose_lift import PoseLifter
from third_party.motionbert import infer as mb_infer
from third_party.motionbert.DSTformer import DSTformer

_WEIGHTS = "weights/motionbert.pt"


@pytest.mark.skipif(not os.path.exists(_WEIGHTS), reason="MotionBERT weights not present")
def test_real_model_lifts_to_3d():
    lifter = PoseLifter(model_path=_WEIGHTS, device="cpu")
    assert lifter.available
    frames = []
    for i in range(16):
        kp = np.full((17, 2), 50.0, dtype=float)
        kp[11], kp[12] = (95.0, 200.0), (105.0, 200.0)
        frames.append({"frame": i, "keypoints": kp})
    result = lifter.lift(frames, image_size=(320, 240))
    assert result is not None
    kp3d, out_frames = result
    assert kp3d.shape == (16, 17, 3)
    assert len(out_frames) == 16


# ---- weight-free adapter coverage ---------------------------------------

_TINY = dict(dim_in=3, dim_out=3, dim_feat=32, dim_rep=16, depth=1,
             num_heads=4, mlp_ratio=2, num_joints=17, maxlen=32, att_fuse=True)


def _tiny_checkpoint(tmp_path, **overrides):
    """Save a tiny DSTformer the way official checkpoints are saved."""
    kwargs = dict(_TINY)
    kwargs.update(overrides)
    net = DSTformer(norm_layer=partial(nn.LayerNorm, eps=1e-6), **kwargs)
    # Official checkpoints come from nn.DataParallel -> "module." prefixed,
    # nested under "model_pos", alongside non-tensor training state.
    state = {"module." + k: v for k, v in net.state_dict().items()}
    path = tmp_path / "tiny_motionbert.pt"
    torch.save({"epoch": 7, "lr": 1e-4, "model_pos": state}, str(path))
    return str(path), kwargs


def _frames(count):
    frames = []
    for i in range(count):
        kp = np.full((17, 2), 50.0 + i, dtype=float)
        kp[11], kp[12] = (95.0 + i, 200.0), (105.0 + i, 200.0)
        kp[5], kp[6] = (90.0, 120.0), (110.0, 120.0)
        frames.append({"frame": i, "keypoints": kp})
    return frames


@pytest.mark.parametrize("dim_feat, mlp_ratio", [
    (32, 2),      # tiny stand-in
    (512, 2),     # configs/pose3d/MB_ft_h36m.yaml (and MB_train_h36m.yaml)
    (256, 4),     # configs/pose3d/MB_ft_h36m_global_lite.yaml
])
def test_infer_arch_recovers_config_from_checkpoint(tmp_path, dim_feat, mlp_ratio):
    """Covers both official pose3d shapes, including lite's mlp_ratio=4 (which
    exercises infer_arch's mlp_hidden // dim_feat integer division)."""
    path, kwargs = _tiny_checkpoint(tmp_path, dim_feat=dim_feat, mlp_ratio=mlp_ratio)
    ckpt = torch.load(path, map_location="cpu", weights_only=False)
    state = mb_infer.extract_state_dict(ckpt)
    assert not any(k.startswith("module.") for k in state)
    derived = mb_infer.infer_arch(state, num_heads=kwargs["num_heads"])
    assert derived == kwargs
    # ...and the derived kwargs really do reconstruct a strict-loadable model.
    net = mb_infer.load_model(path, device="cpu", num_heads=kwargs["num_heads"])
    assert mb_infer.model_dim_in(net) == 3


def test_load_model_strict_and_eval(tmp_path):
    path, kwargs = _tiny_checkpoint(tmp_path)
    net = mb_infer.load_model(path, device="cpu", num_heads=kwargs["num_heads"])
    assert net.training is False
    assert mb_infer.model_maxlen(net) == kwargs["maxlen"]
    assert mb_infer.model_dim_in(net) == 3


def test_adapter_lifts_window_to_root_relative_3d(tmp_path):
    path, kwargs = _tiny_checkpoint(tmp_path)
    lifter = PoseLifter(model_path=path, device="cpu", model=None)
    assert lifter.available
    result = lifter.lift(_frames(16), image_size=(320, 240))
    assert result is not None
    kp3d, out_frames = result
    assert kp3d.shape == (16, 17, 3)
    assert out_frames == list(range(16))
    # Pelvis (H36M joint 0) is the origin in every frame.
    assert np.allclose(kp3d[:, 0, :], 0.0, atol=1e-6)
    assert np.isfinite(kp3d).all()


def test_adapter_resamples_windows_longer_than_maxlen(tmp_path):
    # maxlen 8 forces the M -> maxlen -> M resample path for a 20-frame window.
    path, _ = _tiny_checkpoint(tmp_path, maxlen=8)
    lifter = PoseLifter(model_path=path, device="cpu")
    result = lifter.lift(_frames(20), image_size=(320, 240))
    assert result is not None
    kp3d, out_frames = result
    assert kp3d.shape == (20, 17, 3)
    assert len(out_frames) == 20
    assert np.isfinite(kp3d).all()


def test_prepare_clip_appends_confidence_and_is_similarity_invariant():
    rng = np.random.default_rng(0)
    clip = rng.uniform(20.0, 400.0, size=(12, 17, 2)).astype(np.float32)
    out = mb_infer.prepare_clip(clip, dim_in=3)
    assert out.shape == (12, 17, 3)
    assert np.allclose(out[..., 2], 1.0)
    assert out[..., :2].min() >= -1.0 and out[..., :2].max() <= 1.0
    # crop_scale is invariant to one shared scale plus PER-AXIS translation --
    # exactly the transform _normalize_screen applies (shared 2/w scale, but
    # offsets -1 on x and -h/w on y). Mirror that shape here, not an equal
    # offset on both axes.
    moved = clip * 3.0 + np.array([17.0, -4.5], dtype=np.float32)
    assert np.allclose(out, mb_infer.prepare_clip(moved, dim_in=3), atol=1e-5)


def test_prepare_clip_is_unchanged_by_normalize_screen():
    """The concrete claim the adapter relies on: running _normalize_screen first
    (as PoseLifter.lift does) does not change what the model receives."""
    rng = np.random.default_rng(1)
    px = rng.uniform(20.0, 400.0, size=(12, 17, 2))
    from badminton_analysis.detection.pose_lift import _normalize_screen
    direct = mb_infer.prepare_clip(px.astype(np.float32), dim_in=3)
    screened = mb_infer.prepare_clip(
        _normalize_screen(px, (640, 480)).astype(np.float32), dim_in=3)
    assert np.allclose(direct, screened, atol=1e-5)


def test_prepare_clip_does_not_disturb_global_numpy_rng():
    """crop_scale calls np.random.uniform internally; prepare_clip must not leak
    that into the process-wide RNG stream."""
    rng = np.random.default_rng(2)
    clip = rng.uniform(20.0, 400.0, size=(10, 17, 2)).astype(np.float32)
    np.random.seed(1234)
    expected = np.random.rand(3)
    np.random.seed(1234)
    mb_infer.prepare_clip(clip, dim_in=3)
    assert np.allclose(np.random.rand(3), expected)


def test_prepare_clip_rejects_degenerate_clip():
    with pytest.raises(ValueError):
        mb_infer.prepare_clip(np.zeros((10, 17, 2), dtype=np.float32))


def test_missing_weights_leaves_lifter_unavailable(tmp_path):
    lifter = PoseLifter(model_path=str(tmp_path / "absent.pt"), device="cpu")
    assert lifter.available is False
    assert lifter.lift(_frames(16), image_size=(320, 240)) is None


def test_unreadable_weights_degrade_to_no_lift(tmp_path):
    bad = tmp_path / "not_a_checkpoint.pt"
    bad.write_bytes(b"not a torch checkpoint")
    lifter = PoseLifter(model_path=str(bad), device="cpu")
    assert lifter.available is True       # file exists, so the lazy load is tried
    assert lifter.lift(_frames(16), image_size=(320, 240)) is None
    assert lifter.available is False      # ...and the failure disables it
