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


# ================================================================== doubles

def _doubles(players=None, **extra):
    record = {"date": "2026-10-11", "format": "doubles",
              "players": players if players is not None else {}, "result": None}
    record.update(extra)
    return record


def _with_four():
    data = ath.empty()
    ids = []
    for name in ("A. One", "B. Two", "C. Three", "D. Four"):
        data, athlete_id = ath.add_athlete(data, name)
        ids.append(athlete_id)
    return data, ids


def test_record_format_defaults_to_singles():
    assert ath.record_format({"format": "doubles"}) == "doubles"
    for record in ({}, {"format": "singles"}, {"format": None}, {"format": ["doubles"]},
                   {"upper": None}, None, [], "doubles"):
        assert ath.record_format(record) == "singles"


def test_validate_match_accepts_a_full_doubles_record_and_round_trips():
    data, ids = _with_four()
    record = {"date": "2026-10-11", "format": "doubles",
              "players": {"P1": {"athlete_id": ids[0]}, "P2": {"name": "  Guest "},
                          "P3": {"athlete_id": ids[2]}, "P4": None},
              "result": {"winner_team": 2, "sets": [[21, 18], [19, 21], [15, 21]]}}
    clean, err = ath.validate_match(record, data)
    assert err is None
    assert clean == {**record, "players": {**record["players"], "P2": {"name": "Guest"}}}
    data = ath.set_match(data, "clip", clean)
    assert ath.get_match(data, "clip") == clean
    assert "upper" not in clean and "lower" not in clean


def test_validate_match_doubles_accepts_missing_and_null_identities():
    data, ids = _with_four()
    clean, err = ath.validate_match(_doubles({"P3": {"athlete_id": ids[0]}}), data)
    assert err is None and clean["players"] == {"P3": {"athlete_id": ids[0]}}
    clean, err = ath.validate_match({"format": "doubles", "date": "2026-10-11"}, data)
    assert err is None and clean["players"] == {} and clean["result"] is None
    clean, err = ath.validate_match(
        _doubles({"P1": None, "P2": None, "P3": None, "P4": None}), data)
    assert err is None and clean["players"] == {"P1": None, "P2": None, "P3": None, "P4": None}


@pytest.mark.parametrize("winner", [1, 2])
def test_doubles_winner_team_accepts_one_and_two(winner):
    clean, err = ath.validate_match(
        _doubles(result={"winner_team": winner, "sets": [[21, 1]]}), ath.empty())
    assert err is None and clean["result"] == {"winner_team": winner, "sets": [[21, 1]]}


@pytest.mark.parametrize("result", [
    {"winner_team": 3, "sets": []},
    {"winner_team": 0, "sets": []},
    {"winner_team": "upper", "sets": []},
    {"winner_team": "1", "sets": []},
    {"winner_team": True, "sets": []},                         # bool is not a team
    {"winner_team": 1.0, "sets": []},
    {"winner_team": None, "sets": [[21, 18]]},                 # sets need a winner
    {"sets": [[21, 18]]},
    {"winner": "upper", "sets": []},                           # singles winner
    {"winner": "upper", "winner_team": 1, "sets": []},
    {"winner_team": 1, "sets": [[1, 2]] * 6},                  # six sets
    {"winner_team": 1, "sets": [[100, 0]]},
    {"winner_team": 1, "sets": [[-1, 0]]},
    {"winner_team": 1, "sets": [[1.5, 0]]},
    {"winner_team": 1, "sets": [[True, 0]]},
    {"winner_team": 1, "sets": [[1, 2, 3]]},
    {"winner_team": 1, "sets": "21-18"},
    "team 1",
])
def test_doubles_invalid_results_are_rejected(result):
    clean, err = ath.validate_match(_doubles(result=result), ath.empty())
    assert clean is None and err == "invalid_result"


def test_doubles_result_accepts_zero_to_five_sets():
    for count in range(6):
        clean, err = ath.validate_match(
            _doubles(result={"winner_team": 1, "sets": [(21, 10)] * count}), ath.empty())
        assert err is None and clean["result"]["sets"] == [[21, 10]] * count


