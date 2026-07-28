"""Racket detection via an (optional) YOLO model, with dependency injection for tests."""
import os

import numpy as np


class RacketDetector:
    def __init__(self, model=None, model_path=None, conf=0.25, device="auto"):
        self.conf = conf
        self.roi_padding_ratio = 0.08
        if device in (None, "auto"):
            self.device = self._auto_device()
        else:
            self.device = device

        if model is not None:
            self.model = model
        elif model_path and os.path.exists(model_path):
            from ultralytics import YOLO
            self.model = YOLO(model_path)
        else:
            self.model = None

    @staticmethod
    def _auto_device():
        try:
            import torch
            if torch.cuda.is_available():
                return 0
        except Exception:
            pass
        return "cpu"

    def _point_in_roi(self, point, roi_corners):
        if roi_corners is None:
            return True
        x1, y1 = roi_corners[0]
        x2, y2 = roi_corners[1]
        pad = int(max(x2 - x1, y2 - y1) * self.roi_padding_ratio)
        return (x1 - pad) <= point[0] <= (x2 + pad) and (y1 - pad) <= point[1] <= (y2 + pad)

    def _boxes_in_roi(self, frame, roi_corners=None):
        """All in-ROI (point, confidence) box centers this frame, unsorted, []
        when the model is absent or nothing was detected. Shared by
        detect_racket_head (single best) and detect_racket_heads (all of
        them, for assigning different detections to different players)."""
        if self.model is None:
            return []
        try:
            result = self.model(frame, conf=self.conf, device=self.device, verbose=False)[0]
        except TypeError:
            result = self.model(frame, conf=self.conf, verbose=False)[0]

        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xywh.shape[0] < 1:
            return []

        xywh = boxes.xywh.detach().cpu().numpy()
        conf = boxes.conf.detach().cpu().numpy() if boxes.conf is not None else np.ones(len(xywh))

        out = []
        for box, c in zip(xywh, conf):
            cx, cy = int(box[0]), int(box[1])
            if not self._point_in_roi((cx, cy), roi_corners):
                continue
            out.append(((cx, cy), float(c)))
        return out

    def detect_racket_head(self, frame, roi_corners=None):
        boxes = self._boxes_in_roi(frame, roi_corners)
        if not boxes:
            return None
        return max(boxes, key=lambda b: b[1])[0]

    def detect_racket_heads(self, frame, roi_corners=None):
        """All in-ROI racket-box centers this frame, confidence-descending.

        Unlike detect_racket_head (single highest-confidence box), this lets a
        caller nearest-match different detections to different tracked
        players (badminton_analysis.system._capture_analysis_frame's
        both-player capture). Returns [] when the model is absent or no boxes
        fall in the ROI.
        """
        boxes = self._boxes_in_roi(frame, roi_corners)
        boxes.sort(key=lambda b: b[1], reverse=True)
        return [pt for pt, _c in boxes]

    def infer_racket_head(self, keypoints, dominant="right"):
        """Kinematic fallback: infer racket head from elbow+wrist keypoints."""
        from ..analysis.joint_angles import infer_racket_head as _infer
        return _infer(keypoints, dominant=dominant)
