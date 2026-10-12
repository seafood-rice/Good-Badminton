# tests/test_tracknet_reader.py
"""Vendored TrackNetV3 ``Video_IterableDataset`` window iteration.

Regression: in nonoverlap mode (``sliding_step == seq_len``) a video whose frame count is
an exact multiple of ``seq_len`` used to raise ``IndexError`` on ``frame_list[-1]`` after
the last full window (an upstream bug; see third_party/tracknet/CONTRACT.md).
"""
import cv2
import numpy as np

from badminton_analysis.shuttle_track.tracknet import vendored_imports

SEQ_LEN = 8


def _write_video(path, n_frames):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (64, 48))
    assert writer.isOpened()
    rng = np.random.RandomState(0)
    for i in range(n_frames):
        frame = rng.randint(0, 255, (48, 64, 3), dtype=np.uint8)
        frame[:, :, 0] = (i * 10) % 255
        writer.write(frame)
    writer.release()
    return str(path)


def _windows(video, sliding_step):
    with vendored_imports():
        from dataset import Video_IterableDataset

        ds = Video_IterableDataset(video, seq_len=SEQ_LEN, sliding_step=sliding_step,
                                   bg_mode="", max_sample_num=4)
        assert ds.video_len > 0
        return [(np.asarray(idx), frames) for idx, frames in ds]


def test_nonoverlap_frame_count_multiple_of_seq_len(tmp_path):
    video = _write_video(tmp_path / "v16.mp4", 16)
    windows = _windows(video, sliding_step=SEQ_LEN)
    assert len(windows) == 2
    covered = [int(f) for idx, _ in windows for f in idx[:, 1]]
    assert covered == list(range(16))
    assert all(frames.shape[0] == 3 * SEQ_LEN for _, frames in windows)


def test_nonoverlap_frame_count_not_multiple_pads_last_window(tmp_path):
    video = _write_video(tmp_path / "v17.mp4", 17)
    windows = _windows(video, sliding_step=SEQ_LEN)
    assert len(windows) == 3
    last_idx = windows[-1][0][:, 1]
    assert list(last_idx) == [16] * SEQ_LEN
    covered = sorted({int(f) for idx, _ in windows for f in idx[:, 1]})
    assert covered == list(range(17))


def test_weight_mode_sliding_step_one(tmp_path):
    video = _write_video(tmp_path / "v16w.mp4", 16)
    windows = _windows(video, sliding_step=1)
    # 9 full windows (starts 0..8) plus the upstream padded tail window (start 9, 7 real
    # frames); the project fix must leave this unchanged.
    assert len(windows) == 16 - SEQ_LEN + 2
    assert [int(idx[0, 1]) for idx, _ in windows] == list(range(10))
    assert list(windows[-1][0][:, 1]) == list(range(9, 16)) + [15]
