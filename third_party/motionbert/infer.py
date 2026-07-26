"""Checkpoint-only MotionBERT loader + input preparation.

This module is **our own extraction**, not vendored verbatim -- see
``README.md`` ("``infer.py`` is our extraction") for the exact upstream
sources each piece is transcribed from.

Why an extraction instead of using upstream ``lib/utils/learning.py``:
``load_backbone(args)`` needs an ``args`` namespace built by
``lib/utils/tools.get_config``, which imports ``yaml`` + ``easydict`` (neither
is a dependency of this project) and needs one of the ``configs/pose3d/*.yaml``
files on disk. Every hyperparameter those configs supply except ``num_heads``
is recoverable from the checkpoint's own tensor shapes, so we derive the
architecture from the state dict and need no config file and no new
dependency.
"""
import numpy as np
import torch
import torch.nn as nn
from functools import partial

from .DSTformer import DSTformer
from .utils_data import crop_scale

# ``num_heads`` is the one DSTformer hyperparameter that leaves no trace in the
# checkpoint's tensor shapes (it only controls how ``dim_feat`` is split inside
# attention). All four upstream ``configs/pose3d/*.yaml`` use 8.
DEFAULT_NUM_HEADS = 8

# Upstream default clip length / positional-embedding capacity
# (``maxlen: 243`` in every ``configs/pose3d/*.yaml``). The real value is read
# from the loaded checkpoint by ``model_maxlen``; this is only a fallback.
MODEL_MAXLEN_DEFAULT = 243

_STATE_DICT_KEYS = ("model_pos", "model", "state_dict", "net")


def _strip_module_prefix(state_dict):
    """Drop the ``module.`` prefix DataParallel checkpoints carry.

    Same normalization upstream ``lib/utils/learning.load_pretrained_weights``
    performs; official MotionBERT checkpoints are saved from
    ``nn.DataParallel`` and so are prefixed.
    """
    out = {}
    for key, value in state_dict.items():
        if key.startswith("module."):
            key = key[7:]
        out[key] = value
    return out


def extract_state_dict(checkpoint):
    """Pull the DSTformer state dict out of a loaded checkpoint object.

    Official MotionBERT pose3d checkpoints (``best_epoch.bin``) are a dict with
    the backbone weights under ``model_pos`` (see upstream ``infer_wild.py``:
    ``model_backbone.load_state_dict(checkpoint['model_pos'], strict=True)``).
    A bare state dict is also accepted.
    """
    if not isinstance(checkpoint, dict):
        raise ValueError("checkpoint is not a dict (got %s)" % type(checkpoint).__name__)
    for key in _STATE_DICT_KEYS:
        inner = checkpoint.get(key)
        if isinstance(inner, dict) and inner:
            return _strip_module_prefix(inner)
    return _strip_module_prefix(checkpoint)


def infer_arch(state_dict, num_heads=DEFAULT_NUM_HEADS):
    """Recover DSTformer constructor kwargs from checkpoint tensor shapes.

    Mapping (all shapes read off ``DSTformer.__init__``):
      ``joints_embed.weight``      (dim_feat, dim_in)
      ``pos_embed``                (1, num_joints, dim_feat)
      ``temp_embed``               (1, maxlen, 1, dim_feat)
      ``blocks_st.<i>.*``          -> depth = max(i) + 1
      ``blocks_st.0.mlp_s.fc1.weight`` (dim_feat * mlp_ratio, dim_feat)
      ``pre_logits.fc.weight``     (dim_rep, dim_feat)   [absent => dim_rep 0]
      ``head.weight``              (dim_out, dim_rep)
      ``ts_attn.0.weight``         present <=> att_fuse True
    """
    try:
        dim_feat, dim_in = tuple(state_dict["joints_embed.weight"].shape)
        num_joints = int(state_dict["pos_embed"].shape[1])
        maxlen = int(state_dict["temp_embed"].shape[1])
    except KeyError as exc:
        raise ValueError("checkpoint is not a DSTformer state dict (missing %s)" % exc)

    depth = 0
    for key in state_dict:
        if key.startswith("blocks_st."):
            part = key.split(".", 2)[1]
            if part.isdigit():
                depth = max(depth, int(part) + 1)
    if depth == 0:
        raise ValueError("checkpoint has no blocks_st.* layers")

    mlp_hidden = int(state_dict["blocks_st.0.mlp_s.fc1.weight"].shape[0])
    mlp_ratio = mlp_hidden // int(dim_feat)

    pre_logits_w = state_dict.get("pre_logits.fc.weight")
    dim_rep = int(pre_logits_w.shape[0]) if pre_logits_w is not None else 0
    head_w = state_dict.get("head.weight")
    dim_out = int(head_w.shape[0]) if head_w is not None else 0

    return {
        "dim_in": int(dim_in),
        "dim_out": dim_out,
        "dim_feat": int(dim_feat),
        "dim_rep": dim_rep,
        "depth": depth,
        "num_heads": int(num_heads),
        "mlp_ratio": mlp_ratio,
        "num_joints": num_joints,
        "maxlen": maxlen,
        "att_fuse": "ts_attn.0.weight" in state_dict,
    }


