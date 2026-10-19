"""The athlete / squad / match store.

Kept free of Flask, like ``tests/test_library_tags.py``: the rules that matter
-- name validation, reference rewriting on delete, match validation, the atomic
write and recovering from a corrupt file -- are testable without a request.
"""
import copy
import json
import re
import threading

import pytest

from badminton_analysis.library import athletes as ath


def _with_two_athletes():
    data, tanaka = ath.add_athlete(ath.empty(), "J. Tanaka")
    data, ito = ath.add_athlete(data, "S. Ito")
    return data, tanaka, ito


# ------------------------------------------------------------------ shape

def test_empty_store_shape():
    assert ath.empty() == {"squads": {}, "athletes": {}, "matches": {}}


def test_empty_returns_a_fresh_dict_each_time():
    first = ath.empty()
    first["athletes"]["a_x"] = {"name": "x", "squad_id": None}
    assert ath.empty() == {"squads": {}, "athletes": {}, "matches": {}}


def test_schema_version_is_one():
    assert ath.SCHEMA_VERSION == 1


# ---------------------------------------------------------------- names

def test_normalise_name_trims():
    assert ath.normalise_name("  J. Tanaka ") == "J. Tanaka"


@pytest.mark.parametrize("bad", ["", "   ", "x" * 61, "a\x00b", "a\tb", "a\nb", None, 7])
def test_normalise_name_rejects_unusable(bad):
    assert ath.normalise_name(bad) is None


def test_normalise_name_accepts_the_length_limit_and_unicode():
    assert ath.normalise_name("x" * 60) == "x" * 60
    assert ath.normalise_name("田中 太郎") == "田中 太郎"


# --------------------------------------------------------------- athletes

def test_add_athlete_returns_an_id_in_the_documented_form():
    data, athlete_id = ath.add_athlete(ath.empty(), "J. Tanaka")
    assert re.fullmatch(r"a_[0-9a-f]{8}", athlete_id)
    assert data["athletes"][athlete_id] == {"name": "J. Tanaka", "squad_id": None}


def test_add_athlete_does_not_mutate_its_argument():
    data = ath.empty()
    before = copy.deepcopy(data)
    ath.add_athlete(data, "J. Tanaka")
    assert data == before


def test_add_athlete_duplicate_name_is_case_insensitive():
    data, _ = ath.add_athlete(ath.empty(), "J. Tanaka")
    with pytest.raises(ValueError, match="^duplicate$"):
        ath.add_athlete(data, "  j. TANAKA ")


@pytest.mark.parametrize("bad", ["  ", "x" * 61, "a\x00b"])
def test_add_athlete_invalid_names(bad):
    with pytest.raises(ValueError, match="^invalid$"):
        ath.add_athlete(ath.empty(), bad)


def test_add_athlete_with_unknown_squad():
    with pytest.raises(ValueError, match="^unknown_squad$"):
        ath.add_athlete(ath.empty(), "J. Tanaka", squad_id="s_deadbeef")


def test_add_athlete_into_an_existing_squad():
    data, squad_id = ath.add_squad(ath.empty(), "Seniors")
    data, athlete_id = ath.add_athlete(data, "J. Tanaka", squad_id=squad_id)
    assert data["athletes"][athlete_id]["squad_id"] == squad_id


def test_update_athlete_renames_and_moves_squad():
    data, squad_id = ath.add_squad(ath.empty(), "Seniors")
    data, athlete_id = ath.add_athlete(data, "J. Tanaka")
    data = ath.update_athlete(data, athlete_id, name=" Jun Tanaka ", squad_id=squad_id)
    assert data["athletes"][athlete_id] == {"name": "Jun Tanaka", "squad_id": squad_id}


