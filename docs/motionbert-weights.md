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

That test only proves the plumbing works (shapes, device, no exceptions). It
says nothing about whether the numbers are anatomically right — see the next
section before trusting any 3D score.

## Validating the 3D output

### Read this first: the output is 2.5D, not true camera-space 3D

MotionBERT's 3D-pose head was trained on H36M's `joint3d_image` /
`joints_2.5d_image` target, in which channels 0/1 are the **perspective
projection** of each joint onto the image plane and channel 2 is a
commensurately-scaled depth (see
`third_party/motionbert/README.md` → "Confirmed model contract", and the
contract docstring in `badminton_analysis/detection/pose_lift.py`). Upstream's
own evaluation cannot score the raw output without first denormalizing it and
multiplying by a per-sample `2.5d_factor` that comes from dataset metadata, not
from the model.

Consequences for the four 3D metrics (`elbow_extension`, `knee_flexion`,
`trunk_rotation`, `hip_shoulder_separation`):

- The missing `2.5d_factor` is a uniform scale, and angles are scale-invariant,
  so **that** gap does not bias them.
- The perspective projection **does**. It is not a similarity transform, so the
  angles are not the true anatomical angles and are not fully view-invariant.
  Expect a systematic bias that varies with how far the player is from the
  camera and how far off the optical axis they are, of **unquantified
  magnitude** — and badminton framing (wide lens, player off-axis, deep court)
  is unlike the H36M training rigs.
- `reference_ranges_3d` was calibrated assuming *true* 3D angles. Until the
  checks below are done, read the 3D scores as a better-than-2D approximation
  and a relative/trend signal, **not** as quantitative ground truth. The
  coach report's angle-space badge and the `(2D: Y)` shadow value exist
  precisely so a coach can sanity-check a 3D number against its 2D counterpart.

### Checklist, in order

1. **Orientation sanity (cheap, do this first).** Lift one clip of an upright,
   camera-facing stance and assert the head sits above the pelvis on the
   vertical axis. Y points *down*, so the assertion is:

   ```python
   assert (kp3d[:, 10, 1] < kp3d[:, 0, 1]).all()   # H36M head(10) above pelvis(0)
   ```

   Failure here means the confirmed contract is wrong for your checkpoint —
   stop and re-check `joint_angles.VERTICAL_AXIS_3D` before anything else.
2. **Limb-length stability.** Over a rep, the distance between adjacent joints
   (e.g. shoulder→elbow) should be roughly constant. Large swings mean the lift
   is unstable on your footage — usually a 2D-detection or framing problem.
3. **2D/3D agreement on a near-frontal clip.** For a rep shot roughly
   perpendicular to the player, each 3D metric should land close to its 2D
   shadow (the report prints both). Big disagreement on a clip where 2D *should*
   be nearly correct is a red flag.
4. **Quantitative validation before trusting absolute scores.** Compare the four
   3D metrics against either published joint-angle ranges for the stroke or a
   small set of hand-labelled clips, across at least two shooting distances and
   two positions in the frame. If a consistent offset shows up, recalibrate
   `reference_ranges_3d` (or keep scoring 2D) rather than shipping the bias.
5. **Camera-geometry note.** Prefer a consistent camera position between the
   clips you validate on and the clips you score. Changing distance or lens
   changes the projective bias.

## Licensing

MotionBERT is Apache-2.0; the vendored inference code and its license text are
in `third_party/motionbert/` (see that directory's `README.md` for the pinned
commit, the exact files, and the confirmed model contract). The **weights** are
distributed by the upstream authors under their own terms — check the upstream
repo before redistributing them or using them commercially.