def test_format_doubles_with_a_singles_winner_is_rejected():
    clean, err = ath.validate_match(
        {"format": "doubles", "result": {"winner": "upper", "sets": []}}, ath.empty())
    assert clean is None and err == "invalid_result"


@pytest.mark.parametrize("players", [
    {"P5": None}, {"P0": None}, {"p1": None}, {"upper": {"name": "x"}}, {"": None}, {"1": None},
])
def test_doubles_players_keys_outside_p1_to_p4_are_rejected(players):
    clean, err = ath.validate_match(_doubles(players), ath.empty())
    assert clean is None and err == "invalid_players"


@pytest.mark.parametrize("players", [[], ["P1"], "P1", 7, [{"name": "x"}]])
def test_doubles_players_that_is_not_an_object_is_rejected(players):
    clean, err = ath.validate_match(_doubles(players), ath.empty())
    assert clean is None and err == "invalid_players"


def test_doubles_identity_with_both_athlete_id_and_name_is_rejected():
    data, ids = _with_four()
    clean, err = ath.validate_match(
        _doubles({"P1": {"athlete_id": ids[0], "name": "A. One"}}), data)
    assert clean is None and err == "invalid_side"


@pytest.mark.parametrize("entry", ["S. Ito", {}, {"name": ""}, {"name": 5}, [], {"athlete_id": ""},
                                   {"athlete_id": 7}])
def test_doubles_malformed_identities_are_rejected(entry):
    clean, err = ath.validate_match(_doubles({"P2": entry}), ath.empty())
    assert clean is None and err == "invalid_side"


def test_doubles_same_athlete_on_two_identities_is_rejected():
    data, ids = _with_four()
    clean, err = ath.validate_match(
        _doubles({"P1": {"athlete_id": ids[0]}, "P4": {"athlete_id": ids[0]}}), data)
    assert clean is None and err == "invalid_players"
    # the same plain name twice is only text, not a registry mapping
    clean, err = ath.validate_match(
        _doubles({"P1": {"name": "Guest"}, "P2": {"name": "Guest"}}), data)
    assert err is None


def test_doubles_unknown_athlete_id_is_rejected():
    data, _ = _with_four()
    clean, err = ath.validate_match(_doubles({"P1": {"athlete_id": "a_00000000"}}), data)
    assert clean is None and err == "unknown_athlete"


@pytest.mark.parametrize("extra", [
    {"upper": {"name": "A"}}, {"lower": {"name": "A"}}, {"upper": {"name": "A"}, "lower": None}])
def test_doubles_mixed_with_upper_lower_is_rejected(extra):
    clean, err = ath.validate_match(_doubles({"P1": {"name": "x"}}, **extra), ath.empty())
    assert clean is None and err == "invalid_record"


def test_singles_mixed_with_players_is_rejected():
    clean, err = ath.validate_match(
        {"upper": {"name": "A"}, "players": {"P1": {"name": "x"}}}, ath.empty())
    assert clean is None and err == "invalid_record"
    clean, err = ath.validate_match(
        {"format": "singles", "players": {"P1": {"name": "x"}}}, ath.empty())
    assert clean is None and err == "invalid_record"
    clean, err = ath.validate_match({"players": {"P1": {"name": "x"}}}, ath.empty())
    assert clean is None and err == "invalid_record"


def test_a_null_players_key_does_not_make_a_singles_record_invalid():
    clean, err = ath.validate_match({"upper": {"name": "A"}, "players": None}, ath.empty())
    assert err is None
    assert clean == {"date": None, "upper": {"name": "A"}, "lower": None, "result": None}


@pytest.mark.parametrize("fmt", ["triples", "Doubles", 2, ["doubles"], {}])
def test_an_unknown_format_is_rejected(fmt):
    clean, err = ath.validate_match({"format": fmt, "date": "2026-10-11"}, ath.empty())
    assert clean is None and err == "invalid_record"


def test_explicit_singles_format_is_accepted_and_not_stored():
    clean, err = ath.validate_match({"format": "singles", "upper": {"name": "A"}}, ath.empty())
    assert err is None and "format" not in clean
    assert clean == {"date": None, "upper": {"name": "A"}, "lower": None, "result": None}