def test_update_athlete_omitted_fields_are_left_alone():
    data, squad_id = ath.add_squad(ath.empty(), "Seniors")
    data, athlete_id = ath.add_athlete(data, "J. Tanaka", squad_id=squad_id)
    data = ath.update_athlete(data, athlete_id, name="Jun Tanaka")
    assert data["athletes"][athlete_id]["squad_id"] == squad_id
    data = ath.update_athlete(data, athlete_id, squad_id=None)   # explicit None clears
    assert data["athletes"][athlete_id] == {"name": "Jun Tanaka", "squad_id": None}


def test_update_athlete_may_keep_its_own_name_with_other_case():
    data, athlete_id = ath.add_athlete(ath.empty(), "J. Tanaka")
    data = ath.update_athlete(data, athlete_id, name="j. tanaka")
    assert data["athletes"][athlete_id]["name"] == "j. tanaka"


def test_update_athlete_to_another_athletes_name_is_a_duplicate():
    data, tanaka, ito = _with_two_athletes()
    with pytest.raises(ValueError, match="^duplicate$"):
        ath.update_athlete(data, tanaka, name="s. ito")


def test_update_athlete_invalid_name_and_unknown_squad():
    data, athlete_id = ath.add_athlete(ath.empty(), "J. Tanaka")
    with pytest.raises(ValueError, match="^invalid$"):
        ath.update_athlete(data, athlete_id, name="")
    with pytest.raises(ValueError, match="^unknown_squad$"):
        ath.update_athlete(data, athlete_id, squad_id="s_deadbeef")


@pytest.mark.parametrize("bad_squad", [[], {}, 7, ["s_1"]])
def test_non_string_squad_id_is_an_unknown_squad_not_a_type_error(bad_squad):
    with pytest.raises(ValueError, match="^unknown_squad$"):
        ath.add_athlete(ath.empty(), "X", squad_id=bad_squad)
    data, athlete_id = ath.add_athlete(ath.empty(), "X")
    with pytest.raises(ValueError, match="^unknown_squad$"):
        ath.update_athlete(data, athlete_id, squad_id=bad_squad)


def test_update_unknown_athlete_raises_key_error():
    with pytest.raises(KeyError):
        ath.update_athlete(ath.empty(), "a_00000000", name="x")


def test_update_athlete_does_not_mutate_its_argument():
    data, athlete_id = ath.add_athlete(ath.empty(), "J. Tanaka")
    before = copy.deepcopy(data)
    ath.update_athlete(data, athlete_id, name="Jun")
    assert data == before


def test_delete_athlete_rewrites_references_to_a_plain_name():
    data, tanaka, ito = _with_two_athletes()
    data = ath.set_match(data, "clip", {
        "date": "2026-10-11", "upper": {"athlete_id": tanaka}, "lower": {"name": "Guest"},
        "result": None})
    data, rewritten = ath.delete_athlete(data, tanaka)
    assert rewritten == 1
    assert tanaka not in data["athletes"]
    assert data["matches"]["clip"]["upper"] == {"name": "J. Tanaka"}
    assert data["matches"]["clip"]["lower"] == {"name": "Guest"}


def test_delete_athlete_counts_every_rewritten_side_across_matches():
    data, tanaka, ito = _with_two_athletes()
    for stem in ("m1", "m2"):
        data = ath.set_match(data, stem, {
            "date": None, "upper": {"athlete_id": tanaka}, "lower": {"athlete_id": ito},
            "result": None})
    data, rewritten = ath.delete_athlete(data, tanaka)
    assert rewritten == 2
    assert data["matches"]["m1"]["lower"] == {"athlete_id": ito}   # others untouched


def test_delete_athlete_with_no_references():
    data, tanaka, ito = _with_two_athletes()
    data, rewritten = ath.delete_athlete(data, tanaka)
    assert rewritten == 0
    assert list(data["athletes"]) == [ito]


def test_delete_unknown_athlete_is_a_no_op():
    data, _, _ = _with_two_athletes()
    out, rewritten = ath.delete_athlete(data, "a_00000000")
    assert out == data and rewritten == 0


# ----------------------------------------------------------------- squads

