"""The video-library tag store.

Kept free of Flask so the rules that matter -- normalisation, merge-on-rename,
the atomic write, and recovering from a corrupt file -- are testable without a
request context.
"""
import json
import os

import pytest

from badminton_analysis.library import tags as libtags


# ---------------------------------------------------------------- normalise

def test_tags_are_trimmed_and_lowercased():
    assert libtags.normalise("  Smash ") == "smash"


def test_empty_and_whitespace_are_rejected():
    assert libtags.normalise("") is None
    assert libtags.normalise("   ") is None
    assert libtags.normalise(None) is None


def test_reserved_names_are_rejected():
    """match/drill are derived from analysis results, never user-owned."""
    for name in ("match", "drill", "  MATCH  "):
        assert libtags.normalise(name) is None


def test_overlong_tags_are_rejected_not_truncated():
    assert libtags.normalise("x" * libtags.MAX_TAG_LEN) == "x" * libtags.MAX_TAG_LEN
    assert libtags.normalise("x" * (libtags.MAX_TAG_LEN + 1)) is None


def test_control_characters_are_rejected():
    assert libtags.normalise("bad\ttag") is None
    assert libtags.normalise("bad\ntag") is None


def test_path_separators_are_rejected():
    """A tag is a URL path segment in DELETE /api/tags/<tag>; a slash breaks routing."""
    assert libtags.normalise("a/b") is None
    assert libtags.normalise("a\\b") is None


def test_normalise_all_splits_clean_from_rejected():
    clean, rejected = libtags.normalise_all([" Smash ", "match", "", "court-a", "smash"])
    assert clean == ["court-a", "smash"]          # sorted, de-duplicated
    assert rejected == ["match", ""]


def test_normalise_all_of_nothing():
    assert libtags.normalise_all([]) == ([], [])


# ---------------------------------------------------------------- load / save

def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "data" / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    assert libtags.load(p) == {"clip": ["smash"]}


def test_save_writes_the_documented_shape(tmp_path):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["schema_version"] == libtags.SCHEMA_VERSION
    assert payload["videos"] == {"clip": ["smash"]}


def test_missing_file_loads_as_empty(tmp_path):
    assert libtags.load(tmp_path / "nope.json") == {}


def test_corrupt_file_loads_as_empty_and_is_not_overwritten(tmp_path):
    """A recoverable file must survive a read. Losing every tag to a bad parse
    would be worse than showing none."""
    p = tmp_path / "library_tags.json"
    p.write_text("{not json", encoding="utf-8")
    assert libtags.load(p) == {}
    assert p.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("junk", ['["a","b"]', '{"videos": []}', '"a string"', '123'])
def test_structurally_wrong_files_load_as_empty(tmp_path, junk):
    p = tmp_path / "library_tags.json"
    p.write_text(junk, encoding="utf-8")
    assert libtags.load(p) == {}


def test_non_list_and_non_string_entries_are_dropped(tmp_path):
    p = tmp_path / "library_tags.json"
    p.write_text(json.dumps({"videos": {"a": "smash", "b": ["ok", 7], "c": ["fine"]}}),
                 encoding="utf-8")
    assert libtags.load(p) == {"b": ["ok"], "c": ["fine"]}


def test_save_is_atomic_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(libtags.os, "replace", boom)
    with pytest.raises(OSError):
        libtags.save({"clip": ["smash", "court-a"]}, p)

    assert libtags.load(p) == {"clip": ["smash"]}, "the previous file must survive"


def test_save_leaves_no_temp_file_behind(tmp_path):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    assert [f.name for f in tmp_path.iterdir()] == ["library_tags.json"]


def test_failed_save_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    monkeypatch.setattr(libtags.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        libtags.save({"clip": ["x"]}, p)
    assert sorted(f.name for f in tmp_path.iterdir()) == ["library_tags.json"]


def test_save_creates_the_directory(tmp_path):
    p = tmp_path / "made" / "up" / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    assert p.exists()


# ---------------------------------------------------------------- mutations

def test_set_tags_replaces_the_whole_list():
    data = {"clip": ["old"]}
    assert libtags.set_tags(data, "clip", ["new", "another"]) == {"clip": ["another", "new"]}


def test_set_tags_to_empty_drops_the_key():
    """An empty list is absence, not an empty array on disk."""
    assert libtags.set_tags({"clip": ["smash"]}, "clip", []) == {}


def test_set_tags_does_not_mutate_its_argument():
    data = {"clip": ["old"]}
    libtags.set_tags(data, "clip", ["new"])
    assert data == {"clip": ["old"]}


def test_tags_for_unknown_stem_is_empty():
    assert libtags.tags_for({}, "nope") == []


def test_rename_applies_everywhere():
    data = {"a": ["smash"], "b": ["smash", "net"], "c": ["net"]}
    assert libtags.rename(data, "smash", "smashes") == {
        "a": ["smashes"], "b": ["net", "smashes"], "c": ["net"]}


def test_rename_onto_an_existing_tag_merges_without_duplicating():
    data = {"a": ["smash", "net"]}
    assert libtags.rename(data, "smash", "net") == {"a": ["net"]}


def test_rename_of_an_unused_tag_changes_nothing():
    data = {"a": ["net"]}
    assert libtags.rename(data, "smash", "smashes") == {"a": ["net"]}


def test_delete_removes_the_tag_everywhere_and_drops_emptied_keys():
    data = {"a": ["smash"], "b": ["smash", "net"]}
    assert libtags.delete(data, "smash") == {"b": ["net"]}


def test_forget_drops_one_video():
    assert libtags.forget({"a": ["x"], "b": ["y"]}, "a") == {"b": ["y"]}


def test_forget_of_an_unknown_stem_is_a_no_op():
    assert libtags.forget({"a": ["x"]}, "zzz") == {"a": ["x"]}