def test_doubles_bad_date_is_invalid_date():
    clean, err = ath.validate_match(_doubles(date="2026-02-30"), ath.empty())
    assert clean is None and err == "invalid_date"


def test_validate_match_doubles_does_not_mutate_its_argument():
    data, ids = _with_four()
    record = _doubles({"P1": {"athlete_id": ids[0]}, "P2": {"name": " G "}},
                      result={"winner_team": 1, "sets": [(21, 1)]})
    before = copy.deepcopy(record)
    ath.validate_match(record, data)
    assert record == before


def test_set_match_doubles_blank_record_deletes():
    data = ath.set_match(ath.empty(), "clip", _doubles(date="2026-10-11"))
    assert "clip" in data["matches"]
    blank = {"date": None, "format": "doubles", "players": {"P1": None}, "result": None}
    assert ath.set_match(data, "clip", blank)["matches"] == {}
    assert ath.set_match(ath.empty(), "clip", {"format": "doubles"}) == ath.empty()


def test_set_match_doubles_with_only_players_is_kept():
    data = ath.set_match(ath.empty(), "clip", {"format": "doubles",
                                               "players": {"P2": {"name": "G"}}})
    assert ath.get_match(data, "clip") == {
        "date": None, "format": "doubles", "players": {"P2": {"name": "G"}}, "result": None}


def test_set_match_replaces_a_doubles_record_with_a_singles_one_whole():
    data = ath.set_match(ath.empty(), "clip", _doubles({"P1": {"name": "G"}}))
    data = ath.set_match(data, "clip", {"upper": {"name": "A"}})
    assert ath.get_match(data, "clip") == {
        "date": None, "upper": {"name": "A"}, "lower": None, "result": None}


def test_get_match_of_a_doubles_record_returns_a_copy():
    data = ath.set_match(ath.empty(), "clip", _doubles({"P1": {"name": "G"}}))
    ath.get_match(data, "clip")["players"]["P1"]["name"] = "changed"
    assert data["matches"]["clip"]["players"]["P1"] == {"name": "G"}


def test_set_match_does_not_mutate_its_doubles_argument():
    record = _doubles({"P1": {"name": "G"}})
    before = copy.deepcopy(record)
    ath.set_match(ath.empty(), "clip", record)
    assert record == before


# --------------------------------------------------------- player_athlete

def test_player_athlete_resolves_registered_plain_and_empty_identities():
    data, ids = _with_four()
    record = _doubles({"P1": {"athlete_id": ids[0]}, "P2": {"name": "Guest"}, "P3": None})
    assert ath.player_athlete(data, record, "P1") == {"athlete_id": ids[0], "name": "A. One"}
    assert ath.player_athlete(data, record, "P2") == {"athlete_id": None, "name": "Guest"}
    nobody = {"athlete_id": None, "name": None}
    assert ath.player_athlete(data, record, "P3") == nobody
    assert ath.player_athlete(data, record, "P4") == nobody     # missing
    assert ath.player_athlete(data, record, "P9") == nobody     # unknown identity


def test_player_athlete_follows_a_rename_and_a_dangling_id_is_nobody():
    data, ids = _with_four()
    record = _doubles({"P1": {"athlete_id": ids[0]}, "P2": {"athlete_id": "a_00000000"}})
    data = ath.update_athlete(data, ids[0], name="Alpha One")
    assert ath.player_athlete(data, record, "P1")["name"] == "Alpha One"
    assert ath.player_athlete(data, record, "P2") == {"athlete_id": None, "name": None}


@pytest.mark.parametrize("record", [None, {}, [], "x", {"players": None}, {"players": []},
                                    {"players": "P1"}, {"upper": {"name": "A"}},
                                    {"players": {"P1": "x"}}, {"players": {"P1": []}},
                                    {"players": {"P1": {"athlete_id": []}}}])
def test_player_athlete_is_total_on_hostile_records(record):
    assert ath.player_athlete(ath.empty(), record, "P1") == {"athlete_id": None, "name": None}
    assert ath.player_athlete(ath.empty(), record, ["P1"]) == {"athlete_id": None, "name": None}


