"""Rule-based stroke classification from a contact-centered window."""

STROKE_TYPES = ("high_clear", "smash", "drop_shot", "serve")


def _at(seq, idx):
    if idx < 0 or idx >= len(seq):
        return None
    return seq[idx]


def _racket_velocity_after(racket, contact_idx, span=3):
    """Mean per-frame vertical racket displacement just after contact (image px; + = downward)."""
    a = _at(racket, contact_idx)
    b = _at(racket, min(contact_idx + span, len(racket) - 1))
    if a is None or b is None:
        return None
    frames = max(1, min(contact_idx + span, len(racket) - 1) - contact_idx)
    return (b[1] - a[1]) / frames


def classify_stroke(window):
    """Return (stroke_type, confidence in [0,1])."""
    ci = window["contact_index"]
    racket = window["racket_head"]
    nose = window["nose"]
    hip = window["hip"]

    racket_c = _at(racket, ci)
    nose_c = _at(nose, ci)
    racket_start = next((r for r in racket if r is not None), None)
    hip_c = _at(hip, ci)

    # Insufficient data -> low-confidence default.
    if racket_c is None or nose_c is None:
        return ("high_clear", 0.2)

    overhead = racket_c[1] < nose_c[1]            # racket above head (smaller y)
    vy = _racket_velocity_after(racket, ci)        # + downward
    if vy is None:
        vy = 0.0

    starts_low = racket_start is not None and hip_c is not None and racket_start[1] > hip_c[1]

    scores = {s: 0.0 for s in STROKE_TYPES}

    # Serve: racket starts below the hip and never goes overhead.
    if starts_low and not overhead:
        scores["serve"] += 1.0
    if not overhead:
        scores["serve"] += 0.3

    if overhead:
        # Smash: fast downward racket travel after contact.
        if vy >= 12:
            scores["smash"] += 1.0
        elif vy >= 6:
            scores["smash"] += 0.5
        # Drop shot: almost no racket travel after contact (gentle).
        if abs(vy) <= 3:
            scores["drop_shot"] += 1.0
        # High clear: moderate travel, not a steep downward smash.
        if -10 <= vy < 6:
            scores["high_clear"] += 0.7
        scores["high_clear"] += 0.2  # overhead baseline

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    conf = (scores[best] / total) if total > 0 else 0.2
    return (best, round(float(conf), 3))
