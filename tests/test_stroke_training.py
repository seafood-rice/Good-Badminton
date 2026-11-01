import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import train_stroke_classifier as tsc  # noqa: E402

from badminton_analysis.stroke import stroke_labels as sl  # noqa: E402
from badminton_analysis.stroke import stroke_model as smod  # noqa: E402
from badminton_analysis.stroke.hit_signals import save_signals  # noqa: E402
from tests.test_stroke_windows import _sig  # noqa: E402


def _npz(path, match, raw):
    s = _sig()
    np.savez_compressed(path, fps=s.fps, first_frame=s.first_frame, shuttle=s.shuttle,
                        keypoints=s.keypoints, court_corners=s.court_corners,
                        image_size=np.array(s.image_size), match=match, set=1, rally=1,
                        hit_frames=np.array([60, 120, 180]), hitter=np.array([0, 1, -1]),
                        stroke7=np.array([0, 0, 0]), stroke_raw=np.array(raw))


def test_load_shuttleset_maps_raw_and_drops_unusable(tmp_path):
    d = tmp_path / "ds" / "M1"
    d.mkdir(parents=True)
    _npz(d / "s1_r1.npz", "M1", ["殺球", "未知球種", "推球"])
    (d / "s1_r2.part.npz").write_bytes(b"")
    rallies = tsc.load_shuttleset(str(tmp_path / "ds"))
    assert len(rallies) == 1 and rallies[0].match == "M1"
    # 未知球種 is dropped; hitter -1 is dropped
    assert rallies[0].hits == [{"frame": 60, "hitter": "lower", "stroke": "smash"}]


def test_load_dji_train_split_only(tmp_path):
    res = tmp_path / "res"
    for name in ("A", "B"):
        (res / name).mkdir(parents=True)
        save_signals(res / name / "hit_signals.npz", _sig())
    text = sl.write_sheet([
        {"name": "A", "split": "train", "fps": 30.0, "clip": "A.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"}]},
        {"name": "B", "split": "test", "fps": 30.0, "clip": "B.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"}]}]).replace("|  | 61 |", "| lift | 61 |")
    sheet = tmp_path / "LABELS.md"
    sheet.write_text(text, encoding="utf-8")
    rallies = tsc.load_dji(str(sheet), str(res))
    assert [r.match for r in rallies] == ["dji:A"]
    assert rallies[0].hits == [{"frame": 61, "hitter": "lower", "stroke": "lift"}]
    with pytest.raises(ValueError, match="test"):
        tsc.load_dji(str(sheet), str(res), split="test")


def test_train_smoke_writes_loadable_model(tmp_path):
    rallies = [tsc.Rally(_sig(), [{"frame": f, "hitter": "lower", "stroke": s}
                                  for f, s in ((60, "smash"), (120, "lift"), (180, "net_shot"))],
                         f"M{k}") for k in range(3)]
    model, meta = tsc.train(rallies[:2], rallies[2:], epochs=2, hidden=8, levels=1, seed=0,
                            device="cpu", log=lambda *_: None)
    path = tmp_path / "s.pt"
    smod.save_stroke_model(model, meta, path)
    _, loaded = smod.load_stroke_model(path)
    assert 0.0 <= loaded["unknown_threshold"] < 1.0
    assert loaded["train_counts"]["smash"] == 2 and loaded["val_matches"] == ["M2"]
    assert "val_metrics" in loaded and loaded["validated_on_owner_footage"] is False


def test_load_dji_reports_skipped_rows(tmp_path, capsys):
    res = tmp_path / "res"
    (res / "A").mkdir(parents=True)
    save_signals(res / "A" / "hit_signals.npz", _sig())
    text = sl.write_sheet([
        {"name": "A", "split": "train", "fps": 30.0, "clip": "A.mp4",
         "hits": [{"frame": 61, "time_sec": 2.0, "hitter": "lower"},
                  {"frame": 91, "time_sec": 3.0, "hitter": "lower"}]}])
    text = text.replace("|  | 61 |", "| lift | 61 |").replace("|  | 91 |", "| lift | 91 |")
    text = text.replace("| 3.0000 | near |", "| 3.0000 |  |")
    sheet = tmp_path / "LABELS.md"
    sheet.write_text(text, encoding="utf-8")
    rallies = tsc.load_dji(str(sheet), str(res))
    assert len(rallies[0].hits) == 1
    assert "A: skipped 1 labelled row(s) without player or frame" in capsys.readouterr().out


