import pytest
from badminton_analysis.stroke.classifier import classify_stroke


def _window(contact_idx, racket_y_at_contact, nose_y, racket_dy_after,
            elbow_angle=150.0, hip_y=300.0, racket_start_y=None, n=36, fps=30.0):
    racket = []
    nose = []
    shoulder = []
    hip = []
    elbow = []
    centroid = []
    start_y = racket_start_y if racket_start_y is not None else racket_y_at_contact
    for i in range(n):
        if i < contact_idx and contact_idx > 0:
            ry = start_y + (racket_y_at_contact - start_y) * (i / contact_idx)
        elif i == contact_idx:
            ry = racket_y_at_contact
        else:
            ry = racket_y_at_contact + racket_dy_after * (i - contact_idx)
        racket.append((100.0, ry))
        nose.append((100.0, nose_y))
        shoulder.append((100.0, nose_y + 40))
        hip.append((100.0, hip_y))
        elbow.append(elbow_angle)
        centroid.append((100.0, 320.0))
    return {
        "contact_index": contact_idx, "racket_head": racket, "nose": nose,
        "shoulder": shoulder, "hip": hip, "elbow_angle": elbow,
        "centroid": centroid, "fps": fps,
    }


def test_classify_smash_overhead_fast_downward():
    # racket above head at contact (small y), strong downward motion after
    w = _window(contact_idx=18, racket_y_at_contact=20, nose_y=60,
                racket_dy_after=25, elbow_angle=160)
    stroke, conf = classify_stroke(w)
    assert stroke == "smash"
    assert 0.0 <= conf <= 1.0


def test_classify_drop_shot_overhead_gentle():
    # overhead but very little racket motion after contact
    w = _window(contact_idx=18, racket_y_at_contact=20, nose_y=60,
                racket_dy_after=1, elbow_angle=130)
    stroke, conf = classify_stroke(w)
    assert stroke == "drop_shot"


def test_classify_serve_racket_starts_low():
    # racket starts below hip and stays low (never overhead)
    w = _window(contact_idx=18, racket_y_at_contact=320, nose_y=60,
                racket_dy_after=2, elbow_angle=140, hip_y=300, racket_start_y=360)
    stroke, conf = classify_stroke(w)
    assert stroke == "serve"


def test_classify_high_clear_overhead_moderate_upward():
    w = _window(contact_idx=18, racket_y_at_contact=25, nose_y=60,
                racket_dy_after=-6, elbow_angle=150)
    stroke, conf = classify_stroke(w)
    assert stroke == "high_clear"
