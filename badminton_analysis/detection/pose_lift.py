"""Optional MotionBERT 2D->3D pose lifting for the posture pipeline.

MotionBERT contract (confirmed in Task 10 by reading the vendored source at
``third_party/motionbert``, pinned upstream commit ``705d3a95``; see that
directory's ``README.md`` for provenance):

1. **Clip length is variable, not fixed.** ``DSTformer.forward`` slices its
   temporal embedding (``self.temp_embed[:, :F, :, :]``), so any clip length
   ``F <= maxlen`` runs as-is; ``F > maxlen`` raises. ``maxlen`` is 243 in all
   four upstream ``configs/pose3d/*.yaml`` and is read back per checkpoint from
   ``temp_embed.shape[1]``. Upstream ``infer_wild.py`` itself feeds the final
   short tail of a video straight through, so short clips are a sanctioned
   path. The adapter therefore runs ``M`` frames unchanged when ``M <= maxlen``
   (exact 1:1 input/output frame alignment, no interpolation blur) and only
   resamples ``M -> maxlen -> M`` when ``M > maxlen``.

2. **Input normalization.** ``_normalize_screen`` below is exactly the
   normalization MotionBERT trained on: upstream
   ``lib/data/datareader_h36m.DataReaderH36M.read_2d`` maps H36M 2D joints with
   ``joints / res_w * 2 - [1, res_h / res_w]``, the same VideoPose3D formula.
   On top of that, the adapter applies upstream's own in-the-wild
   preprocessing (``lib/data/dataset_wild.read_input`` default branch:
   ``crop_scale(motion, scale_range=[1, 1])``), which rescales the player's
   clip-wide bounding box into ``[-1, 1]``. That step is what keeps a player who
   fills a small part of a wide badminton frame inside the model's training
   scale distribution. ``crop_scale`` is invariant under uniform scale plus
   translation, so applying it after ``_normalize_screen`` gives the same
   result as applying it to raw pixels -- the two do not fight.

3. **Input channels: 3, not 2.** ``joints_embed`` is ``Linear(dim_in=3,
   dim_feat)`` -- the model wants ``(x, y, confidence)``. The public adapter
   contract stays ``(1, M, 17, 2) -> (1, M, 17, 3)``; the adapter appends a
   confidence channel of 1.0 internally (upstream does the same when a detector
   supplies no confidence: see ``read_2d``'s "No conf provided, fill with 1.").

4. **Output orientation: vertical axis is index 1 (Y), pointing DOWN.** The 3D
   supervision (``read_3d``) normalizes ``joint3d_image`` x/y with the same
   image-space formula as the 2D input and z by ``/ res_w * 2``, so output
   channels are ``(image_x, image_y, depth)``. ``infer_wild.py``'s ``--gt_2d``
   option overwrites ``predicted_3d_pos[..., :2] = batch_input[..., :2]``,
   which only makes sense if channels 0/1 already live in the input's 2D image
   space; and ``lib/utils/vismo.motion2video_3d`` plots ``-ys`` as its up axis.
   So ``joint_angles.VERTICAL_AXIS_3D = 1`` (set in Tasks 2/3/5) is CORRECT and
   was left unchanged. Vertical points down (head y < pelvis y). Nothing consumes
   that axis any more: its only consumer was a 3D ``trunk_rotation`` that
   projected onto the horizontal plane, dropped in the final-review pass (see
   ``joint_angles.METRICS_3D_CAPABLE``). The constant is kept as the recorded
   contract for the orientation check in ``docs/motionbert-weights.md``.

5. **Root-relative output.** Upstream's checkpoints differ (``rootrel: True``
   for ``MB_ft_h36m``, ``False`` for the ``_global`` variants), so the adapter
   subtracts the pelvis (H36M joint 0) itself. That makes the output
   root-relative for either checkpoint and is a no-op on angles anyway.

6. **IMPORTANT CAVEAT -- the output is 2.5D image space, not true camera-space
   3D.** This falls straight out of finding 4 and limits how far the 3D angle
   scores can be trusted quantitatively. The supervision target
   (``lib/data/datareader_h36m.DataReaderH36M.read_3d``) is H36M's
   ``joint3d_image`` / ``joints_2.5d_image`` representation:

       ``labels[..., :2] = labels[..., :2] / res_w * 2 - [1, res_h / res_w]``
       ``labels[..., 2:] = labels[..., 2:] / res_w * 2``

   so channels 0/1 are the **perspective projection** of the joints onto the
   image plane and channel 2 is a commensurately-scaled depth. That is only
   equal to the true metric 3D pose under a *scaled-orthographic* approximation
   of the camera. Corroboration: upstream's own evaluation (``train.py``'s
   ``evaluate``) cannot score the raw output directly -- it calls
   ``datareader.denormalize(...)``, then multiplies by a per-sample
   ``2.5d_factor`` read from dataset metadata (which the model does not predict)
   before computing MPJPE against ``joints_2.5d_image``.

   What this does and does not mean for the three 3D-scored metrics
   (``joint_angles.METRICS_3D_CAPABLE``):

   - The missing ``2.5d_factor`` is itself a **uniform scalar** multiply, and
     angles are scale-invariant, so that particular gap does *not* bias angles.
   - The projective representation *does*. A perspective projection is not a
     similarity transform, so angles measured in this space are not the true
     anatomical angles and are not fully view-invariant. The error grows with
     the subject's depth extent relative to camera distance and with the
     subject's offset from the principal point, and depends on focal length --
     i.e. it is a systematic, subject-distance- and frame-position-dependent
     bias of **unquantified magnitude**, and badminton camera geometry (wide
     lens, player far off-axis, deep court) differs substantially from the H36M
     training rigs.
   - Practical consequence: treat the 3D metrics as a better-than-2D
     approximation, not as ground truth. ``reference_ranges_3d`` was calibrated
     assuming true 3D angles, so absolute 3D scores need validating against
     literature or labelled clips before being read quantitatively. See
     ``docs/motionbert-weights.md`` ("Validating the 3D output") for the
     checklist. Fixing the representation (camera intrinsics + a proper
     perspective back-projection, or a metric-output lifter) is out of scope
     here and is not attempted.
"""
import os
import sys

