"""Learned per-rep form scorer (TorchScript over normalized keypoint windows)."""
import os

from .normalize import normalize_window


def to_display_score(raw):
    """Map a 1-7 coach-scale prediction to 0-100 (clamped, 1dp)."""
    pct = (float(raw) - 1.0) / 6.0 * 100.0
    return round(min(100.0, max(0.0, pct)), 1)


class QualityScorer:
    def __init__(self, model_path=None, stroke_type="high_clear", model=None):
        self.stroke_type = stroke_type
        self.model_path = model_path
        self._model = model
        if model is not None:
            self.available = True
        elif model_path and os.path.exists(model_path):
            self.available = True   # loaded lazily on first score()
        else:
            self.available = False

    def _load(self):
        if self._model is None and self.available:
            try:
                import torch
                self._model = torch.jit.load(self.model_path, map_location="cpu")
                self._model.eval()
            except Exception as e:
                print("Quality model unavailable (" + str(e) + ")")
                self.available = False
        return self._model

    def score(self, window_frames, dominant="right"):
        """0-100 AI form score for one rep window, or None."""
        if not self.available:
            return None
        model = self._load()
        if model is None:
            return None
        arr = normalize_window(window_frames, mirror=(dominant == "left"))
        if arr is None:
            return None
        try:
            import torch
            with torch.no_grad():
                out = model(torch.from_numpy(arr).unsqueeze(0))
            return to_display_score(out.reshape(-1)[0].item())
        except Exception as e:
            print("Quality scoring failed (" + str(e) + ")")
            return None