def _owner_fixture(tmp_path):
    from badminton_analysis.stroke import owner_labels as ol
    res, lab = tmp_path / "res", tmp_path / "lab"
    for stem in ("A", "B", "T"):
        d = res / stem
        d.mkdir(parents=True)
        (d / "hits.json").write_text(json.dumps(
            {"fps": 30.0, "hits": [{"frame": 60, "hitter": "lower"}, {"frame": 120, "hitter": "upper"}]}),
            encoding="utf-8")
        save_signals(d / "hit_signals.npz", _sig())
    rows = [{"time_sec": 2.0, "hitter": "lower", "stroke": "smash", "kind": "label"},
            {"time_sec": 4.0, "hitter": "upper", "stroke": None, "kind": "not_hit"},
            {"time_sec": 5.0, "hitter": "lower", "stroke": "lift", "kind": "added"},
            {"time_sec": 6.0, "hitter": "lower", "stroke": None, "kind": "unsure"},
            {"time_sec": 15.0, "hitter": "lower", "stroke": "lift", "kind": "added"}]
    for stem, split in (("A", "train"), ("B", "train"), ("T", "test")):
        ol.save(lab / f"{stem}.json", {"schema": 1, "revision": 1, "split": split, "labels": rows})
    return res, lab


def test_load_owner_train_split_only_and_counts_outside_frames(tmp_path, capsys):
    res, lab = _owner_fixture(tmp_path)
    rallies = tsc.load_owner(str(lab), str(res))
    assert [r.match for r in rallies] == ["owner:A", "owner:B"]
    # label at 60, added at 150 and 450 (outside the 300-frame npz); not_hit and unsure excluded
    assert [(h["frame"], h["stroke"]) for h in rallies[0].hits] == [(60, "smash"), (150, "lift"), (450, "lift")]
    assert "2 hit(s) outside the saved signals" in capsys.readouterr().out
    assert tsc.load_owner(str(tmp_path / "nolabels"), str(res)) == []


def test_main_no_labels_no_dataset_is_a_clear_exit(tmp_path):
    res, lab = _owner_fixture(tmp_path)
    with pytest.raises(SystemExit) as e:
        tsc.main(["--stroke-labels", str(tmp_path / "nolabels"), "--results-dir", str(res),
                  "--out", str(tmp_path / "o.pt")])
    assert "need training and validation data" in str(e.value)


def test_main_owner_calib_accepts_bare_stem_and_holds_it_out(tmp_path, monkeypatch):
    res, lab = _owner_fixture(tmp_path)
    seen = {}

    def fake_train(train_r, val_r, **kw):
        seen["train"], seen["val"] = [r.match for r in train_r], [r.match for r in val_r]
        return "model", {"unknown_threshold": 0.5, "val_metrics": {"accuracy": 1, "unknown_rate": 0}}

    monkeypatch.setattr(tsc, "train", fake_train)
    monkeypatch.setattr(tsc.smod, "save_stroke_model", lambda *a: None)
    assert tsc.main(["--stroke-labels", str(lab), "--results-dir", str(res), "--calib-video", "A",
                     "--out", str(tmp_path / "o.pt")]) == 0
    assert seen == {"train": ["owner:B"], "val": ["owner:A"]}
    assert tsc.main(["--stroke-labels", str(lab), "--results-dir", str(res),
                     "--calib-video", "owner:B", "--out", str(tmp_path / "o.pt")]) == 0
    assert seen == {"train": ["owner:A"], "val": ["owner:B"]}


# ---- per-hit attributes (spec 2026-11-02) ----

_HOM = ("id,video,homography_matrix,upleft_x,upright_x,downleft_x,downright_x,"
        "upleft_y,upright_y,downleft_y,downright_y\n"
        '1,M,"[[1,0,0],[0,1,0],[0,0,1]]",407.6,867.0,307.2,973.0,308.6,307.4,671.2,669.4\n')
_SET = ("rally,ball_round,frame_num,player,type,aroundhead,backhand,hit_height,player_location_y\n"
        "1,1.0,600.0,B,發短球,,1.0,2.0,532.0\n"
        "1,2.0,660.0,A,挑球,1.0,,1.0,373.0\n")