def test_add_squad_returns_an_id_in_the_documented_form():
    data, squad_id = ath.add_squad(ath.empty(), "Senior men's singles")
    assert re.fullmatch(r"s_[0-9a-f]{8}", squad_id)
    assert data["squads"][squad_id] == {"name": "Senior men's singles"}


def test_squad_names_are_unique_case_insensitively_and_validated():
    data, _ = ath.add_squad(ath.empty(), "Seniors")
    with pytest.raises(ValueError, match="^duplicate$"):
        ath.add_squad(data, "SENIORS")
    with pytest.raises(ValueError, match="^invalid$"):
        ath.add_squad(data, "a\x00b")


def test_update_squad_renames_and_guards():
    data, one = ath.add_squad(ath.empty(), "Seniors")
    data, two = ath.add_squad(data, "Juniors")
    data = ath.update_squad(data, one, name="Elite")
    assert data["squads"][one] == {"name": "Elite"}
    with pytest.raises(ValueError, match="^duplicate$"):
        ath.update_squad(data, two, name="elite")
    with pytest.raises(ValueError, match="^invalid$"):
        ath.update_squad(data, two, name="  ")
    with pytest.raises(KeyError):
        ath.update_squad(data, "s_00000000", name="x")


def test_deleting_a_squad_clears_its_members_squad_id():
    data, squad_id = ath.add_squad(ath.empty(), "Seniors")
    data, member = ath.add_athlete(data, "J. Tanaka", squad_id=squad_id)
    data, other = ath.add_athlete(data, "S. Ito")
    data, cleared = ath.delete_squad(data, squad_id)
    assert cleared == 1
    assert squad_id not in data["squads"]
    assert data["athletes"][member]["squad_id"] is None
    assert data["athletes"][other]["squad_id"] is None


def test_delete_unknown_squad_is_a_no_op():
    data, _ = ath.add_squad(ath.empty(), "Seniors")
    out, cleared = ath.delete_squad(data, "s_00000000")
    assert out == data and cleared == 0


# ---------------------------------------------------------------- matches

def _good_record(lower_id):
    return {"date": "2026-10-11", "upper": {"name": "S. Ito"}, "lower": {"athlete_id": lower_id},
            "result": {"winner": "upper", "sets": [[18, 21], [21, 19]]}}


def test_validate_match_accepts_a_full_record():
    data, tanaka, _ = _with_two_athletes()
    clean, err = ath.validate_match(_good_record(tanaka), data)
    assert err is None
    assert clean == _good_record(tanaka)


def test_validate_match_trims_a_name_and_fills_missing_keys():
    clean, err = ath.validate_match({"upper": {"name": "  S. Ito "}}, ath.empty())
    assert err is None
    assert clean == {"date": None, "upper": {"name": "S. Ito"}, "lower": None, "result": None}


def test_validate_match_normalises_sets_to_lists():
    clean, err = ath.validate_match({"result": {"winner": "lower", "sets": [(21, 5)]}}, ath.empty())
    assert err is None
    assert clean["result"] == {"winner": "lower", "sets": [[21, 5]]}


def test_validate_match_without_sets_gives_an_empty_list():
    clean, err = ath.validate_match({"result": {"winner": "lower"}}, ath.empty())
    assert err is None
    assert clean["result"] == {"winner": "lower", "sets": []}