def load_model(model_path, device="cpu", num_heads=DEFAULT_NUM_HEADS):
    """Build a DSTformer matching ``model_path`` and load its weights.

    Returns an ``eval()``-mode model on ``device``. ``weights_only=False`` is
    required because official checkpoints bundle non-tensor training state
    (epoch/lr/optimizer) alongside ``model_pos``, which torch >= 2.6 refuses to
    unpickle under the new ``weights_only=True`` default. The checkpoint is a
    file the operator downloaded themselves (see ``docs/motionbert-weights.md``).
    """
    checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
    state_dict = extract_state_dict(checkpoint)
    arch = infer_arch(state_dict, num_heads=num_heads)
    net = DSTformer(norm_layer=partial(nn.LayerNorm, eps=1e-6), **arch)
    net.load_state_dict(state_dict, strict=True)
    net.eval()
    net.to(device)
    return net


def model_maxlen(net):
    """Longest clip the model's temporal embedding supports (upstream ``maxlen``)."""
    try:
        return int(net.temp_embed.shape[1])
    except AttributeError:
        return MODEL_MAXLEN_DEFAULT


def model_dim_in(net):
    """Input channels the model expects per joint (3 = x, y, confidence)."""
    try:
        return int(net.joints_embed.in_features)
    except AttributeError:
        return 3


def prepare_clip(kps2d, dim_in=3):
    """``(T, J, 2)`` 2D keypoints -> ``(T, J, dim_in)`` float32 model input.

    Transcribed from upstream ``lib/data/dataset_wild.read_input``'s default
    (non-``--pixel``) branch: append a confidence channel, then normalize with
    ``crop_scale(motion, scale_range=[1, 1])`` -- the person's bounding box over
    the whole clip mapped into ``[-1, 1]`` with aspect ratio preserved.

    ``crop_scale`` is invariant under any uniform scale + translation of its
    input, so it is safe to apply on top of the caller's own screen
    normalization: the result is identical either way.

    ``crop_scale`` draws ``np.random.uniform(1, 1)`` internally; with
    ``scale_range=[1, 1]`` the draw is deterministically ``1.0``, but it would
    still advance the global numpy RNG, so we save and restore RNG state to
    keep this a side-effect-free call.
    """
    arr = np.asarray(kps2d, dtype=np.float32)
    conf = np.ones(arr.shape[:-1] + (1,), dtype=np.float32)
    motion = np.concatenate([arr, conf], axis=-1)      # (T, J, 3): x, y, conf
    rng_state = np.random.get_state()
    try:
        motion = crop_scale(motion, scale_range=[1, 1])
    finally:
        np.random.set_state(rng_state)
    motion = np.asarray(motion, dtype=np.float32)
    if not np.any(motion[..., :2]):
        # crop_scale returns all zeros for a degenerate clip (<4 valid joints,
        # or zero bbox extent) -- refuse rather than feed the model garbage.
        raise ValueError("degenerate 2D clip: crop_scale produced an empty pose")
    if dim_in == 2:
        motion = motion[..., :2]
    return motion
