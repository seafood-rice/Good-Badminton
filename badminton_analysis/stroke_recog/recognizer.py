"""Rally-level stroke-labeling orchestration -- the Task 6 match-pipeline seam.

Ties together the already-merged Task 1-4 modules: ``hits.hit_events`` turns
a contact track into per-hit ``{"frame", "hitter"}`` records,
``inputs.build_inputs`` assembles the BST input tensors for the window around
each hit, ``bst_model.load_bst``/``predict`` run the vendored model, and
``classes.to_coarse`` collapses the 25 fine-grained logits into one of the 6
``COARSE`` buckets (or ``"uncertain"``). ``StrokeRecognizer`` is the single
class the match pipeline instantiates and calls per rally.

Model (and torch, which ``bst_model`` imports lazily) loading only happens on
first use, and any failure to load -- a missing checkpoint, a corrupt
``state_dict``, no ``weights_path`` at all -- leaves stroke recognition off
(``label_rally`` returns ``[]``) rather than raising, so this is purely an
optional enrichment that can never break the rest of the pipeline.
"""

from . import bst_model
from .classes import to_coarse
from .hits import hit_events
from .inputs import build_inputs

MIN_STROKE_CONF = 0.5


class StrokeRecognizer:
    """Lazy-loading BST stroke recognizer for one rally's contact track."""

    def __init__(self, weights_path):
        self.weights_path = weights_path
        self._model = None
        self._load_attempted = False

    def _get_model(self):
        """Load the BST model on first use; cache the outcome (incl. failure).

        Returns ``None`` (without ever calling ``bst_model.load_bst``) when
        ``weights_path`` is falsy, and also ``None`` if loading raises --
        both cases mean stroke recognition is off for this recognizer.
        """
        if not self._load_attempted:
            self._load_attempted = True
            if self.weights_path:
                try:
                    self._model = bst_model.load_bst(self.weights_path)
                except Exception as e:
                    print(f"BST stroke model unavailable ({e}); stroke labeling off.")
                    self._model = None
        return self._model

    def label_rally(self, track, frame_lookup, court_corners, video_wh):
        """Label every hit in ``track`` with a coarse stroke, frame-sorted.

        Returns ``[]`` when the model is unavailable (no/failed load) --
        stroke recognition is an optional enrichment, not a hard pipeline
        dependency. Otherwise returns one dict per hit from ``hit_events``:
        ``{"frame", "hitter", "stroke", "confidence", "uncertain"}``. Hits
        whose window has too little pose signal (``build_inputs`` returns
        ``None``) are labeled ``"uncertain"`` with ``confidence`` 0.0 rather
        than skipped, so every hit still gets a result.
        """
        model = self._get_model()
        if model is None:
            return []

        results = []
        for hit in hit_events(track, frame_lookup):
            frame = hit["frame"]
            hitter = hit["hitter"]
            built = build_inputs(frame, frame_lookup, court_corners, video_wh)
            if built is None:
                results.append({
                    "frame": frame,
                    "hitter": hitter,
                    "stroke": "uncertain",
                    "confidence": 0.0,
                    "uncertain": True,
                })
                continue

            logits = bst_model.predict(model, built["pose"], built["shuttle"], built["positions"])
            label, conf = to_coarse(logits, MIN_STROKE_CONF)
            results.append({
                "frame": frame,
                "hitter": hitter,
                "stroke": label,
                "confidence": float(conf),
                "uncertain": label == "uncertain",
            })

        results.sort(key=lambda r: r["frame"])
        return results