def test_side_athlete_is_unchanged_for_a_doubles_record():
    data, ids = _with_four()
    record = _doubles({"P1": {"athlete_id": ids[0]}})
    assert ath.side_athlete(data, record, "upper") == {"athlete_id": None, "name": None}


# ---------------------------------------------------- record_athlete_ids

def test_record_athlete_ids_covers_both_formats_and_hostile_input():
    data, ids = _with_four()
    assert ath.record_athlete_ids(
        {"upper": {"athlete_id": ids[0]}, "lower": {"athlete_id": ids[0]}}) == {ids[0]}
    assert ath.record_athlete_ids(_doubles(
        {"P1": {"athlete_id": ids[0]}, "P2": {"name": "G"}, "P3": {"athlete_id": ids[2]},
         "P4": None})) == {ids[0], ids[2]}
    for hostile in (None, [], {}, {"format": "doubles", "players": []},
                    {"format": "doubles", "players": {"P1": {"athlete_id": []}}},
                    {"upper": "x"}):
        assert ath.record_athlete_ids(hostile) == set()


# ------------------------------------------------------------ delete_athlete

def test_delete_athlete_rewrites_doubles_identities_and_counts_them():
    data, ids = _with_four()
    data = ath.set_match(data, "d1", _doubles(
        {"P1": {"athlete_id": ids[0]}, "P2": {"athlete_id": ids[1]}, "P3": {"name": "G"}}))
    data = ath.set_match(data, "d2", _doubles({"P4": {"athlete_id": ids[0]}}))
    data = ath.set_match(data, "s1", {"upper": {"athlete_id": ids[0]}})
    data, rewritten = ath.delete_athlete(data, ids[0])
    assert rewritten == 3
    assert ids[0] not in data["athletes"]
    assert data["matches"]["d1"]["players"] == {
        "P1": {"name": "A. One"}, "P2": {"athlete_id": ids[1]}, "P3": {"name": "G"}}
    assert data["matches"]["d2"]["players"] == {"P4": {"name": "A. One"}}
    assert data["matches"]["s1"]["upper"] == {"name": "A. One"}


def test_delete_athlete_tolerates_a_hostile_in_memory_players_value():
    data, ids = _with_four()
    data["matches"]["odd"] = {"format": "doubles", "players": ["x"], "result": None}
    out, rewritten = ath.delete_athlete(data, ids[0])
    assert rewritten == 0 and ids[0] not in out["athletes"]


# ----------------------------------------------------------------- load

def _write_store(path, matches, athletes=None):
    path.write_text(json.dumps({"athletes": athletes or {}, "matches": matches}),
                    encoding="utf-8")


def test_load_keeps_a_valid_doubles_record_and_orders_players(tmp_path):
    p = tmp_path / "athletes.json"
    _write_store(p, {"d": {"date": "2026-10-11", "format": "doubles",
                           "players": {"P3": {"athlete_id": "a_1"}, "P1": {"name": " G "}},
                           "result": {"winner_team": 1, "sets": [[21, 5]]}}},
                 {"a_1": {"name": "A"}})
    for loaded in (ath.load(p), ath.load_for_update(p)):
        assert loaded["matches"]["d"] == {
            "date": "2026-10-11", "format": "doubles",
            "players": {"P1": {"name": "G"}, "P3": {"athlete_id": "a_1"}},
            "result": {"winner_team": 1, "sets": [[21, 5]]}}
        assert list(loaded["matches"]["d"]["players"]) == ["P1", "P3"]


@pytest.mark.parametrize("hostile", [
    [], ["P1", "P2"], "P1", 7, None, [{"name": "x"}], {"P1": [[["deep"]]]},
    {"P1": "S. Ito", "P2": 5, "P3": [], "P4": {"athlete_id": []}},
    {"P1": {"athlete_id": {}}, "P2": {"athlete_id": ["a_1"]}},
    {"P1": {"athlete_id": "a_1", "name": "both"}, "P2": {"name": ""}},
    {"P1": {"athlete_id": "a_gone"}},
])
def test_hostile_players_degrade_to_null_identities_without_raising(tmp_path, hostile):
    p = tmp_path / "athletes.json"
    _write_store(p, {"d": {"date": "2026-10-11", "format": "doubles", "players": hostile,
                           "result": None}}, {"a_1": {"name": "A"}})
    for loaded in (ath.load(p), ath.load_for_update(p)):
        record = loaded["matches"]["d"]                      # the date keeps it alive
        assert record["date"] == "2026-10-11" and record["format"] == "doubles"
        assert all(value is None for value in record["players"].values())
        for pid in ("P1", "P2", "P3", "P4"):
            assert ath.player_athlete(loaded, record, pid) == {"athlete_id": None, "name": None}


