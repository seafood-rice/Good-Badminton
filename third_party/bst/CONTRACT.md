# BST vendoring contract (ShuttleSet-25-merged)

This records the exact facts read from the vendored source of
[BST-Badminton-Stroke-type-Transformer](https://github.com/Va6lue/BST-Badminton-Stroke-type-Transformer)
(MIT License, see `LICENSE`) that downstream tasks (class mapping, input
assembly, recognizer) must build against. Nothing here is invented — every
value below was read out of the upstream repo's source files or derived from
the pretrained checkpoint's `state_dict` tensor shapes.

## Pinned model

- **Class:** `BST_CG_AP` ("BST-3" in the arXiv preprint v2; "Clean Gate" +
  "Aim Player" variant with Pose-Position Fusion).
- **Import path (vendored):** `model.bst.BST_CG_AP`
  (file: `third_party/bst/model/bst.py`, sys.path must include
  `third_party/bst`; see `badminton_analysis/stroke_recog/bst_model.py` which
  does this for you).
- **Upstream source:** `stroke_classification/model/bst.py`, class `BST_CG_AP`
  (upstream also defines `BST_0`, `BST`, `BST_CG`, `BST_AP` — those training/
  ablation variants were stripped when vendoring; see "What was vendored"
  below).
- **This is the exact config used by upstream's own
  `stroke_classification/main_on_shuttleset/bst_main.py`** (`hyp.n_classes=25`,
  `hyp.seq_len=30`, `hyp.pose_style='JnB_bone'`, `model_name='BST_CG_AP'`,
  `depth_tem=2`, `depth_inter=1`) — i.e. the default/canonical
  `main_on_shuttleset` BST configuration, confirming the README's "seq_len=30"
  note for the ShuttleSet-merged (25-class) dataset.
- **Construction args used by `load_bst`:**
  `BST_CG_AP(in_dim=72, n_class=25, seq_len=30, depth_tem=2, depth_inter=1)`
  (all other constructor args — `d_model=100`, `d_head=128`, `n_head=6`,
  `drop_p=0.3`, `mlp_d_scale=4`, `tcn_kernel_size=5` — are the class defaults
  and were **not** overridden, matching the training script).

## Constants (authoritative for Tasks 2-4)

```
SEQ_LEN      = 30
N_CLASSES    = 25
N_PEOPLE     = 2
POSE_IN_DIM  = 72          # (17 COCO joints + 19 bones) * 2 (x, y); pose_style="JnB_bone"

POSE_SHAPE     = (SEQ_LEN, N_PEOPLE, POSE_IN_DIM)   # (30, 2, 72)
SHUTTLE_SHAPE  = (SEQ_LEN, 2)                       # (30, 2)
POS_SHAPE      = (SEQ_LEN, N_PEOPLE, 2)             # (30, 2, 2)
```

These are the shapes `bst_model.predict(model, pose, shuttle, positions)`
expects for its three array arguments (no leading batch dimension — batching
is added internally). `predict()` treats the whole window as valid
(`video_len = SEQ_LEN`); a real clip shorter than 30 frames must be zero-padded
up to 30 frames by the caller (Task 3), matching upstream's
`make_seq_len_same` behavior in
`stroke_classification/preparing_data/shuttleset_dataset.py`.

`POSE_IN_DIM` derivation (from upstream `bst_infer.py` / `bst_main.py`
`get_network_architecture`, `pose_style='JnB_bone'`):
`in_dim = (n_joints + n_bones * extra) * in_channels = (17 + 19 * 1) * 2 = 72`,
where `n_bones = len(get_bone_pairs())` from
`stroke_classification/preparing_data/shuttleset_dataset.py` (17 COCO
keypoints + 19 bone pairs; `extra=1` for pose_style `JnB_bone`, i.e. joints
concatenated with bone vectors, not interpolated midpoints).

All of the above is independently confirmed by the downloaded checkpoint's
`state_dict` tensor shapes (see "Weights" section): `tcn_pose.net.0.weight`
is `(100, 72, 5)` (in_channels=72), `embedding_cross` is `(1, 30, 100)`
(seq_len=30), `mlp_head.mlp.mlp.3.weight` is `(25, 400)` (n_class=25).

## CLASS_NAMES (ordered, index 0-24)

Read verbatim from upstream `get_merged_stroke_types()` in
`stroke_classification/preparing_data/shuttleset_dataset.py` (index order is
`['未知球種'] + ['Top_'+s for s in class_ls] + ['Bottom_'+s for s in class_ls]`,
i.e. "unknown" first, then the 12 merged stroke types for the Top player, then
the same 12 for the Bottom player):

```
 0  未知球種            (unknown / none)
 1  Top_放小球
 2  Top_擋小球
 3  Top_殺球
 4  Top_挑球
 5  Top_長球
 6  Top_平球
 7  Top_切球
 8  Top_推球
 9  Top_撲球
10  Top_勾球
11  Top_發短球
12  Top_發長球
13  Bottom_放小球
14  Bottom_擋小球
15  Bottom_殺球
16  Bottom_挑球
17  Bottom_長球
18  Bottom_平球
19  Bottom_切球
20  Bottom_推球
21  Bottom_撲球
22  Bottom_勾球
23  Bottom_發短球
24  Bottom_發長球
```

`model(...)` output index `i` (0-24) maps 1:1 to `CLASS_NAMES[i]` above — the
model's `argmax` output is a merged (player-side, stroke-type) label, not a
bare stroke type; Task 2 (class mapping) is expected to split the `Top_` /
`Bottom_` prefix from the underlying stroke type.

## Weights

- **File:** `weights/bst-shuttleset.pt` (git-ignored; `weights/` was already
  in `.gitignore` before this task).
- **Status: fetched.** Downloaded from the repo's published
  ["Weights trained on ShuttleSet"](https://drive.google.com/drive/folders/1D4172WZDJWPvpJdpaHDhy_cA-s8F-zR5?usp=sharing)
  Google Drive folder, file `bst_CG_AP_JnB_bone_merged.pt`
  (Drive file id `1PhnbUAyqq-sfsj0cI6sSIYHkQcRxV8qb`) — the entry matching
  model `BST_CG_AP`, pose_style `JnB_bone`, the merged (25-class) label set,
  no `train_partial` / `between_2_hits` / `3d` suffix (i.e. the standard,
  fully-trained, seq_len=30 run, serial 1).
- **To re-fetch:**
  1. Open the folder link above in a browser and locate
     `bst_CG_AP_JnB_bone_merged.pt`, or
  2. Headlessly:
     `curl -L "https://drive.google.com/uc?export=download&id=1PhnbUAyqq-sfsj0cI6sSIYHkQcRxV8qb" -o weights/bst-shuttleset.pt`
     (works for this file since it is small enough to skip Google's
     virus-scan interstitial page; for a larger file you may need `gdown` or
     the interactive browser flow instead).
- **Loaded via `weights_only=True`, `map_location='cpu'`** — it is a plain
  `OrderedDict` `state_dict` (not a full pickled model object), 94 tensors,
  no extra wrapper keys; `model.load_state_dict(state_dict, strict=True)`
  succeeds with zero missing/unexpected keys against the vendored
  `BST_CG_AP(in_dim=72, n_class=25, seq_len=30)`.

## Verifying "sane output" (in place of running upstream `bst_infer.py`)

Upstream's `stroke_classification/main_on_shuttleset/bst_infer.py` example
loads a pre-collated ShuttleSet `.npy` test-set sample via
`Dataset_npy_collated` + `DataLoader`. That collated ShuttleSet dataset is a
separate, multi-gigabyte Google-Drive download (not the model weights) and is
out of scope for this task — we did not fetch it. To still confirm the
vendored model + weights combination is wired correctly (not just "instead of
inventing it, we asserted a shape"), we instead did, directly against
`third_party/bst/model/bst.py` + `weights/bst-shuttleset.pt`:

1. Built `BST_CG_AP(in_dim=72, n_class=25, seq_len=30, depth_tem=2, depth_inter=1)`
   and called `load_state_dict(state_dict, strict=True)` — **0 missing, 0
   unexpected keys**.
2. Ran one forward pass with an all-zeros `(1, 30, 2, 72)` pose /
   `(1, 30, 2)` shuttle / `(1, 30, 2, 2)` pos / `video_len=[30]` batch:
   output shape `(1, 25)`, all finite, non-degenerate (distinct per-class
   logit values, e.g. class 0 logit `4.97` clearly highest — a plausible
   "no clear stroke" default for an all-zero input).
3. Ran a second forward pass with random (non-zero) pose/shuttle/position
   tensors: output shape `(1, 25)`, all finite, `argmax` well-defined.

This confirms the vendored architecture exactly matches the checkpoint's
tensor shapes and produces sane (finite, non-constant-across-classes) logits,
without requiring the multi-GB ShuttleSet npy dataset.

## What was vendored

- `LICENSE` — upstream repo's MIT license, verbatim.
- `model/bst.py` — upstream `stroke_classification/model/bst.py`, trimmed to
  only `MultiHeadCrossAttention`, `CrossTransformerLayer`, and `BST_CG_AP`
  (the `BST_0`/`BST`/`BST_CG`/`BST_AP` sibling classes and the `__main__`
  FLOPs-counting script were dropped — training/ablation-only, per the
  pinned-variant decision above).
- `model/tempose.py` — upstream `stroke_classification/model/tempose.py`,
  trimmed to only the building blocks `BST_CG_AP` depends on (`MLP`,
  `MLP_Head`, `FeedForward`, `MultiHeadAttention`, `TransformerLayer`,
  `TransformerEncoder`, `TCN`); the `TemPose_V`/`TemPose_PF`/`TemPose_SF`/
  `TemPose_TF` baseline models and `__main__` script were dropped (unused by
  BST, training-only).
- **Not vendored:** `preparing_data/shuttleset_dataset.py` (dataset/DataLoader
  training infra) — its two pure label/topology helpers
  (`get_merged_stroke_types()`, `get_bone_pairs()`) were read once to produce
  the `CLASS_NAMES` list and the `POSE_IN_DIM=72` derivation above, and are
  recorded here as literal values rather than vendored as importable code,
  per the task brief ("CONTRACT.md documents... the authoritative values for
  Tasks 2-4").

## New runtime dependency

`model/bst.py`'s `BST_CG_AP.init_weights()` calls
`positional_encodings.torch_encodings.PositionalEncoding1D` to seed the
learned positional-embedding parameters (their initial values are irrelevant
for inference since `load_state_dict` overwrites them, but the module must
still import and run without error to construct the model). Added
`positional-encodings==6.0.4` (MIT-licensed, `numpy`-only dependency) to
`requirements.txt`.
