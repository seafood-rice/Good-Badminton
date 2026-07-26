# MotionBERT vendoring contract

Vendored inference subset of [MotionBERT](https://github.com/Walter0807/MotionBERT)
(Apache License 2.0 — full text in `LICENSE`), used by
`badminton_analysis/detection/pose_lift.py` to lift 2D COCO-17 keypoints to 3D
H36M-17 joints. Like `third_party/tracknet` and `third_party/bst`, everything
here except `infer.py` is upstream code copied as-is; this file records the
exact facts downstream code is built against so it stays reviewable against
upstream.

## Pinned upstream commit

- **Repo:** `https://github.com/Walter0807/MotionBERT`
- **Commit:** `705d3a95354db8bdb696b3492e47a3b5537174ff` (2026-03-14, "Update mesh.md")
- **License:** Apache License 2.0 (`LICENSE`, copied verbatim from the repo root)

## Attribution

```bibtex
@inproceedings{motionbert2022,
  title     =   {MotionBERT: A Unified Perspective on Learning Human Motion Representations},
  author    =   {Zhu, Wentao and Ma, Xiaoxuan and Liu, Zhaoyang and Liu, Libin and Wu, Wayne and Wang, Yizhou},
  booktitle =   {Proceedings of the IEEE/CVF International Conference on Computer Vision},
  year      =   {2023},
}
```

`drop.py` carries its own upstream-of-upstream attribution in its docstring —
"Hacked together by / Copyright 2020 Ross Wightman", i.e. MotionBERT's own
vendored copy of `timm`'s `DropPath` (also Apache-2.0). We reuse MotionBERT's
copy rather than adding a `timm` dependency; see "No new dependencies" below.

## What was vendored (verbatim, byte-for-byte)

| Here | Upstream path | Why the inference path needs it |
|---|---|---|
| `DSTformer.py` | `lib/model/DSTformer.py` | The model itself: `DSTformer` plus its `Block`/`Attention`/`MLP` parts and the local `trunc_normal_` init. One-line edit, below. |
| `drop.py` | `lib/model/drop.py` | `DropPath`, imported by `DSTformer.Block`. Pure-torch; `drop_path` is a no-op in `eval()` mode but the symbol must resolve at import. |
| `utils_data.py` | `lib/utils/utils_data.py` | `crop_scale` — the input normalization upstream's in-the-wild inference uses. Copied whole (rather than excerpting one function) to keep the file diffable against upstream; the training/dataset helpers it also contains (`crop_scale_3d`, `flip_data`, `resample`, `split_clips`) are unused by us. |
| `LICENSE` | `LICENSE` | Upstream Apache-2.0 text. |

Verified with `diff` against the pinned clone at copy time: `drop.py`,
`utils_data.py` and `LICENSE` are identical; `DSTformer.py` differs on exactly
one line.

**The single edit to `DSTformer.py`** (line 10), needed because the vendored
files are flattened out of `lib/model/` into a package of their own:

```diff
-from lib.model.drop import DropPath
+from .drop import DropPath   # VENDORING EDIT: was `from lib.model.drop import DropPath`
```

**Not vendored** — no part of the 2D→3D inference path needs them: `train*.py`,
`infer_wild.py`/`infer_wild_mesh.py` (argparse CLIs that pull in `imageio`,
`tqdm`, `matplotlib` and AlphaPose JSON handling), `lib/data/*` (dataset/
datareader modules; `dataset_wild.py` additionally imports `ipdb` at module
load, which is not an installed dependency), `lib/model/loss*.py`,
`lib/model/model_action.py`, `lib/model/model_mesh.py`, `lib/utils/learning.py`
(see below), `lib/utils/tools.py`, `lib/utils/vismo.py`,
`lib/utils/utils_mesh.py`, `lib/utils/utils_smpl.py`, `tools/`, `configs/`,
`docs/`, and upstream's `requirements.txt`.

## No new dependencies

The plan's hard constraint is "no new pip dependencies". The vendored subset
imports only `torch`, `numpy` and the stdlib — verified: neither `einops` nor
`timm` appears anywhere in it. Concretely:

- **`einops`** is not used by `DSTformer.py` at all. Its attention blocks are
  already written with plain `.reshape()`/`.permute()`/`.transpose()`
  (`Attention.forward_spatial`, `forward_temporal`, `forward_coupling`,
  `reshape_T`), so no `rearrange` calls needed rewriting.
- **`timm`** is not needed either: upstream already inlines the two utilities
  that would otherwise come from it — `DropPath` in `drop.py`, and
  `trunc_normal_` / `_no_grad_trunc_normal_` at the top of `DSTformer.py`.

`requirements.txt` is therefore unchanged by this vendoring.

## `infer.py` is our extraction, not vendored verbatim

`infer.py` replaces upstream's `lib/utils/learning.load_backbone(args)` +
`infer_wild.py` checkpoint-loading block. It is not a copy:

1. `load_backbone(args)` needs an `args` namespace produced by
   `lib/utils/tools.get_config`, which imports `yaml` **and** `easydict` — two
   packages this project does not have and will not add — and needs one of the
   `configs/pose3d/*.yaml` files on disk. `infer_arch()` instead recovers every
   DSTformer constructor argument from the checkpoint's own tensor shapes, so no
   config file and no new dependency are required, and both the full and the
   lite checkpoints load from the same code path. Verified round-trip for both
   official `pose3d` configs in `tests/test_pose_lift_model.py`.
2. `num_heads` is the one hyperparameter with no trace in the tensor shapes (it
   only controls how `dim_feat` is split inside attention). All four upstream
   `configs/pose3d/*.yaml` use `8`, which is `DEFAULT_NUM_HEADS`.
3. `extract_state_dict()` reproduces upstream's checkpoint layout handling —
   `infer_wild.py` reads `checkpoint['model_pos']`, and
   `learning.load_pretrained_weights` strips the `module.` prefix that
   `nn.DataParallel`-saved checkpoints carry.
4. `torch.load(..., weights_only=False)`, as in `third_party/tracknet`:
   official checkpoints bundle non-tensor training state (`epoch`, `lr`)
   alongside `model_pos`, which torch >= 2.6 refuses to unpickle under its new
   `weights_only=True` default.
5. `prepare_clip()` is transcribed from `lib/data/dataset_wild.read_input`'s
   default (non-`--pixel`) branch: append a confidence channel, then
   `crop_scale(motion, scale_range=[1, 1])`. Two deliberate differences from
   upstream: the numpy RNG state is saved/restored around `crop_scale` (which
   calls `np.random.uniform(1, 1)` — deterministic, but it would otherwise
   advance the global RNG), and a degenerate all-zero `crop_scale` result
   raises `ValueError` instead of being fed to the model.
6. No CUDA is hardcoded and there is no `nn.DataParallel` wrapper (upstream's
   `infer_wild.py` applies both unconditionally when CUDA is present); the
   caller passes `device`.

## Confirmed model contract (authoritative for `pose_lift.py`)

```
dim_in     = 3        # (x, y, confidence) per joint -- joints_embed: Linear(3, dim_feat)
dim_out    = 3        # (image_x, image_y, depth)
num_joints = 17       # H36M-17, MotionBERT joint order
maxlen     = 243      # temporal-embedding capacity; clip length T is VARIABLE, T <= maxlen
num_heads  = 8        # not derivable from weights; all configs/pose3d/*.yaml use 8
```

- **Clip length is variable, not fixed at 243.** `DSTformer.forward` slices
  `self.temp_embed[:, :F, :, :]`, so any `F <= maxlen` runs; `F > maxlen`
  raises a shape error. Upstream `infer_wild.py` itself feeds a video's short
  final tail straight through. Confirmed empirically for `F` = 1, 8, 16, 243
  (pass) and 244 (raises).
- **Input normalization** (`lib/data/datareader_h36m.DataReaderH36M.read_2d`):
  training 2D joints are mapped with `joints / res_w * 2 - [1, res_h / res_w]`
  — exactly the VideoPose3D formula `pose_lift._normalize_screen` already
  implements, so that function needed no change. On top of it, `prepare_clip`
  applies upstream's wild-inference `crop_scale`, which is invariant under
  uniform scale + translation and therefore composes with `_normalize_screen`
  without double-normalizing.
- **Output orientation: vertical axis is index 1 (Y), pointing down.**
  `read_3d` normalizes the `joint3d_image` supervision's x/y with the same
  image-space formula as the 2D input (z by `/ res_w * 2`), so output channels
  are `(image_x, image_y, depth)`. Two corroborations: `infer_wild.py`'s
  `--gt_2d` overwrites `predicted_3d_pos[..., :2] = batch_input[..., :2]`, and
  `lib/utils/vismo.motion2video_3d` plots `-ys` as its up axis. Hence
  `badminton_analysis/analysis/joint_angles.VERTICAL_AXIS_3D = 1` is correct as
  set in Tasks 2/3/5 and was left unchanged.
- **Root-relativity varies by checkpoint** (`rootrel: True` in
  `configs/pose3d/MB_ft_h36m.yaml`, `False` in the `_global` variants), so the
  adapter in `pose_lift.PoseLifter._load` subtracts H36M joint 0 (pelvis)
  itself rather than trusting the checkpoint.

## Weights (not fetched, not committed)

- **File:** `weights/motionbert.pt` — git-ignored twice over (`weights/` and
  `*.pt` in `.gitignore`).
- **How to obtain:** `docs/motionbert-weights.md`.