def test_a_hostile_players_map_with_nothing_else_drops_the_record(tmp_path):
    p = tmp_path / "athletes.json"
    _write_store(p, {"d": {"format": "doubles", "players": ["x"]},
                     "ok": {"date": "2026-10-11"}})
    assert list(ath.load(p)["matches"]) == ["ok"]


def test_load_nulls_only_the_bad_identities_and_the_second_duplicate(tmp_path):
    p = tmp_path / "athletes.json"
    _write_store(p, {"d": {"format": "doubles", "players": {
        "P1": {"athlete_id": "a_1"}, "P2": {"athlete_id": "a_1"}, "P3": {"athlete_id": "a_gone"},
        "P4": {"name": "Guest"}, "P9": {"name": "stray"}}}}, {"a_1": {"name": "A"}})
    assert ath.load(p)["matches"]["d"]["players"] == {
        "P1": {"athlete_id": "a_1"}, "P2": None, "P3": None, "P4": {"name": "Guest"}}


@pytest.mark.parametrize("record", [
    {"format": "doubles", "date": "2026-10-11", "upper": {"name": "A"}},     # mixed shapes
    {"format": "doubles", "date": "2026-10-11", "result": {"winner": "upper", "sets": []}},
    {"format": "doubles", "date": "2026-10-11", "result": {"winner_team": 3, "sets": []}},
    {"format": "doubles", "date": "2026-02-30"},
    {"format": "triples", "date": "2026-10-11"},
    {"format": "doubles", "date": "2026-10-11", "result": []},
])
def test_load_drops_a_doubles_record_that_fails_validation(tmp_path, record):
    p = tmp_path / "athletes.json"
    _write_store(p, {"bad": record, "ok": {"date": "2026-10-11"}})
    assert list(ath.load(p)["matches"]) == ["ok"]


def test_doubles_record_survives_save_then_load(tmp_path):
    p = tmp_path / "athletes.json"
    data, ids = _with_four()
    record = _doubles({"P1": {"athlete_id": ids[0]}, "P2": {"name": "田中"}},
                      result={"winner_team": 2, "sets": [[18, 21]]})
    data = ath.set_match(data, "clip", ath.validate_match(record, data)[0])
    ath.save(data, p)
    assert ath.load(p) == data


# ------------------------------------------------------ split matches (links)

def _linked(sides_swapped=False, *, primary="p1", part="p2"):
    """A store with a singles primary record and one linked part."""
    data, tanaka, ito = _with_two_athletes()
    record = {"date": "2026-10-11", "upper": {"athlete_id": tanaka}, "lower": {"athlete_id": ito},
              "result": {"winner": "upper", "sets": [[21, 18], [15, 21], [21, 19]]}}
    data = ath.set_match(data, primary, ath.validate_match(record, data, primary)[0])
    link = {"part_of": primary, "sides_swapped": sides_swapped}
    clean, err = ath.validate_match(link, data, part)
    assert err is None, err
    return ath.set_match(data, part, clean), tanaka, ito


def test_validate_match_accepts_a_link_and_defaults_the_swap_flag():
    data, _, _ = _linked()
    clean, err = ath.validate_match({"part_of": "p1"}, data, "p3")
    assert err is None
    assert clean == {"part_of": "p1", "sides_swapped": False}
    clean, err = ath.validate_match({"part_of": "p1", "sides_swapped": True}, data, "p3")
    assert (clean, err) == ({"part_of": "p1", "sides_swapped": True}, None)


