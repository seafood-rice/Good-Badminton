# tests/test_doubles_multi_tracker.py
import numpy as np

from badminton_analysis.tracking.multi import Detection, MultiTracker, filter_tracklets

FPS = 30


def det(x, y):
    return Detection(court=(x, y), image=(x * 100, y * 100), keypoints=None, appearance=None)


def run(frames):
    t = MultiTracker(FPS)
    for f, dets in frames:
        t.step(f, dets)
    return t.finish()


def ids_by_start(tracklets):
    return sorted(tracklets, key=lambda t: (t["frames"][0], t["court"][0][0]))


def test_four_static_players_give_four_tracklets():
    frames = [(f, [det(1.5, 2), det(4.5, 2), det(1.5, 11), det(4.5, 11)]) for f in range(60)]
    tr = run(frames)
    assert len(tr) == 4
    assert all(len(t["frames"]) == 60 for t in tr)
    assert sorted(t["side"] for t in tr) == ["lower", "lower", "upper", "upper"]


def test_partners_moving_past_each_other_without_touching_keep_ids():
    # Two lower-side partners run left/right past each other 1.5 m apart in y.
    frames = []
    for f in range(90):
        a = det(0.5 + 5.0 * f / 89, 8.5)
        b = det(5.5 - 5.0 * f / 89, 10.0)
        frames.append((f, [a, b]))
    tr = run(frames)
    assert len(tr) == 2
    for t in tr:
        ys = {round(c[1], 1) for c in t["court"]}
        assert len(ys) == 1          # each tracklet stays on its own y line: no swap


def test_occlusion_crossing_cuts_tracklets():
    # Partners converge to the same spot; one disappears (occluded) while close.
    frames = []
    for f in range(30):
        frames.append((f, [det(1.0 + f * 0.05, 9.0), det(4.0 - f * 0.05, 9.0)]))
    for f in range(30, 40):
        frames.append((f, [det(2.5, 9.0)]))            # only one visible, both within 0.6 m
    for f in range(40, 70):
        frames.append((f, [det(2.5 - (f - 40) * 0.05, 9.0), det(2.5 + (f - 40) * 0.05, 9.0)]))
    tr = run(frames)
    # Nothing may span the ambiguous stretch: every tracklet either ends by
    # frame 30 or starts at/after it.
    for t in tr:
        assert t["frames"][-1] <= 30 or t["frames"][0] >= 30


def test_gap_longer_than_half_second_ends_track():
    frames = [(f, [det(3, 10)]) for f in range(20)] + [(f, [det(3, 10)]) for f in range(40, 60)]
    tr = run(frames)
    assert len(tr) == 2


def test_regular_stride_does_not_end_tracks():
    frames = [(f, [det(3, 10)]) for f in range(0, 90, 3)]   # fast mode stride 3
    tr = run(frames)
    assert len(tr) == 1


def test_net_crossing_splits_tracklet():
    frames = [(f, [det(3, 5.0 + f * 0.1)]) for f in range(40)]   # walks from y=5 to y=8.9
    tr = run(frames)
    assert len(tr) == 2
    assert {t["side"] for t in tr} == {"upper", "lower"}
    for t in tr:
        ys = [c[1] for c in t["court"]]
        assert all(y < 6.7 for y in ys) or all(y >= 6.7 for y in ys)


def test_filter_drops_short_and_off_court():
    frames = [(f, [det(3, 10)]) for f in range(60)]
    frames += [(f, [det(3, 10), det(-2.0, 10)]) for f in range(60, 120)]   # bystander off court
    frames += [(f, [det(3, 10), det(1, 2)]) for f in range(120, 130)]      # 10 frames only
    kept = filter_tracklets(run(frames), FPS)
    assert len(kept) == 1
    assert kept[0]["court"][0] == [3, 10]


def test_appearance_samples_are_kept():
    sig = {"torso": np.ones(64) / 64, "legs": None, "height": 1.3, "embed": None}
    t = MultiTracker(FPS)
    for f in range(40):
        t.step(f, [Detection((3, 10), (300, 1000), None, sig if f % 5 == 0 else None)])
    (tr,) = t.finish()
    assert len(tr["appearance"]) == 8
