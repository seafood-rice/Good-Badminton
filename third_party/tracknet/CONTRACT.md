# TrackNetV3 vendoring contract

This records the exact facts read from the vendored source of
[TrackNetV3](https://github.com/qaz812345/TrackNetV3) (MIT License, see
`LICENSE`) that downstream tasks (Task 2's `infer.py` extraction and later
consumers) must build against. Nothing here is invented — every value below
was read out of the pinned upstream repo's source files.

## Pinned upstream commit

- **Repo:** `https://github.com/qaz812345/TrackNetV3`
- **Commit:** `77c123ad4dd449b7d275f16cc43f316ba5b54042`

## What was vendored (verbatim, byte-for-byte)

- `model.py` -> `third_party/tracknet/model.py` — defines `TrackNet` and
  `InpaintNet`.
- `utils/__init__.py` -> `third_party/tracknet/utils/__init__.py` (empty
  package marker).
- `utils/general.py` -> `third_party/tracknet/utils/general.py` — constants
  (`HEIGHT`, `WIDTH`, `COOR_TH`, ...), `get_model`, `to_img`, `to_img_format`,
  `generate_frames`, `write_pred_csv`, `write_pred_video`, and training/
  data-prep helpers we do not call.
- `dataset.py` -> `third_party/tracknet/dataset.py` — `Shuttlecock_Trajectory_Dataset`,
  `Video_IterableDataset`.
- `LICENSE` -> `third_party/tracknet/LICENSE` — upstream repo's MIT license.

No line of these five files was reformatted, "cleaned up", or otherwise
changed — this is intentional third-party vendoring. Verified identical via
`diff` against the pinned clone at copy time.

**Not vendored** (inference path does not need them, and `test.py` imports
`pycocotools` at module load which is not an installed dependency — importing
it would break):
`test.py`, `predict.py`, `train.py`, `preprocess.py`, `error_analysis.py`,
`correct_label.py`, `utils/metric.py`, `utils/visualize.py`.

## `infer.py` (Task 2) is our extraction, not vendored verbatim

Task 2 will add `third_party/tracknet/infer.py` (or an equivalent module) as
**our own extraction** of:
- upstream `predict.py`'s `if __name__ == '__main__':` block (model loading +
  the non-overlap/overlap TrackNet inference loop + optional InpaintNet
  refinement pass + CSV/video writing), and
- three helper functions from `test.py` that `predict.py` imports at module
  level (`from test import predict_location, get_ensemble_weight,
  generate_inpaint_mask`), copied out of `test.py` directly into our module so
  we never import `test.py` itself (and therefore never trigger its
  `pycocotools` import):
  - `predict_location(heatmap)` — largest-contour bounding box from a binary
    heatmap.
  - `get_ensemble_weight(seq_len, eval_mode)` — temporal-ensemble weighting
    (`'average'` or `'weight'` mode).
  - `generate_inpaint_mask(pred_dict, th_h)` — marks TrackNet-predicted gaps
    that should be filled by InpaintNet.

Exact edits Task 2 must make relative to the upstream sources above (so the
extraction stays reviewable against upstream):
1. Drop the `argparse` CLI wrapper; expose a plain Python function/class API
   instead (callers pass `video_file`, checkpoint paths, etc. as arguments,
   not `sys.argv`).
2. Drop `.cuda()` calls / make device selectable (CPU-only is the project's
   pinned `torch==2.5.1+cpu` runtime; upstream hardcodes `.cuda()`).
3. Inline the three `test.py` helper functions listed above verbatim (no
   logic changes) instead of `from test import ...`, so `third_party/tracknet`
   never needs `test.py` (and thus never needs `pycocotools`).
4. Everything else (the ensemble-buffer bookkeeping, `predict()` coordinate
   decoding, CSV column order) carries over unchanged from `predict.py`.

## Pinned parameters (authoritative for Task 2+)

```
HEIGHT = 288    # model input/output height (utils/general.py)
WIDTH  = 512    # model input/output width  (utils/general.py)

TrackNet   seq_len = 8    # tracking window; README's TrackNet training command
                          # (`train.py --model_name TrackNet --seq_len 8 ...`)
                          # and train.py's own argparse default agree.
InpaintNet seq_len = 16   # inpainting window; README's InpaintNet training
                          # command (`train.py --model_name InpaintNet --seq_len 16 ...`).

bg_mode = 'concat'        # README's TrackNet training example; get_model('TrackNet', seq_len, 'concat')
                          # -> TrackNet(in_dim=(seq_len+1)*3, out_dim=seq_len)
```

## Output format: `Frame,Visibility,X,Y`

`utils.general.write_pred_csv` (non-inpaint-mask branch) writes columns in
this exact order: `Frame`, `Visibility`, `X`, `Y`. Semantics (from
`predict.py`'s `predict()` and `test.py`'s `generate_inpaint_mask`):

- `Frame` (int): 0-indexed frame number within the input video/clip.
- `Visibility` (int, 0 or 1): `0` = shuttle not detected/invisible in this
  frame (`X`/`Y` are `0, 0` in that case), `1` = visible.