def test_a_third_part_may_point_at_the_same_primary():
    data, _, _ = _linked()
    clean, _err = ath.validate_match({"part_of": "p1"}, data, "p3")
    data = ath.set_match(data, "p3", clean)
    assert [p["stem"] for p in ath.parts_of(data, "p1")] == ["p2", "p3"]


@pytest.mark.parametrize("link,stem", [
    ({"part_of": "p2"}, "p2"),                       # itself
    ({"part_of": "ghost"}, "p3"),                    # primary has no record
    ({"part_of": ""}, "p3"),                         # empty
    ({"part_of": 5}, "p3"),                          # not a string
    ({"part_of": ["p1"]}, "p3"),                     # unhashable
    ({"part_of": None}, "p3"),
    ({"sides_swapped": True}, "p3"),                 # no target at all
    ({"part_of": "p1", "sides_swapped": "yes"}, "p3"),   # not a bool
    ({"part_of": "p1", "sides_swapped": 1}, "p3"),       # ints are not bools
    ({"part_of": "p2"}, "p3"),                       # chain: p2 is itself a part
])
def test_validate_match_rejects_an_invalid_link(link, stem):
    data, _, _ = _linked()
    clean, err = ath.validate_match(link, data, stem)
    assert (clean, err) == (None, "invalid_link")


def test_validate_match_rejects_a_doubles_primary():
    data, ids = _with_four()
    data = ath.set_match(data, "dbl", ath.validate_match(_doubles({"P1": {"name": "A"}}), data)[0])
    assert ath.validate_match({"part_of": "dbl"}, data, "p2") == (None, "invalid_link")


@pytest.mark.parametrize("extra", [{"date": "2026-10-11"}, {"upper": None}, {"lower": None},
                                   {"result": None}, {"players": {}}, {"format": "singles"},
                                   {"format": "doubles"}, {"note": "x"}])
def test_a_link_record_carries_no_other_key(extra):
    data, _, _ = _linked()
    clean, err = ath.validate_match({"part_of": "p1", **extra}, data, "p3")
    assert (clean, err) == (None, "invalid_record")


def test_linking_over_a_video_with_its_own_record_is_part_has_record():
    data, _, _ = _linked()
    data = ath.set_match(data, "p3", {"date": "2026-10-12"})
    assert ath.validate_match({"part_of": "p1"}, data, "p3") == (None, "part_has_record")


def test_linking_over_a_blank_or_an_existing_link_is_allowed():
    data, _, _ = _linked()
    assert ath.validate_match({"part_of": "p1"}, data, "never-seen")[1] is None
    data = ath.set_match(data, "p0", {"date": "2026-10-01"})
    clean, err = ath.validate_match({"part_of": "p0", "sides_swapped": True}, data, "p2")
    assert err is None                      # p2 is linked to p1 today; re-pointing it is fine
    data = ath.set_match(data, "p2", clean)
    assert ath.get_match(data, "p2") == {"part_of": "p0", "sides_swapped": True}


def test_a_primary_with_parts_cannot_become_a_part_or_a_doubles_record():
    data, _, _ = _linked()
    assert ath.validate_match({"part_of": "p1"}, data, "p1")[1] == "invalid_link"
    data = ath.set_match(data, "p9", {"date": "2026-10-12"})
    assert ath.validate_match({"part_of": "p9"}, data, "p1")[1] == "has_parts"
    assert ath.validate_match(_doubles(), data, "p1") == (None, "has_parts")
    assert ath.validate_match(_doubles(), data, "p2")[1] is None      # a part may become doubles
    assert ath.validate_match(_doubles(), data)[1] is None            # no stem: no parts check


def test_a_primary_with_parts_may_get_a_new_singles_record():
    data, tanaka, _ = _linked()
    clean, err = ath.validate_match({"date": "2026-10-20", "upper": {"name": "Z"}}, data, "p1")
    assert err is None
    data = ath.set_match(data, "p1", clean)
    assert [p["stem"] for p in ath.parts_of(data, "p1")] == ["p2"]


def test_validate_match_link_does_not_mutate_its_arguments():
    data, _, _ = _linked()
    link = {"part_of": "p1", "sides_swapped": True}
    before_data, before_link = copy.deepcopy(data), copy.deepcopy(link)
    ath.validate_match(link, data, "p3")
    assert data == before_data and link == before_link


