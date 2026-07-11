"""Maps the vendored BST model's 25 fine-grained classes to 6 coarse strokes.

``CLASS_NAMES`` is copied verbatim (index-aligned) from
``third_party/bst/CONTRACT.md`` ("CLASS_NAMES" section) -- that file is the
authoritative source; keep the two in sync.

The 25 classes are ``未知球種`` (unknown, index 0) followed by the same 12
merged stroke types for the ``Top_`` player (indices 1-12) and the ``Bottom_``
player (indices 13-24). Both player sides of a stroke collapse to the same
coarse label -- the hitter is tracked separately elsewhere, so player-side
does not affect the coarse stroke label. ``未知球種`` has no coarse bucket of
its own; it maps to the sentinel ``"uncertain"``, which is not one of the 6
``COARSE`` labels.
"""

import numpy as np

COARSE = ("serve", "clear", "smash", "drop", "drive", "net")

CLASS_NAMES = [
    "未知球種",
    "Top_放小球",
    "Top_擋小球",
    "Top_殺球",
    "Top_挑球",
    "Top_長球",
    "Top_平球",
    "Top_切球",
    "Top_推球",
    "Top_撲球",
    "Top_勾球",
    "Top_發短球",
    "Top_發長球",
    "Bottom_放小球",
    "Bottom_擋小球",
    "Bottom_殺球",
    "Bottom_挑球",
    "Bottom_長球",
    "Bottom_平球",
    "Bottom_切球",
    "Bottom_推球",
    "Bottom_撲球",
    "Bottom_勾球",
    "Bottom_發短球",
    "Bottom_發長球",
]

# Stroke (with Top_/Bottom_ prefix stripped) -> coarse bucket.
_STROKE_TO_COARSE = {
    "放小球": "net",  # net shot
    "擋小球": "net",  # block / return-net
    "殺球": "smash",
    "挑球": "clear",  # lob
    "長球": "clear",  # clear
    "平球": "drive",
    "切球": "drop",  # slice drop
    "推球": "drive",  # push
    "撲球": "net",  # rush / kill
    "勾球": "net",  # cross-court net
    "發短球": "serve",  # short serve
    "發長球": "serve",  # long serve
}

FINE_TO_COARSE = {"未知球種": "uncertain"}
for _name in CLASS_NAMES[1:]:
    _, _stroke = _name.split("_", 1)
    FINE_TO_COARSE[_name] = _STROKE_TO_COARSE[_stroke]
del _name, _stroke

_UNKNOWN_IDX = CLASS_NAMES.index("未知球種")

# Per-COARSE-bucket list of class indices, precomputed once at import time.
_BUCKET_INDICES = {
    label: [i for i, name in enumerate(CLASS_NAMES) if FINE_TO_COARSE[name] == label]
    for label in COARSE
}


def to_coarse(logits: np.ndarray, min_conf: float) -> tuple:
    """Collapse 25 fine-grained class logits into a coarse stroke label.

    Softmaxes ``logits`` over the 25 classes, sums the resulting
    probabilities into the 6 ``COARSE`` buckets (the unknown class
    contributes to no bucket), and takes the argmax bucket and its summed
    probability as the confidence.

    Returns ``("uncertain", best_prob)`` if that confidence is below
    ``min_conf``, or if the single highest-probability class overall is the
    unknown class (``未知球種``); otherwise returns ``(best_bucket,
    best_prob)``.
    """
    logits = np.asarray(logits, dtype=np.float64)
    shifted = logits - np.max(logits)
    exp = np.exp(shifted)
    probs = exp / np.sum(exp)

    bucket_probs = {label: probs[idx].sum() for label, idx in _BUCKET_INDICES.items()}
    best_bucket = max(bucket_probs, key=bucket_probs.get)
    best_prob = bucket_probs[best_bucket]

    if best_prob < min_conf or int(np.argmax(probs)) == _UNKNOWN_IDX:
        return "uncertain", best_prob
    return best_bucket, best_prob