@pytest.mark.parametrize("record", [
    {"date": "2026-02-30"},                                    # not a real date
    {"date": "2026-2-3"},                                      # not zero padded
    {"date": "20261011"},                                      # wrong format
    {"date": 20261011},                                        # wrong type
    {"result": {"winner": "left", "sets": []}},                # unknown winner
    {"result": {"sets": []}},                                  # no winner
    {"result": {"winner": "upper", "sets": [[1, 2]] * 6}},     # six sets
    {"result": {"winner": "upper", "sets": [[100, 0]]}},       # score out of range
    {"result": {"winner": "upper", "sets": [[-1, 0]]}},        # negative score
    {"result": {"winner": "upper", "sets": [[1.5, 0]]}},       # not an integer
    {"result": {"winner": "upper", "sets": [[True, 0]]}},      # bool is not a score
    {"result": {"winner": "upper", "sets": [[1, 2, 3]]}},      # not a pair
    {"result": {"winner": "upper", "sets": "21-18"}},          # not a list
    {"result": "upper"},                                       # not an object
    {"upper": {"athlete_id": "a_00000000", "name": "S. Ito"}},  # both reference and name
    {"upper": {}},                                             # neither
    {"upper": {"name": ""}},                                   # unusable name
    {"upper": "S. Ito"},                                       # not an object
])
def test_validate_match_rejects(record):
    clean, err = ath.validate_match(record, ath.empty())
    assert clean is None
    assert isinstance(err, str) and err


def test_validate_match_rejects_a_non_object_record():
    assert ath.validate_match(["x"], ath.empty())[0] is None
    assert ath.validate_match(None, ath.empty())[0] is None


def test_validate_match_rejects_an_unknown_athlete_id_given_the_store():
    data, _, _ = _with_two_athletes()
    clean, err = ath.validate_match({"upper": {"athlete_id": "a_00000000"}}, data)
    assert clean is None
    assert err == "unknown_athlete"


def test_validate_match_requires_the_store():
    """No default: a caller can never skip the unknown-athlete check by accident."""
    with pytest.raises(TypeError):
        ath.validate_match({"upper": {"athlete_id": "a_00000000"}})


def test_validate_match_error_codes_are_distinct_per_field():
    codes = {
        ath.validate_match({"date": "2026-02-30"}, ath.empty())[1],
        ath.validate_match({"result": {"winner": "left"}}, ath.empty())[1],
        ath.validate_match({"upper": {}}, ath.empty())[1],
    }
    assert len(codes) == 3


def test_validate_match_does_not_mutate_its_argument():
    record = {"upper": {"name": "  S. Ito "}, "result": {"winner": "upper", "sets": [[21, 1]]}}
    before = copy.deepcopy(record)
    ath.validate_match(record, ath.empty())
    assert record == before


def test_set_match_then_get_match_round_trips():
    data, tanaka, _ = _with_two_athletes()
    record = _good_record(tanaka)
    data = ath.set_match(data, "Dji 20260919100627 0004 D", record)
    assert ath.get_match(data, "Dji 20260919100627 0004 D") == record


def test_get_match_of_an_unknown_stem_is_empty():
    assert ath.get_match(ath.empty(), "nope") == {}


def test_set_match_none_deletes():
    data = ath.set_match(ath.empty(), "clip", {"date": "2026-10-11"})
    assert "clip" in data["matches"]
    data = ath.set_match(data, "clip", None)
    assert data["matches"] == {}


def test_set_match_all_null_record_deletes():
    data = ath.set_match(ath.empty(), "clip", {"date": "2026-10-11"})
    data = ath.set_match(data, "clip", {"date": None, "upper": None, "lower": None, "result": None})
    assert data["matches"] == {}
    assert ath.set_match(ath.empty(), "clip", {}) == ath.empty()


def test_set_match_replaces_the_record_whole():
    data = ath.set_match(ath.empty(), "clip", {"date": "2026-10-11", "upper": {"name": "A"}})
    data = ath.set_match(data, "clip", {"date": "2026-10-12"})
    assert ath.get_match(data, "clip") == {
        "date": "2026-10-12", "upper": None, "lower": None, "result": None}


def test_set_match_does_not_mutate_its_argument():
    data = ath.empty()
    before = copy.deepcopy(data)
    ath.set_match(data, "clip", {"date": "2026-10-11"})
    assert data == before