def test_set_match_writes_a_link_and_a_blank_clears_it():
    data, _, _ = _linked(True)
    assert data["matches"]["p2"] == {"part_of": "p1", "sides_swapped": True}
    assert ath.get_match(data, "p2") == {"part_of": "p1", "sides_swapped": True}
    cleared = ath.set_match(data, "p2", {})
    assert "p2" not in cleared["matches"] and "p1" in cleared["matches"]
    assert "p2" not in ath.set_match(data, "p2", None)["matches"]


def test_a_link_is_never_blank():
    assert not ath._is_blank({"part_of": "p1", "sides_swapped": False})


def test_clearing_a_primary_unlinks_its_parts():
    data, _, _ = _linked()
    data = ath.set_match(data, "p3", ath.validate_match({"part_of": "p1"}, data, "p3")[0])
    out = ath.set_match(data, "p1", None)
    assert out["matches"] == {}
    assert ath.has_parts(data, "p1") and not ath.has_parts(out, "p1")


def test_set_match_refuses_to_orphan_parts_with_a_doubles_record():
    data, _, _ = _linked()
    with pytest.raises(ValueError, match="^has_parts$"):
        ath.set_match(data, "p1", _doubles())
    with pytest.raises(ValueError, match="^has_parts$"):
        ath.set_match(data, "p1", {"part_of": "p9", "sides_swapped": False})


def test_set_match_does_not_mutate_its_argument_when_unlinking():
    data, _, _ = _linked()
    before = copy.deepcopy(data)
    ath.set_match(data, "p1", None)
    assert data == before


def test_resolve_record_of_an_unlinked_video_is_its_record_or_empty():
    data, _, _ = _linked()
    assert ath.resolve_record(data, "p1") == data["matches"]["p1"]
    assert ath.resolve_record(data, "nope") == {}
    assert ath.resolve_record(data, "p1") is not data["matches"]["p1"]     # a copy


def test_resolve_record_of_a_part_is_the_primarys_record_plus_the_link():
    data, tanaka, ito = _linked(False)
    got = ath.resolve_record(data, "p2")
    assert got == {**data["matches"]["p1"], "part_of": "p1", "sides_swapped": False}
    assert got["upper"] == {"athlete_id": tanaka} and got["result"]["winner"] == "upper"


def test_resolve_record_swaps_sides_and_the_winner_but_not_sets_or_date():
    data, tanaka, ito = _linked(True)
    primary = copy.deepcopy(data["matches"]["p1"])
    got = ath.resolve_record(data, "p2")
    assert got["upper"] == {"athlete_id": ito} and got["lower"] == {"athlete_id": tanaka}
    assert got["result"] == {"winner": "lower", "sets": primary["result"]["sets"]}
    assert got["date"] == "2026-10-11"
    assert (got["part_of"], got["sides_swapped"]) == ("p1", True)
    assert data["matches"]["p1"] == primary                 # the stored primary is untouched
    assert ath.side_athlete(data, got, "upper")["athlete_id"] == ito


def test_resolve_record_swaps_a_missing_winner_and_missing_result_safely():
    data, _, _ = _linked(True)
    data["matches"]["p1"]["result"] = None
    assert ath.resolve_record(data, "p2")["result"] is None
    data["matches"]["p1"]["result"] = {"winner": "nobody", "sets": []}
    assert ath.resolve_record(data, "p2")["result"] == {"winner": "nobody", "sets": []}


@pytest.mark.parametrize("matches", [
    {"p2": {"part_of": "ghost", "sides_swapped": False}},                        # dangling
    {"p2": {"part_of": "p2", "sides_swapped": False}},                           # self
    {"p1": {"part_of": "p2"}, "p2": {"part_of": "p1"}},                          # cycle
    {"p1": {"part_of": "p0"}, "p0": {"date": "2026-10-11"}, "p2": {"part_of": "p1"}},   # chain
    {"p1": {"format": "doubles", "players": {}}, "p2": {"part_of": "p1"}},       # doubles primary
    {"p1": "junk", "p2": {"part_of": "p1"}},                                     # hostile primary
    {"p2": {"part_of": ["p1"], "sides_swapped": "x"}},                           # hostile link
    {"p2": {"part_of": None}},
])
def test_resolve_record_of_a_broken_link_is_empty_and_never_raises(matches):
    data = ath.empty()
    data["matches"] = matches
    assert ath.resolve_record(data, "p2") == {}
    assert ath.parts_of(data, "p1") == []


