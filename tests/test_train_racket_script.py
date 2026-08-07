import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from train_racket_detector import build_dataset_yaml, discover_splits, find_dataset_yaml


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_discover_splits_roboflow_layout(tmp_path):
    for split in ("train", "valid", "test"):
        _mk(tmp_path / split / "images")
        _mk(tmp_path / split / "labels")
    splits = discover_splits(tmp_path)
    assert splits["train"] == tmp_path / "train" / "images"
    assert splits["val"] == tmp_path / "valid" / "images"
    assert splits["test"] == tmp_path / "test" / "images"


def test_discover_splits_cvat_layout(tmp_path):
    for split in ("train", "val"):
        _mk(tmp_path / "images" / split)
        _mk(tmp_path / "labels" / split)
    splits = discover_splits(tmp_path)
    assert splits["train"] == tmp_path / "images" / "train"
    assert splits["val"] == tmp_path / "images" / "val"
    assert splits["test"] is None


def test_build_yaml_prefers_existing_yaml(tmp_path):
    (tmp_path / "data.yaml").write_text(
        "path: /somewhere/else\ntrain: train/images\nval: valid/images\nnc: 1\nnames: ['racket']\n",
        encoding="utf-8")
    out = build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    text = out.read_text(encoding="utf-8")
    assert tmp_path.resolve().as_posix() in text
    assert "/somewhere/else" not in text


def test_build_yaml_rewrites_parent_relative_splits(tmp_path):
    (tmp_path / "data.yaml").write_text(
        "train: ../train/images\nval: ../valid/images\nnc: 1\nnames: ['racket']\n",
        encoding="utf-8")
    out = build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    text = out.read_text(encoding="utf-8")
    assert "../" not in text
    assert "train/images" in text and "valid/images" in text
    assert tmp_path.resolve().as_posix() in text


def test_build_yaml_from_discovered_splits(tmp_path):
    for split in ("train", "valid"):
        _mk(tmp_path / split / "images")
    out = build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    text = out.read_text(encoding="utf-8")
    assert "names:" in text and "racket" in text
    assert "train" in text and "valid" in text
    assert "\\" not in text


def test_build_yaml_fails_clearly_when_empty(tmp_path):
    with pytest.raises(SystemExit) as e:
        build_dataset_yaml(tmp_path, tmp_path / "racketdb.yaml")
    assert "racketdb" in str(e.value).lower() or "huggingface" in str(e.value).lower()