def test_get_match_returns_a_copy():
    data = ath.set_match(ath.empty(), "clip", {"upper": {"name": "A"}})
    ath.get_match(data, "clip")["upper"]["name"] = "changed"
    assert data["matches"]["clip"]["upper"] == {"name": "A"}


# ----------------------------------------------------------- side_athlete

def test_side_athlete_resolves_a_registered_athlete():
    data, tanaka, _ = _with_two_athletes()
    record = {"upper": {"athlete_id": tanaka}}
    assert ath.side_athlete(data, record, "upper") == {"athlete_id": tanaka, "name": "J. Tanaka"}


def test_side_athlete_follows_a_rename():
    data, tanaka, _ = _with_two_athletes()
    record = {"upper": {"athlete_id": tanaka}}
    data = ath.update_athlete(data, tanaka, name="Jun Tanaka")
    assert ath.side_athlete(data, record, "upper")["name"] == "Jun Tanaka"


def test_side_athlete_plain_name_and_empty_sides():
    data = ath.empty()
    record = {"upper": {"name": "S. Ito"}, "lower": None}
    assert ath.side_athlete(data, record, "upper") == {"athlete_id": None, "name": "S. Ito"}
    assert ath.side_athlete(data, record, "lower") == {"athlete_id": None, "name": None}
    assert ath.side_athlete(data, {}, "upper") == {"athlete_id": None, "name": None}


def test_side_athlete_with_a_dangling_id_is_nobody():
    record = {"upper": {"athlete_id": "a_00000000"}}
    assert ath.side_athlete(ath.empty(), record, "upper") == {"athlete_id": None, "name": None}


# ------------------------------------------------------------ load / save

def test_save_then_load_round_trips_a_unicode_name(tmp_path):
    p = tmp_path / "data" / "athletes.json"
    data, athlete_id = ath.add_athlete(ath.empty(), "田中 太郎")
    ath.save(data, p)
    assert ath.load(p) == data
    assert ath.load(p)["athletes"][athlete_id]["name"] == "田中 太郎"
    assert "田中" in p.read_text(encoding="utf-8")        # ensure_ascii=False


def test_save_writes_the_documented_shape(tmp_path):
    p = tmp_path / "athletes.json"
    data, squad_id = ath.add_squad(ath.empty(), "Seniors")
    ath.save(data, p)
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["schema_version"] == ath.SCHEMA_VERSION
    assert payload["squads"] == {squad_id: {"name": "Seniors"}}
    assert payload["athletes"] == {} and payload["matches"] == {}


def test_missing_file_loads_as_empty(tmp_path):
    assert ath.load(tmp_path / "nope.json") == ath.empty()
    assert ath.load_for_update(tmp_path / "nope.json") == ath.empty()


def test_corrupt_file_loads_empty_is_not_overwritten_and_blocks_update(tmp_path):
    p = tmp_path / "athletes.json"
    p.write_text("{not json", encoding="utf-8")
    assert ath.load(p) == ath.empty()
    assert p.read_text(encoding="utf-8") == "{not json"
    with pytest.raises(ath.StoreCorrupt):
        ath.load_for_update(p)
    assert p.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("junk", [
    '["a"]', '"a string"', '123', '{}',
    '{"athletes": []}', '{"squads": 1, "athletes": {}}', '{"matches": "x"}'])
def test_structurally_wrong_files_are_corrupt(tmp_path, junk):
    p = tmp_path / "athletes.json"
    p.write_text(junk, encoding="utf-8")
    assert ath.load(p) == ath.empty()
    with pytest.raises(ath.StoreCorrupt):
        ath.load_for_update(p)


def test_a_file_with_only_some_sections_is_ok(tmp_path):
    p = tmp_path / "athletes.json"
    p.write_text(json.dumps({"athletes": {"a_1": {"name": "A"}}}), encoding="utf-8")
    assert ath.load(p) == {"squads": {}, "matches": {},
                           "athletes": {"a_1": {"name": "A", "squad_id": None}}}