def _joined_fixture(tmp_path, frames=(31, 91), raw=("發短球", "挑球"), with_arrays=False):
    root = tmp_path / "shuttleset"
    (root / "labels" / "M").mkdir(parents=True)
    (root / "alignment").mkdir()
    (root / "labels" / "homography.csv").write_text(_HOM, encoding="utf-8")
    (root / "labels" / "M" / "set1.csv").write_text(_SET, encoding="utf-8")
    (root / "alignment" / "M.json").write_text(json.dumps({"offset": 0.0, "accepted": True}),
                                               encoding="utf-8")
    d = tmp_path / "ds" / "M"
    d.mkdir(parents=True)
    s = _sig()
    extra = {"backhand": np.array([0, 0]), "around_head": np.array([0, 0]),
             "above_net": np.array([-1, -1])} if with_arrays else {}
    np.savez_compressed(d / "s1_r1.npz", fps=s.fps, first_frame=s.first_frame, shuttle=s.shuttle,
                        keypoints=s.keypoints, court_corners=s.court_corners,
                        image_size=np.array(s.image_size), match="M", set=1, rally=1, kind="rally",
                        hit_frames=np.array(frames), hitter=np.array([0, 1]), stroke7=np.array([1, 5]),
                        stroke_raw=np.array(raw), **extra)
    return str(tmp_path / "ds"), str(root)


def test_load_shuttleset_joins_attributes_on_exact_match(tmp_path):
    ds, root = _joined_fixture(tmp_path)
    rallies = tsc.load_shuttleset(ds, root)
    assert [h["attrs"] for h in rallies[0].hits] == [
        {"backhand": 1, "around_head": 0, "above_net": 0},
        {"backhand": 0, "around_head": 1, "above_net": 1}]


def test_load_shuttleset_mismatch_trains_type_head_only(tmp_path, capsys):
    ds, root = _joined_fixture(tmp_path, frames=(31, 92))
    rallies = tsc.load_shuttleset(ds, root)
    assert len(rallies[0].hits) == 2 and all("attrs" not in h for h in rallies[0].hits)
    assert "attributes not joined for 1 of 1" in capsys.readouterr().out
    ds, root = _joined_fixture(tmp_path / "b", raw=("發短球", "殺球"))      # stroke_raw differs
    assert all("attrs" not in h for h in tsc.load_shuttleset(ds, root)[0].hits)
    ds, _ = _joined_fixture(tmp_path / "c")
    assert all("attrs" not in h for h in tsc.load_shuttleset(ds)[0].hits)    # no labels_root


def test_load_shuttleset_missing_homography_row_trains_type_head_only(tmp_path, capsys):
    ds, root = _joined_fixture(tmp_path)
    (tmp_path / "shuttleset" / "labels" / "homography.csv").write_text(
        _HOM.splitlines()[0] + "\n", encoding="utf-8")                  # alignment present, no row for M
    rallies = tsc.load_shuttleset(ds, root)
    assert len(rallies) == 1 and all("attrs" not in h for h in rallies[0].hits)
    assert "attributes not joined for 1 of 1" in capsys.readouterr().out


@pytest.mark.parametrize("breakage", ["bad_offset", "no_offset", "bad_json"])
def test_load_shuttleset_broken_alignment_does_not_stop_training(tmp_path, capsys, breakage):
    ds, root = _joined_fixture(tmp_path)
    body = {"bad_offset": json.dumps({"offset": "x"}), "no_offset": "{}", "bad_json": "{"}[breakage]
    (tmp_path / "shuttleset" / "alignment" / "M.json").write_text(body, encoding="utf-8")
    rallies = tsc.load_shuttleset(ds, root)
    assert len(rallies) == 1 and all("attrs" not in h for h in rallies[0].hits)


def test_gap_files_do_not_count_in_the_joined_line(tmp_path, capsys):
    ds, root = _joined_fixture(tmp_path, frames=(31, 92))
    s = _sig()
    np.savez_compressed(os.path.join(ds, "M", "g1.npz"), fps=s.fps, first_frame=s.first_frame,
                        shuttle=s.shuttle, keypoints=s.keypoints, court_corners=s.court_corners,
                        image_size=np.array(s.image_size), match="M", set=1, rally=2, kind="gap",
                        hit_frames=np.array([], np.int64), hitter=np.array([], np.int64),
                        stroke7=np.array([], np.int64), stroke_raw=np.array([], dtype=str))
    tsc.load_shuttleset(ds, root)
    assert "attributes not joined for 1 of 1" in capsys.readouterr().out


def test_load_shuttleset_prefers_arrays_stored_in_the_npz(tmp_path):
    ds, root = _joined_fixture(tmp_path, with_arrays=True)
    hits = tsc.load_shuttleset(ds, root)[0].hits
    assert hits[0]["attrs"] == {"backhand": 0, "around_head": 0}     # -1 is left out


def _attr_rally(n, match, backhand_every=2):
    hits = [{"frame": 60 + 3 * i, "hitter": "lower", "stroke": "smash",
             "attrs": {"backhand": i % backhand_every, "above_net": -1}} for i in range(n)]
    for h in hits:
        h["attrs"] = {k: v for k, v in h["attrs"].items() if v >= 0}
    return tsc.Rally(_sig(), hits, match)


