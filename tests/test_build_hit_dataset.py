# tests/test_build_hit_dataset.py
import sys
from pathlib import Path

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