import numpy as np


def coco2h36m(seq):
    """Convert (T,17,C) COCO-17 keypoints to (T,17,C) H36M-17 (MotionBERT order).

    Synthesizes the H36M joints COCO lacks (pelvis, spine, thorax, head) from
    COCO joints, following the standard MotionBERT/VideoPose3D convention.
    """
    x = np.asarray(seq, dtype=float)
    y = np.zeros_like(x)
    y[:, 0] = (x[:, 11] + x[:, 12]) * 0.5   # 0 pelvis = mid-hip
    y[:, 1] = x[:, 12]                        # 1 R hip
    y[:, 2] = x[:, 14]                        # 2 R knee
    y[:, 3] = x[:, 16]                        # 3 R ankle
    y[:, 4] = x[:, 11]                        # 4 L hip
    y[:, 5] = x[:, 13]                        # 5 L knee
    y[:, 6] = x[:, 15]                        # 6 L ankle
    y[:, 8] = (x[:, 5] + x[:, 6]) * 0.5       # 8 thorax = mid-shoulder
    y[:, 7] = (y[:, 0] + y[:, 8]) * 0.5       # 7 spine = mid(pelvis, thorax)
    y[:, 9] = x[:, 0]                         # 9 nose
    y[:, 10] = (x[:, 1] + x[:, 2]) * 0.5      # 10 head = mid-eye
    y[:, 11] = x[:, 5]                        # 11 L shoulder
    y[:, 12] = x[:, 7]                        # 12 L elbow
    y[:, 13] = x[:, 9]                        # 13 L wrist
    y[:, 14] = x[:, 6]                        # 14 R shoulder
    y[:, 15] = x[:, 8]                        # 15 R elbow
    y[:, 16] = x[:, 10]                       # 16 R wrist
    return y


