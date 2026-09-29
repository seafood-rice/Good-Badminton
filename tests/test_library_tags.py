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


def test_dot_only_tags_are_rejected():
    """A browser collapses /api/tags/.. , so a tag that is only dots can be
    created (normalise used to accept it) but never deleted through the API."""
    assert libtags.normalise(".") is None
    assert libtags.normalise("..") is None
    assert libtags.normalise("...") is None
    assert libtags.normalise(" .. ") is None


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


def test_load_drops_reserved_and_unnormalised_stored_tags(tmp_path):
    """A hand-edited store can hold a reserved name or un-normalised text.

    Left as-is, a stored "match" would duplicate the system chip and make
    that video's PUT 400 (normalise_all refuses the whole request when any
    tag is invalid), and a stored "Smash" could not be renamed or deleted
    because every route normalises its input before comparing.
    """
    p = tmp_path / "library_tags.json"
    p.write_text(json.dumps({"videos": {"clip": ["Smash", "match", "a/b"]}}),
                 encoding="utf-8")
    assert libtags.load(p) == {"clip": ["smash"]}


def test_load_logs_a_corrupt_file_once(tmp_path, capsys):
    p = tmp_path / "library_tags.json"
    p.write_text("{not json", encoding="utf-8")
    libtags.load(p)
    libtags.load(p)
    out = capsys.readouterr().out
    assert out.count(str(p)) == 1


# ------------------------------------------------------------ load_for_update

def test_load_for_update_of_a_missing_file_is_empty(tmp_path):
    assert libtags.load_for_update(tmp_path / "nope.json") == {}


def test_load_for_update_of_a_corrupt_file_raises_store_corrupt(tmp_path):
    p = tmp_path / "library_tags.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(libtags.StoreCorrupt):
        libtags.load_for_update(p)
    # Never overwritten by the mere act of trying to read it.
    assert p.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("junk", ['["a","b"]', '{"videos": []}', '"a string"', '123'])
def test_load_for_update_of_a_structurally_wrong_file_raises_store_corrupt(tmp_path, junk):
    p = tmp_path / "library_tags.json"
    p.write_text(junk, encoding="utf-8")
    with pytest.raises(libtags.StoreCorrupt):
        libtags.load_for_update(p)


def test_load_for_update_of_an_unreadable_file_raises_store_unreadable(tmp_path, monkeypatch):
    p = tmp_path / "library_tags.json"
    p.write_text('{"videos": {}}', encoding="utf-8")

    def boom(*a, **k):
        raise PermissionError("no")

    monkeypatch.setattr(libtags, "open", lambda *a, **k: boom(), raising=False)
    with pytest.raises(libtags.StoreUnreadable):
        libtags.load_for_update(p)


def test_store_unreadable_is_an_oserror():
    """An existing `except OSError` around a write path must still catch it."""
    assert issubclass(libtags.StoreUnreadable, OSError)


def test_load_for_update_sanitises_like_load(tmp_path):
    p = tmp_path / "library_tags.json"
    p.write_text(json.dumps({"videos": {"clip": ["Smash", "match"]}}), encoding="utf-8")
    assert libtags.load_for_update(p) == {"clip": ["smash"]}


def test_backup_corrupt_copies_the_file_byte_identical(tmp_path):
    p = tmp_path / "library_tags.json"
    original = "{not json"
    p.write_text(original, encoding="utf-8")
    backup_path = libtags.backup_corrupt(p)
    assert backup_path != str(p)
    with open(backup_path, "rb") as handle:
        backed_up = handle.read()
    assert backed_up == original.encode("utf-8")
    # The original is untouched by taking a backup.
    assert p.read_text(encoding="utf-8") == original


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


def test_save_retries_os_replace_on_a_transient_permission_error(tmp_path, monkeypatch):
    """Windows antivirus/indexer tools transiently hold a handle open on a
    just-written file; os.replace must not give up on the first collision."""
    p = tmp_path / "library_tags.json"
    real_replace = libtags.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("busy")
        return real_replace(src, dst)

    monkeypatch.setattr(libtags.os, "replace", flaky)
    monkeypatch.setattr(libtags.time, "sleep", lambda *a, **k: None)
    libtags.save({"clip": ["smash"]}, p)
    assert len(calls) == 3
    assert libtags.load(p) == {"clip": ["smash"]}


def test_save_reraises_after_exhausting_retries(tmp_path, monkeypatch):
    p = tmp_path / "library_tags.json"

    def always_busy(*a, **k):
        raise PermissionError("busy")

    monkeypatch.setattr(libtags.os, "replace", always_busy)
    monkeypatch.setattr(libtags.time, "sleep", lambda *a, **k: None)
    with pytest.raises(PermissionError):
        libtags.save({"clip": ["smash"]}, p)


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
