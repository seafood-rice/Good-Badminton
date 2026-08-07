"""The contact radius must scale with depth, not be a fixed pixel count.

A fixed 80 px gate means 0.75 m at the far baseline and 0.13 m at the near one on
the DJI footage -- so it accepts non-contacts far away and rejects real contacts
close up. These tests pin both failure directions, and pin that the legacy
fixed-pixel behaviour is untouched when no scale is supplied.
"""
import pytest

from badminton_analysis.stroke.events import (CONTACT_M, detect_contacts,
                                              detect_contacts_multi)


class FakeScale:
    """Minimal px_per_m provider: 100 px/m at y=1000, 600 px/m at y=2000."""

    def px_per_m(self, y):
        return 100.0 + (float(y) - 1000.0) * 0.5


def _track(racket, shuttles, side="lower"):
    """Track whose shuttle reverses direction at index 2, i.e. a contact."""
    out = []
    for i, sh in enumerate(shuttles):
        row = {"frame": i, "shuttle": sh,
               "racket_head": racket, "racket_upper": None, "racket_lower": None}
        row["racket_" + side] = racket
        out.append(row)
    return out


# Shuttle approaches, then reverses at index 2 -> a large direction change
SHUTTLES = [(1000.0, 1000.0), (1010.0, 1000.0), (1020.0, 1000.0),
            (1010.0, 1000.0), (1000.0, 1000.0), (990.0, 1000.0)]


def test_far_contact_rejected_when_scaled_but_accepted_by_fixed_px():
    # 60 px at a far row (y=1000, 100 px/m) is 0.60 m -- beyond CONTACT_M, so a
    # scaled gate must NOT fire...
    far_racket = (1080.0, 1000.0)
    assert detect_contacts(_track(far_racket, SHUTTLES),
                           contact_m=CONTACT_M, scale=FakeScale()) == []
    # ...yet the legacy fixed 80 px gate accepts it. That is the defect.
    assert len(detect_contacts(_track(far_racket, SHUTTLES), contact_px=80.0)) == 1


def test_near_contact_accepted_when_scaled_but_missed_by_fixed_px():
    # At a near row (y=2000, 600 px/m) a genuine 0.15 m contact is 90 px, which the
    # fixed 80 px gate wrongly rejects.
    near = [(x, 2000.0) for x, _ in SHUTTLES]
    near_racket = (1020.0 + 90.0, 2000.0)
    assert len(detect_contacts(_track(near_racket, near),
                               contact_m=CONTACT_M, scale=FakeScale())) == 1
    assert detect_contacts(_track(near_racket, near), contact_px=80.0) == []


def test_default_behaviour_is_unchanged_when_scale_is_absent():
    track = _track((1020.0, 1000.0), SHUTTLES)
    assert detect_contacts(track) == detect_contacts(track, contact_px=80.0)


def test_multi_accepts_the_same_scaled_arguments():
    near = [(x, 2000.0) for x, _ in SHUTTLES]
    track = _track((1110.0, 2000.0), near, side="lower")
    got = detect_contacts_multi(track, contact_m=CONTACT_M, scale=FakeScale())
    assert len(got) == 1
    assert got[0]["hitter"] == "lower"


def test_multi_default_behaviour_is_unchanged():
    track = _track((1020.0, 1000.0), SHUTTLES)
    assert detect_contacts_multi(track) == detect_contacts_multi(track, contact_px=80.0)


def test_contact_m_without_scale_is_rejected():
    with pytest.raises(ValueError):
        detect_contacts(_track((1020.0, 1000.0), SHUTTLES), contact_m=0.3)


def test_multi_contact_m_without_scale_is_rejected():
    with pytest.raises(ValueError):
        detect_contacts_multi(_track((1020.0, 1000.0), SHUTTLES), contact_m=0.3)


def test_scale_uses_the_shuttle_row_not_a_constant():
    """Identical pixel separation: far must reject, near must accept."""
    sep = 80.0
    far = _track((1020.0 + sep, 1000.0), SHUTTLES)
    near_sh = [(x, 2000.0) for x, _ in SHUTTLES]
    near = _track((1020.0 + sep, 2000.0), near_sh)
    assert detect_contacts(far, contact_m=CONTACT_M, scale=FakeScale()) == []
    assert len(detect_contacts(near, contact_m=CONTACT_M, scale=FakeScale())) == 1


def test_multi_side_choice_still_uses_nearest_racket_under_scaling():
    """Scaling changes the gate, not the hitter-attribution rule."""
    near = [(x, 2000.0) for x, _ in SHUTTLES]
    track = []
    for i, sh in enumerate(near):
        track.append({"frame": i, "shuttle": sh,
                      "racket_lower": (1020.0 + 150.0, 2000.0),   # 150 px away
                      "racket_upper": (1020.0 + 40.0, 2000.0)})   # 40 px -- nearer
    got = detect_contacts_multi(track, contact_m=CONTACT_M, scale=FakeScale())
    assert len(got) == 1
    assert got[0]["hitter"] == "upper"


def test_real_court_scale_integrates_with_the_gate():
    """End-to-end with the Task 1 model built from the real recorded quad."""
    from badminton_analysis.court.scale import PerspectiveScale

    scale = PerspectiveScale.from_quad(
        [(1724, 1122), (2372, 1135), (3827, 2095), (145, 2005)])
    # Near baseline: 603.8 px/m, so CONTACT_M=0.30 allows ~181 px.
    near = [(x, 2050.0) for x, _ in SHUTTLES]
    assert len(detect_contacts(_track((1020.0 + 150.0, 2050.0), near),
                               contact_m=CONTACT_M, scale=scale)) == 1
    # Far baseline: 106.3 px/m, so 0.30 m is only ~32 px; 150 px must be rejected.
    far = [(x, 1128.5) for x, _ in SHUTTLES]
    assert detect_contacts(_track((1020.0 + 150.0, 1128.5), far),
                           contact_m=CONTACT_M, scale=scale) == []