def coco2h36m_valid(valid):
    """Propagate a (T,17) COCO-17 per-joint validity mask into H36M-17 order.

    Mirrors ``coco2h36m`` exactly: a *derived* H36M joint (pelvis, spine, thorax,
    head) is valid only when every COCO joint it is synthesized from is valid; a
    *direct* joint inherits its single source joint's validity. Returns a (T,17)
    bool array.
    """
    v = np.asarray(valid, dtype=bool)
    out = np.zeros(v.shape, dtype=bool)
    out[:, 0] = v[:, 11] & v[:, 12]           # 0 pelvis = mid-hip
    out[:, 1] = v[:, 12]                      # 1 R hip
    out[:, 2] = v[:, 14]                      # 2 R knee
    out[:, 3] = v[:, 16]                      # 3 R ankle
    out[:, 4] = v[:, 11]                      # 4 L hip
    out[:, 5] = v[:, 13]                      # 5 L knee
    out[:, 6] = v[:, 15]                      # 6 L ankle
    out[:, 8] = v[:, 5] & v[:, 6]             # 8 thorax = mid-shoulder
    out[:, 7] = out[:, 0] & out[:, 8]         # 7 spine = mid(pelvis, thorax)
    out[:, 9] = v[:, 0]                       # 9 nose
    out[:, 10] = v[:, 1] & v[:, 2]            # 10 head = mid-eye
    out[:, 11] = v[:, 5]                      # 11 L shoulder
    out[:, 12] = v[:, 7]                      # 12 L elbow
    out[:, 13] = v[:, 9]                      # 13 L wrist
    out[:, 14] = v[:, 6]                      # 14 R shoulder
    out[:, 15] = v[:, 8]                      # 15 R elbow
    out[:, 16] = v[:, 10]                     # 16 R wrist
    return out


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_repo_root_on_path():
    """Make ``third_party.motionbert`` importable however the app was launched."""
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)


# Hip indices in COCO order, for the posed-frame gate (matches quality/normalize.py).
_COCO_L_HIP, _COCO_R_HIP = 11, 12
POSE_LIFT_MIN_POSED = 8


def joint_validity(kps):
    """Per-joint "was actually detected" mask for COCO keypoints (..., 17, 2).

    Undetected joints carry the project's sentinel value, so a keypoint counts as
    detected only when both axes exceed 1 pixel -- the same predicate
    ``quality/normalize.py`` uses (``(seq[...,0] > 1.0) & (seq[...,1] > 1.0)``)
    and the same one the two-hip gate below applies. Returns a bool array shaped
    like the input minus its last axis.
    """
    arr = np.asarray(kps, dtype=float)
    return (arr[..., 0] > 1.0) & (arr[..., 1] > 1.0)


def _posed_with_frames(window_frames):
    """Return (kps, frames, valid) for the frames with two valid hips.

    ``kps`` is a (M,17,2) float array, ``frames`` the list of their M source
    frame numbers, and ``valid`` a (M,17) bool per-joint detected mask (see
    ``joint_validity``). The other 15 joints of a posed frame may still be
    undetected sentinels, which is exactly what ``valid`` records so the lifter
    can tell the model about them. Empty -> zero-length arrays and [].
    """
    kps = []
    frames = []
    for f in window_frames:
        kp = f.get("keypoints")
        if kp is None:
            continue
        kp = np.asarray(kp, dtype=float)
        detected = joint_validity(kp)
        if detected[_COCO_L_HIP] and detected[_COCO_R_HIP]:
            kps.append(kp)
            frames.append(int(f["frame"]))
    if not kps:
        return (np.zeros((0, 17, 2), dtype=float), [],
                np.zeros((0, 17), dtype=bool))
    stacked = np.stack(kps)
    return stacked, frames, joint_validity(stacked)


def _normalize_screen(kps, image_size):
    """Normalize pixel coords to roughly [-1,1] by width (VideoPose3D convention):
    x' = x / w * 2 - 1 ; y' = y / w * 2 - h / w. Preserves aspect ratio."""
    w, h = float(image_size[0]), float(image_size[1])
    if w <= 0:
        return kps
    out = kps.copy()
    out[..., 0] = out[..., 0] / w * 2.0 - 1.0
    out[..., 1] = out[..., 1] / w * 2.0 - h / w
    return out