def test_windows_carry_attribute_targets_aligned_and_unknown_is_minus_one():
    X, y, A = tsc._windows(_attr_rally(4, "M"))
    assert A.shape == (4, 3) and A[:, 0].tolist() == [0, 1, 0, 1] and (A[:, 1:] == -1).all()
    X, y, A = tsc._windows(_attr_rally(4, "M"), np.random.default_rng(0))
    assert len(X) == len(y) == len(A) == 4 and A[:, 0].tolist() == [0, 1, 0, 1]


def test_masked_bce_ignores_unknown_targets():
    import torch
    logits = torch.tensor([[2.0, -3.0], [0.5, 4.0], [100.0, 100.0]])
    targets = torch.tensor([[1, -1], [0, 0], [-1, -1]])
    loss = tsc._masked_bce(logits, targets, torch.ones(2))
    f = torch.nn.functional.binary_cross_entropy_with_logits
    col0 = f(torch.tensor([2.0, 0.5]), torch.tensor([1.0, 0.0]))
    col1 = f(torch.tensor([4.0]), torch.tensor([0.0]))
    assert float(loss) == pytest.approx(float((col0 + col1) / 2), abs=1e-6)
    assert float(tsc._masked_bce(logits, torch.full((3, 2), -1), torch.ones(2))) == 0.0


def test_attribute_trained_only_with_enough_labels_and_both_classes(monkeypatch):
    A = np.array([[1, -1, 0]] * 5 + [[0, -1, 0]] * 5, np.int64)
    monkeypatch.setattr(tsc, "MIN_ATTR_LABELS", 10)
    assert tsc._trained_attributes(A) == ["backhand"]      # above_net is all 0; around_head unknown
    monkeypatch.setattr(tsc, "MIN_ATTR_LABELS", 11)
    assert tsc._trained_attributes(A) == []


def test_train_with_attribute_head_reports_metrics_and_skips_when_few(tmp_path, monkeypatch):
    rallies = [_attr_rally(6, f"M{k}") for k in range(3)]
    monkeypatch.setattr(tsc, "MIN_ATTR_LABELS", 5)
    model, meta = tsc.train(rallies[:2], rallies[2:], epochs=2, hidden=8, levels=1, seed=0,
                            device="cpu", log=lambda *_: None)
    assert meta["attributes"] == ["backhand"] and model.n_attrs == 1
    m = meta["val_metrics"]["attributes"]["backhand"]
    assert m["n"] == 6 and 0.0 <= m["accuracy"] <= 1.0 and 0 <= m["abstained"] <= 6
    path = tmp_path / "s.pt"
    smod.save_stroke_model(model, meta, path)
    assert smod.load_stroke_model(path)[0].n_attrs == 1
    monkeypatch.setattr(tsc, "MIN_ATTR_LABELS", 200)
    model, meta = tsc.train(rallies[:2], rallies[2:], epochs=1, hidden=8, levels=1, seed=0,
                            device="cpu", log=lambda *_: None)
    assert meta["attributes"] == [] and model.n_attrs == 0
    assert meta["val_metrics"]["attributes"] == {}


def test_attr_metrics_count_abstention_as_wrong():
    import torch
    m = smod.StrokeNet(tsc.INPUT_DIM, hidden=8, levels=1, n_attrs=1).eval()
    with torch.no_grad():
        m.attr_head.weight.zero_()
        m.attr_head.bias.fill_(float(np.log(0.55 / 0.45)))      # p 0.55: always abstains at 0.6
    X, _, A = tsc._windows(_attr_rally(4, "M"))
    out = tsc._attr_metrics(m, ["backhand"], X, A, 0.6)
    assert out["backhand"] == {"n": 4, "accuracy": 0.0, "abstained": 4}
    assert tsc._attr_metrics(m, ["backhand"], X, A, 0.5)["backhand"]["accuracy"] == 0.5


def test_owner_hits_carry_owner_backhand(tmp_path, monkeypatch):
    res, lab = _owner_fixture(tmp_path)
    from badminton_analysis.stroke import owner_labels as ol
    real = ol.apply

    def fake_apply(doc, labels, fps):
        out = real(doc, labels, fps)
        for s in out["strokes"]:
            if s.get("source") == "owner" and s["frame"] == 60:
                s["owner_backhand"] = True
            if s.get("source") == "owner" and s["frame"] == 150:
                s["owner_backhand"] = "yes"          # not a bool: ignored
        return out

    monkeypatch.setattr(ol, "apply", fake_apply)
    hits = tsc.load_owner(str(lab), str(res))[0].hits
    assert hits[0]["attrs"] == {"backhand": 1} and "attrs" not in hits[1]