def test_load_sanitises_hand_edited_junk(tmp_path):
    p = tmp_path / "athletes.json"
    p.write_text(json.dumps({
        "squads": {"s_1": {"name": "Seniors"}, "s_2": "x", "s_3": {"name": ""}},
        "athletes": {
            "a_1": {"name": " J. Tanaka ", "squad_id": "s_1"},
            "a_2": {"name": "Ghost", "squad_id": "s_gone"},     # dangling squad -> cleared
            "a_3": {"name": 7},
            "a_4": "x"},
        "matches": {
            "good": {"date": "2026-10-11", "upper": {"athlete_id": "a_1"}},
            "bad-date": {"date": "2026-02-30"},
            "dangling": {"upper": {"athlete_id": "a_gone"}},
            "not-an-object": []},
    }), encoding="utf-8")
    for loaded in (ath.load(p), ath.load_for_update(p)):
        assert loaded["squads"] == {"s_1": {"name": "Seniors"}}
        assert loaded["athletes"] == {
            "a_1": {"name": "J. Tanaka", "squad_id": "s_1"},
            "a_2": {"name": "Ghost", "squad_id": None}}
        assert list(loaded["matches"]) == ["good"]
        assert loaded["matches"]["good"]["upper"] == {"athlete_id": "a_1"}


@pytest.mark.parametrize("bad_squad", [[], {}, 7, ["s_1"]])
def test_unhashable_or_non_string_squad_id_in_a_file_never_raises(tmp_path, bad_squad):
    p = tmp_path / "athletes.json"
    p.write_text(json.dumps({
        "squads": {"s_1": {"name": "Seniors"}},
        "athletes": {"a_1": {"name": "J. Tanaka", "squad_id": bad_squad}}}), encoding="utf-8")
    expected = {"a_1": {"name": "J. Tanaka", "squad_id": None}}
    assert ath.load(p)["athletes"] == expected
    assert ath.load_for_update(p)["athletes"] == expected


def test_unhashable_athlete_id_in_a_match_side_never_raises(tmp_path):
    p = tmp_path / "athletes.json"
    p.write_text(json.dumps({"matches": {
        "m1": {"date": "2026-10-11", "upper": {"athlete_id": []}},
        "m2": {"upper": {"athlete_id": {}}, "lower": {"athlete_id": []}}}}), encoding="utf-8")
    loaded = ath.load(p)
    assert loaded["matches"] == {"m1": {"date": "2026-10-11", "upper": None,
                                        "lower": None, "result": None}}


def test_a_dangling_athlete_on_one_side_keeps_the_rest_of_the_match(tmp_path):
    p = tmp_path / "athletes.json"
    p.write_text(json.dumps({
        "athletes": {"a_1": {"name": "J. Tanaka"}},
        "matches": {"m": {
            "date": "2026-10-11",
            "upper": {"athlete_id": "a_gone"},
            "lower": {"athlete_id": "a_1"},
            "result": {"winner": "lower", "sets": [[10, 21]]}}}}), encoding="utf-8")
    for loaded in (ath.load(p), ath.load_for_update(p)):
        assert loaded["matches"]["m"] == {
            "date": "2026-10-11", "upper": None, "lower": {"athlete_id": "a_1"},
            "result": {"winner": "lower", "sets": [[10, 21]]}}


def test_deeply_nested_json_is_corrupt_not_a_crash(tmp_path):
    p = tmp_path / "athletes.json"
    p.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert ath.load(p) == ath.empty()
    with pytest.raises(ath.StoreCorrupt):
        ath.load_for_update(p)


def test_store_error_messages_do_not_leak_the_path(tmp_path):
    corrupt = tmp_path / "corrupt_store.json"
    corrupt.write_text("{not json", encoding="utf-8")
    with pytest.raises(ath.StoreCorrupt) as corrupt_info:
        ath.load_for_update(corrupt)
    unreadable = tmp_path / "unreadable_store.json"
    unreadable.mkdir()
    with pytest.raises(ath.StoreUnreadable) as unreadable_info:
        ath.load_for_update(unreadable)
    for info in (corrupt_info, unreadable_info):
        text = str(info.value)
        assert str(tmp_path) not in text
        assert "_store" not in text           # nor the file names


