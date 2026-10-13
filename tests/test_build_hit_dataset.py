# tests/test_build_hit_dataset.py
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_hit_dataset as bd  # noqa: E402

CORNERS = [[407.6, 308.6], [867.0, 307.4], [973.0, 669.4], [307.2, 671.2]]


def test_label_to_clip_frame_applies_offset_and_one_based_frames():
    # label 100.0 s, video = label + 2.0 s, clip starts at 101.0 s, 60 fps -> 1.0 s in -> frame 61
    assert bd.label_to_clip_frame(100.0, 2.0, 101.0, 60.0) == 61
    assert bd.label_to_clip_frame(100.0, 0.0, 100.0, 30.0) == 1


def test_court_annotation_text_has_three_lines_and_net_row():
    text = bd.court_annotation_text(CORNERS)
    lines = text.strip().splitlines()
    assert lines[0].startswith("corners=[[408, 309]") and lines[1].startswith("roi_corners=")
    mid = int(lines[2].split("=")[1])
    assert 308 < mid < 671


def test_encode_hits_drops_out_of_clip_and_maps_labels():
    hits = [{"t": 10.0, "side": "lower", "stroke7": "serve", "type_raw": "發短球"},
            {"t": 11.0, "side": "upper", "stroke7": None, "type_raw": "未知球種"},
            {"t": 99.0, "side": "lower", "stroke7": "net", "type_raw": "放小球"}]
    enc = bd.encode_hits(hits, offset_sec=0.0, clip_start_sec=9.0, fps=30.0, n_frames=90)
    assert enc["hit_frames"].tolist() == [31, 61]
    assert enc["hitter"].tolist() == [0, 1]
    assert enc["stroke7"].tolist() == [1, -1]
    assert enc["stroke_raw"].tolist() == ["發短球", "未知球種"]


def test_encode_hits_label_frame_is_relative_to_span_start():
    # clip starts at span_start + offset, so a label at t lands at round((t - span_start) * fps) + 1
    span_start, offset, fps = 100.0, 2.5, 30.0
    hits = [{"t": 101.0, "side": "lower", "stroke7": "serve", "type_raw": "x"},
            {"t": 103.3, "side": "upper", "stroke7": "net", "type_raw": "y"}]
    enc = bd.encode_hits(hits, offset_sec=offset, clip_start_sec=span_start + offset,
                         fps=fps, n_frames=300)
    assert enc["hit_frames"].tolist() == [round((t["t"] - span_start) * fps) + 1 for t in hits]


def test_encode_hits_with_clamped_clip_start_keeps_labels_aligned():
    hits = [{"t": 0.5, "side": "lower", "stroke7": "serve", "type_raw": "x"}]
    enc = bd.encode_hits(hits, offset_sec=0.0, clip_start_sec=0.0, fps=30.0, n_frames=90)
    assert enc["hit_frames"].tolist() == [16]


def test_clip_window_clamps_negative_start_and_shortens_duration():
    # rally starts 1.0 s before the video does: the clip starts at 0 and is 1.0 s shorter
    start, duration = bd.clip_window(span_start=-1.0, span_end=5.0, offset_sec=0.0)
    assert start == 0.0 and duration == 5.0
    start, duration = bd.clip_window(span_start=10.0, span_end=16.0, offset_sec=2.0)
    assert start == 12.0 and duration == 6.0
    # labels in a clamped clip are computed from the clamped start
    start, duration = bd.clip_window(span_start=0.5, span_end=6.0, offset_sec=-1.0)
    assert start == 0.0 and duration == 5.0
    hits = [{"t": 1.5, "side": "lower", "stroke7": "serve", "type_raw": "x"}]
    enc = bd.encode_hits(hits, -1.0, start, 30.0, 150)
    assert enc["hit_frames"].tolist() == [16]


def _tiny_clip(path):
    import cv2
    import numpy as np

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (64, 48))
    for _ in range(3):
        writer.write(np.zeros((48, 48 + 16, 3), np.uint8))
    writer.release()
    return str(path)


def _fake_system(source):
    from badminton_analysis.stroke import hit_signals as hs
    from tests.hit_fixtures import make_signals

    class FakeSystem:
        def __init__(self, clip, output_dir=None, **kw):
            self.output_dir, self.kw = output_dir, kw
            self._shuttle_source = "yolo"

        def process_video(self):
            self._shuttle_source = source
            hs.save_signals(os.path.join(self.output_dir, "hit_signals.npz"), make_signals(n=60))

    return FakeSystem


def test_run_pipeline_rejects_a_rally_without_tracknet(tmp_path, monkeypatch):
    import badminton_analysis.system as sysmod

    monkeypatch.setattr(sysmod, "BadmintonAnalysisSystem", _fake_system("yolo"))
    clip = _tiny_clip(tmp_path / "clip.mp4")
    with pytest.raises(RuntimeError, match="TrackNetV3 did not run"):
        bd.run_pipeline_on_clip(clip, str(tmp_path / "run"), CORNERS, "weight")


def test_run_pipeline_returns_signals_when_tracknet_ran(tmp_path, monkeypatch):
    import badminton_analysis.system as sysmod

    fake = _fake_system("tracknet")
    seen = {}
    monkeypatch.setattr(sysmod, "BadmintonAnalysisSystem",
                        lambda clip, **kw: seen.update(kw) or fake(clip, **kw))
    clip = _tiny_clip(tmp_path / "clip.mp4")
    sig = bd.run_pipeline_on_clip(clip, str(tmp_path / "run"), CORNERS, "weight")
    assert sig.n_frames == 60
    assert seen["dense_whole_video"] is True