def test_resolve_record_is_total_on_a_hostile_store():
    for data in ({}, {"matches": None}, {"matches": []}, {"matches": {"p": 5}}):
        assert ath.resolve_record(data, "p") == {}
        assert ath.parts_of(data, "p") == []
        assert ath.has_parts(data, "p") is False
    assert ath.resolve_record(ath.empty(), ["unhashable"]) == {}


def test_parts_of_lists_the_parts_in_stem_order_with_their_swap_flag():
    data, _, _ = _linked(True, part="b-part")
    clean, _err = ath.validate_match({"part_of": "p1"}, data, "a-part")
    data = ath.set_match(data, "a-part", clean)
    assert ath.parts_of(data, "p1") == [{"stem": "a-part", "sides_swapped": False},
                                        {"stem": "b-part", "sides_swapped": True}]
    assert ath.parts_of(data, "a-part") == [] and ath.parts_of(data, "nope") == []


def test_match_view_adds_parts_to_a_primary_and_the_link_to_a_part():
    data, _, _ = _linked(True)
    primary = ath.match_view(data, "p1")
    assert primary["parts"] == [{"stem": "p2", "sides_swapped": True}]
    assert "part_of" not in primary
    part = ath.match_view(data, "p2")
    assert (part["part_of"], part["sides_swapped"]) == ("p1", True) and "parts" not in part
    plain = ath.set_match(ath.empty(), "solo", {"date": "2026-10-11"})
    assert ath.match_view(plain, "solo") == ath.get_match(plain, "solo")
    assert ath.match_view(plain, "nope") == {}


def test_links_reference_no_athlete_so_counts_stay_per_match():
    data, tanaka, ito = _linked()
    assert ath.record_athlete_ids(data["matches"]["p2"]) == set()
    data, rewritten = ath.delete_athlete(data, tanaka)
    assert rewritten == 1 and data["matches"]["p2"] == {"part_of": "p1", "sides_swapped": False}


def test_a_link_survives_save_then_load_and_keeps_its_order(tmp_path):
    p = tmp_path / "athletes.json"
    data, _, _ = _linked(True)
    ath.save(data, p)
    loaded = ath.load(p)
    assert loaded == data and list(loaded["matches"]) == ["p1", "p2"]
    assert ath.load_for_update(p) == data


def test_load_keeps_a_dangling_link_but_drops_a_malformed_one(tmp_path):
    p = tmp_path / "athletes.json"
    _write_store(p, {"dangling": {"part_of": "ghost", "sides_swapped": False},
                     "bare": {"part_of": "ghost"},
                     "extra": {"part_of": "ghost", "date": "2026-10-11"},
                     "self": {"part_of": "self"},
                     "empty": {"part_of": ""},
                     "badflag": {"part_of": "ghost", "sides_swapped": "yes"},
                     "ok": {"date": "2026-10-11"}})
    loaded = ath.load(p)
    assert loaded["matches"] == {"dangling": {"part_of": "ghost", "sides_swapped": False},
                                 "bare": {"part_of": "ghost", "sides_swapped": False},
                                 "ok": {"date": "2026-10-11", "upper": None, "lower": None,
                                        "result": None}}
    assert ath.resolve_record(loaded, "dangling") == {}


def test_link_over_a_primary_with_parts_reports_has_parts():
    """Not part_has_record: clearing the primary's record would silently unlink its parts."""
    data, _, _ = _linked()
    data = ath.set_match(data, "p9", {"date": "2026-10-12"})
    assert ath.validate_match({"part_of": "p9"}, data, "p1") == (None, "has_parts")
    assert ath.validate_match({"part_of": "p9"}, data, "p3") == ({"part_of": "p9",
                                                                  "sides_swapped": False}, None)
