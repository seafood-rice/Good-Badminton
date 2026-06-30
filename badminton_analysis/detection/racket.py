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

    def detect_racket_head(self, frame, roi_corners=None):
        if self.model is None:
            return None
        try:
            result = self.model(frame, conf=self.conf, device=self.device, verbose=False)[0]
        except TypeError:
            result = self.model(frame, conf=self.conf, verbose=False)[0]

        boxes = getattr(result, "boxes", None)
        if boxes is None or boxes.xywh.shape[0] < 1:
            return None

        xywh = boxes.xywh.detach().cpu().numpy()
        conf = boxes.conf.detach().cpu().numpy() if boxes.conf is not None else np.ones(len(xywh))

        best = None
        best_conf = -1.0
        for box, c in zip(xywh, conf):
            cx, cy = int(box[0]), int(box[1])
            if not self._point_in_roi((cx, cy), roi_corners):
                continue
            if c > best_conf:
                best_conf = float(c)
                best = (cx, cy)
        return best

    def infer_racket_head(self, keypoints, dominant="right"):
        """Kinematic fallback: infer racket head from elbow+wrist keypoints."""
        from ..analysis.joint_angles import infer_racket_head as _infer
        return _infer(keypoints, dominant=dominant)