class PoseLifter:
    """Optional MotionBERT 2D->3D lifter, structured like quality.scorer.QualityScorer.

    `model` (or the adapter `_load()` builds from `model_path`) is a callable
    taking a (1, M, 17, 2) float32 array of normalized H36M-17 2D keypoints and
    returning a (1, M, 17, 3) array of root-relative 3D joints.

    A model may additionally opt in to receiving per-joint validity by carrying a
    truthy ``accepts_conf`` attribute; it is then called as
    ``model(batch, conf)`` with ``conf`` a (M, 17) float array of 1.0/0.0 in
    H36M-17 order. The real adapter opts in (MotionBERT has a confidence input
    channel, and `crop_scale` needs it to exclude undetected sentinel joints from
    the clip bounding box); the 2-argument call is never made to a plain callable,
    so the injected stub contract is unchanged.
    """

    def __init__(self, model_path=None, device="auto", model=None):
        self.model_path = model_path
        self.device = device
        self._model = model
        if model is not None:
            self.available = True
        elif model_path and os.path.exists(model_path):
            self.available = True   # the real torch load happens lazily in _load()
        else:
            self.available = False

    def _resolve_device(self):
        if self.device == "cuda":
            return "cuda"
        if self.device == "cpu":
            return "cpu"
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _load(self):
        """Build (once) the callable adapter around the vendored MotionBERT.

        Returns a callable with the Task 4 contract -- ``(1, M, 17, 2)``
        normalized H36M-17 2D in, ``(1, M, 17, 3)`` root-relative 3D out -- or
        ``None`` (and ``available = False``) if the weights or torch are absent.
        """
        if self._model is not None:
            return self._model
        if not (self.model_path and os.path.exists(self.model_path)):
            self.available = False
            return None
        try:
            import torch
            _ensure_repo_root_on_path()
            from third_party.motionbert.infer import (
                load_model, model_dim_in, model_maxlen, prepare_clip,
            )
            device = self._resolve_device()
            net = load_model(self.model_path, device=device)   # already .eval()
            maxlen = model_maxlen(net)     # longest clip temp_embed supports (243)
            dim_in = model_dim_in(net)     # 3 -> (x, y, confidence)

            def _adapter(batch_2d, conf=None):
                arr = np.asarray(batch_2d, dtype=np.float32)
                clip = arr[0]                                     # (M,17,2)
                m = clip.shape[0]
                clip_conf = None
                if conf is not None:
                    clip_conf = np.asarray(conf, dtype=np.float32).reshape(clip.shape[:-1])
                if m > maxlen:
                    # Only long windows need resampling; see contract note 1.
                    src = np.linspace(0.0, m - 1.0, maxlen)
                    lo = np.floor(src).astype(int)
                    hi = np.minimum(lo + 1, m - 1)
                    frac = (src - lo)[:, None, None]
                    clip = clip[lo] * (1.0 - frac) + clip[hi] * frac
                    if clip_conf is not None:
                        # An interpolated frame is only trustworthy where BOTH
                        # source frames are: take the conservative minimum rather
                        # than blending validity into a meaningless fraction.
                        clip_conf = np.minimum(clip_conf[lo], clip_conf[hi])
                # Real per-joint validity, not all-ones: crop_scale builds the
                # clip bounding box from joints whose confidence is non-zero, so
                # this is what keeps an undetected sentinel keypoint near the
                # image origin from skewing the normalization.
                model_in = prepare_clip(clip, dim_in=dim_in,
                                        conf=clip_conf)           # (T,17,dim_in)
                with torch.no_grad():
                    out = net(torch.from_numpy(model_in[None, ...]).to(device))
                out = np.asarray(out.detach().cpu().numpy(), dtype=float)
                out = out - out[:, :, 0:1, :]                     # root-relative
                t_out = out.shape[1]
                if t_out != m:
                    src2 = np.linspace(0.0, t_out - 1.0, m)
                    lo2 = np.floor(src2).astype(int)
                    hi2 = np.minimum(lo2 + 1, t_out - 1)
                    frac2 = (src2 - lo2)[None, :, None, None]
                    out = out[:, lo2] * (1.0 - frac2) + out[:, hi2] * frac2
                return out                                        # (1,M,17,3)

            _adapter.accepts_conf = True   # see PoseLifter's class docstring
            self._model = _adapter
            return self._model
        except Exception as e:
            print("MotionBERT load failed (" + str(e) + "); 2D angles only.")
            self.available = False
            return None

    def lift(self, window_frames, image_size):
        if not self.available:
            return None
        model = self._load()
        if model is None:
            return None
        kps2d, frames, valid2d = _posed_with_frames(window_frames)
        if kps2d.shape[0] < POSE_LIFT_MIN_POSED:
            return None
        try:
            h36m = coco2h36m(kps2d)                      # (M,17,2)
            norm = _normalize_screen(h36m, image_size)   # (M,17,2)
            batch = norm[None, ...].astype(np.float32)   # (1,M,17,2)
            if getattr(model, "accepts_conf", False):
                conf = coco2h36m_valid(valid2d).astype(np.float32)   # (M,17)
                out = np.asarray(model(batch, conf), dtype=float)
            else:
                out = np.asarray(model(batch), dtype=float)   # (1,M,17,3)
            kp3d = out[0]
            if kp3d.shape != (kps2d.shape[0], 17, 3):
                return None
            return kp3d, frames
        except Exception as e:
            print("Pose lifting failed (" + str(e) + ")")
            return None