def test_load_logs_a_corrupt_file_once(tmp_path, capsys):
    p = tmp_path / "athletes.json"
    p.write_text("{not json", encoding="utf-8")
    ath.load(p)
    ath.load(p)
    assert capsys.readouterr().out.count(str(p)) == 1


def test_a_directory_as_the_store_is_unreadable(tmp_path):
    p = tmp_path / "athletes.json"
    p.mkdir()
    with pytest.raises(ath.StoreUnreadable):
        ath.load_for_update(p)
    assert ath.load(p) == ath.empty()      # a read never raises


def test_store_unreadable_is_an_oserror():
    assert issubclass(ath.StoreUnreadable, OSError)
    assert issubclass(ath.StoreCorrupt, ValueError)


def test_backup_corrupt_copies_the_file_byte_identical(tmp_path):
    p = tmp_path / "athletes.json"
    original = b"{not json \xff\xfe"
    p.write_bytes(original)
    backup = ath.backup_corrupt(p)
    assert backup != str(p)
    assert ".corrupt-" in backup
    with open(backup, "rb") as handle:
        assert handle.read() == original
    assert p.read_bytes() == original


def test_save_is_atomic_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    p = tmp_path / "athletes.json"
    data, _ = ath.add_athlete(ath.empty(), "J. Tanaka")
    ath.save(data, p)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(ath.os, "replace", boom)
    with pytest.raises(OSError):
        ath.save(ath.add_athlete(data, "S. Ito")[0], p)
    assert ath.load(p) == data
    assert [f.name for f in tmp_path.iterdir()] == ["athletes.json"]    # no temp left


def test_save_leaves_no_temp_file_and_creates_the_directory(tmp_path):
    p = tmp_path / "made" / "up" / "athletes.json"
    ath.save(ath.empty(), p)
    assert [f.name for f in p.parent.iterdir()] == ["athletes.json"]


def test_save_retries_os_replace_on_a_transient_permission_error(tmp_path, monkeypatch):
    p = tmp_path / "athletes.json"
    real_replace = ath.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError("busy")
        return real_replace(src, dst)

    monkeypatch.setattr(ath.os, "replace", flaky)
    monkeypatch.setattr(ath.time, "sleep", lambda *a, **k: None)
    data, _ = ath.add_athlete(ath.empty(), "J. Tanaka")
    ath.save(data, p)
    assert len(calls) == 3
    assert ath.load(p) == data


def test_save_reraises_after_exhausting_retries(tmp_path, monkeypatch):
    calls = []

    def always_busy(*a, **k):
        calls.append(1)
        raise PermissionError("busy")

    monkeypatch.setattr(ath.os, "replace", always_busy)
    monkeypatch.setattr(ath.time, "sleep", lambda *a, **k: None)
    with pytest.raises(PermissionError):
        ath.save(ath.empty(), tmp_path / "athletes.json")
    assert len(calls) == 5


def test_concurrent_edits_under_the_callers_lock_lose_nothing(tmp_path):
    """The module is pure and takes no lock itself: the caller (app.py's
    _ATHLETES_LOCK) serialises every read-modify-write. This documents that
    contract -- with the lock held, two writers both survive."""
    p = tmp_path / "athletes.json"
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    errors = []

    def worker(name):
        try:
            barrier.wait()
            with lock:
                data = ath.load_for_update(p)
                data, _ = ath.add_athlete(data, name)
                ath.save(data, p)
        except Exception as exc:       # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("J. Tanaka", "S. Ito")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    names = sorted(a["name"] for a in ath.load(p)["athletes"].values())
    assert names == ["J. Tanaka", "S. Ito"]