- `X`, `Y` (int): shuttle center pixel coordinates in the **original video's
  pixel space** (upstream rescales model-space coordinates, which are in
  `HEIGHT`x`WIDTH` = 288x512 space, back to the source video's native
  resolution via `img_scaler = (w / WIDTH, h / HEIGHT)` before writing — see
  `predict()`'s `cx_pred, cy_pred` computation in `predict.py`).

## `torch.load(..., weights_only=False)` requirement

TrackNet/InpaintNet checkpoints (`TrackNet_best.pt` / `InpaintNet_best.pt`)
are saved as a dict with a `'model'` key (state_dict) and a `'param_dict'`
key (training config used to reconstruct `seq_len`/`bg_mode` via
`get_model(...)`), not a bare `state_dict`. Task 2's loading code must call
`torch.load(ckpt_path, map_location='cpu', weights_only=False)` — the default
`weights_only=True` (torch >= 2.6) will refuse to unpickle the
`param_dict`/config structure embedded in these checkpoints.

## Weights (not fetched, not committed by this task)

- **Files:** `weights/tracknet.pt`, `weights/inpaintnet.pt` (git-ignored;
  `weights/` is already excluded, matching the existing `third_party/bst`
  pattern).
- **To fetch:** Download `TrackNet_best.pt` and `InpaintNet_best.pt` from the
  repo's Google Drive (see upstream README's
  [checkpoints link](https://drive.google.com/file/d/1CfzE87a0f6LhBp0kniSl1-89zaLCZ8cA/view?usp=sharing)),
  place as `weights/tracknet.pt` and `weights/inpaintnet.pt`. Not committed.

## New runtime dependency

`utils/general.py` and `dataset.py` both do `import parse` at module level
(the pure-python [`parse`](https://pypi.org/project/parse/) string-parsing
library, used only by their training/data-prep helpers — e.g.
`convert_gt_to_coco_json`, `generate_data_frames` — which this vendoring never
calls, but the import must still resolve at module load time). Added `parse`
(no version pin; upstream itself does not pin it) to `requirements.txt`.

## `infer.py` edits vs upstream (Task 2)

`third_party/tracknet/infer.py` is our own extraction, not vendored
verbatim. It contains:
- Three helpers copied **verbatim** from upstream `test.py`:
  `get_ensemble_weight` (`test.py:25-50`), `predict_location`
  (`test.py:52-79`), `generate_inpaint_mask` (`test.py:223-258`) — copied
  directly into `infer.py` instead of `from test import ...`, so
  `third_party/tracknet` never imports `test.py` (and thus never triggers its
  `pycocotools` import).
- `predict(indices, y_pred=None, c_pred=None, img_scaler=(1, 1))` copied
  **verbatim** from upstream `predict.py:14-69`.
- `run_prediction(...)` — transcribed from upstream `predict.py`'s
  `if __name__ == '__main__':` block (`predict.py:86-306`), with these exact
  edits:
  1. Signature: `def run_prediction(video_file, tracknet_file,
     inpaintnet_file='', batch_size=16, eval_mode='weight',
     large_video=True, max_sample_num=1800, video_range=None,
     device=None):` — replaces the upstream `argparse` CLI.
  2. Device resolution added at the top of the function:
     `device = device or ('cuda' if torch.cuda.is_available() else 'cpu')`.
  3. Every `args.X` reference replaced with the corresponding parameter `X`.
     Deleted the `argparse`/CLI setup entirely, plus the
     `num_workers = args.batch_size if args.batch_size <= 16 else 16`
     computation, `video_name`, `out_csv_file`, `out_video_file`, and the
     `if not os.path.exists(args.save_dir): os.makedirs(args.save_dir)`
     block (no `save_dir` concept in `run_prediction` — callers own where
     results go).
  4. `torch.load(args.tracknet_file)` → `torch.load(tracknet_file,
     map_location=device, weights_only=False)`; same substitution for
     `inpaintnet_file`. Required because torch >= 2.6 defaults
     `weights_only=True`, which refuses to unpickle the `param_dict`
     structure embedded in these checkpoints (see the pre-existing
     "`torch.load(..., weights_only=False)` requirement" section above).
  5. `.cuda()` → `.to(device)` on both model constructions
     (`get_model('TrackNet', ...).cuda()` and
     `get_model('InpaintNet').cuda()`); `x.float().cuda()` →
     `x.float().to(device)` at both call sites (non-overlap and overlap
     TrackNet inference loops); `inpaintnet(coor_pred.cuda(),
     inpaint_mask.cuda())` → `inpaintnet(coor_pred.to(device),
     inpaint_mask.to(device))` at both call sites (non-overlap and overlap
     InpaintNet refinement loops).
  6. `num_workers=num_workers` → `num_workers=0` in every `DataLoader(...)`
     call (Windows-safe: avoids multiprocessing pickling of in-memory frame
     arrays across worker processes, which upstream's Linux-oriented default
     does not need to handle).
  7. Deleted the CSV/video-writing block (upstream `predict.py:304-311`,
     `write_pred_csv`/`write_pred_video` calls and the `--output_video`
     branch). `run_prediction` instead ends with `return inpaint_pred_dict
     if inpaintnet is not None else tracknet_pred_dict`.

  Everything else — the non-overlap/overlap dataset branching, the
  large/small-video branching, the temporal-ensemble buffer bookkeeping, and
  the two-pass TrackNet → InpaintNet refinement structure — carries over
  unchanged from `predict.py`'s `__main__` block (faithful mechanical
  extraction; no branches pruned).

Covered by `tests/test_tracknet_infer.py` (weight-free unit tests: `predict`
coordinate-scaling math, `get_ensemble_weight` symmetry/normalization,
`generate_inpaint_mask` gap-marking). `run_prediction`'s end-to-end path
(model loading + video decoding) requires real checkpoint weights and is
validated by a later task/controller, not by this test file.
