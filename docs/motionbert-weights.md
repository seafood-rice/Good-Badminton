# MotionBERT weights (3D pose lifting)

3D joint-angle scoring is **optional**. Without these weights the posture
pipeline runs exactly as before, scoring 2D angles only — nothing fails, no
warning is required. Install the weights only if you want the 3D metrics.

## Where the file goes

```
weights/motionbert.pt
```

`weights/` and `*.pt` are both in `.gitignore`. **Never commit the checkpoint**
(the 3D-pose files are 60-240 MB).

## How to obtain it

The checkpoints live on the upstream project's OneDrive, linked from
[MotionBERT](https://github.com/Walter0807/MotionBERT)'s README ("Model Zoo")
and `docs/inference.md`. Either of the two **3D Pose** rows works — our loader
reads the architecture out of the checkpoint itself, so no config file is needed
and you do not have to tell it which one you picked:

| Upstream entry | Config it was trained with | Reported accuracy | Notes |
|---|---|---|---|
| 3D Pose (H36M-SH, ft) | `configs/pose3d/MB_ft_h36m.yaml` | 37.2 mm MPJPE | Best accuracy; `dim_feat` 512. |
| 3D Pose (H36M-SH, scratch) | `configs/pose3d/MB_train_h36m.yaml` | 39.2 mm MPJPE | Also fine. |
| "MotionBERT-Lite" 3D-pose checkpoint (`FT_MB_lite_MB_ft_h36m_global_lite`), linked from upstream `docs/inference.md` | `configs/pose3d/MB_ft_h36m_global_lite.yaml` | — | Roughly 4x cheaper on CPU (`dim_feat` 256); the one upstream's own in-the-wild demo uses. Recommended if you are lifting on CPU. |

Steps:

1. Open the MotionBERT repo's README **Model Zoo** table (or `docs/inference.md`
   for the lite in-the-wild checkpoint) and follow the OneDrive link for the row
   you chose.
2. Download the checkpoint file from that folder — it is named `best_epoch.bin`.
3. Create the `weights/` directory at the repo root if it does not exist, and
   copy the file in **renamed** to `motionbert.pt`:

   ```powershell
   New-Item -ItemType Directory -Force weights
   Copy-Item <downloaded>\best_epoch.bin weights\motionbert.pt
   ```

   The `.pt` extension is just this project's naming convention (matching
   `weights/tracknet.pt`, `weights/bst-shuttleset.pt`); the file's contents are
   unchanged. Do not unzip or convert it.

Do **not** use the "MotionBERT"/"MotionBERT-Lite" *pretrain* rows, the action-
recognition rows, or the mesh row — those are not 3D-pose heads and will fail to
load (the loader is `strict=True`).

## Enabling it

Nothing is auto-discovered; pass the path explicitly (Task 9's CLI flags):

```bash
python main_posture.py --video-path <drill.mp4> --stroke-type high_clear \
  --lift-model weights/motionbert.pt --lift-device auto
```

The web UI passes the same flag through: `app.py` forwards its `lift_model`
form field as `--lift-model`.

- `--lift-model` — path to the checkpoint. Omit it and no lifting happens.
- `--lift-device` — `auto` (default: CUDA if available, else CPU), `cuda`, or `cpu`.

If the path does not exist, or the file is not a MotionBERT 3D-pose checkpoint,
the lifter prints one `MotionBERT load failed (...); 2D angles only.` line,
disables itself, and the run continues on 2D.

## Verifying the install

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_pose_lift_model.py -q -p no:cacheprovider
```

`test_real_model_lifts_to_3d` is skipped when `weights/motionbert.pt` is absent
and runs once it is present. The other tests in that file are weight-free and
always run.

## Licensing

MotionBERT is Apache-2.0; the vendored inference code and its license text are
in `third_party/motionbert/` (see that directory's `README.md` for the pinned
commit, the exact files, and the confirmed model contract). The **weights** are
distributed by the upstream authors under their own terms — check the upstream
repo before redistributing them or using them commercially.
