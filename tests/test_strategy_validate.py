"""Strategy prompts, schema and the claim validator."""
import json

from badminton_analysis.strategy.prompts import (LLM_SECTIONS, report_schema, system_prompt,
                                                 user_message)
from badminton_analysis.strategy.validate import validate_report


def fact(fid, value, unit=None, status="ok"):
    return {"id": fid, "value": value, "unit": unit, "who": "match", "status": status,
            "reason": None if status != "unavailable" else "no_data", "validated": True}


PACK = {"format": "singles", "focus": {}, "header": {}, "inputs": {"x": 1}, "caveats": [],
        "facts": [fact("match.rally_median_sec", 6.27, "s"),
                  fact("you.mix.attack_share", 0.42, "share"),
                  fact("you.pulled.n", 20, "count"),
                  fact("match.result", {"winner": "you", "sets": [[30, 4]]}),
                  fact("you.serve.short_share", None, "share", "unavailable")]}


def claim(text, *ids):
    return {"text": text, "evidence": list(ids)}


def three(text="Calm.", *ids):
    return {"claims": [claim(text, *(ids or ("match.rally_median_sec",)))] * 3}


def good_doc(fmt="singles"):
    c = claim("You attack often.", "you.mix.attack_share")
    game = {"shot_selection": {"claims": [c]}, "movement": {"claims": [c]},
            "patterns": {"claims": [c]}, "strengths": {"claims": [c, c]},
            "weaknesses": {"claims": [c, c]}}
    if fmt == "doubles":
        del game["shot_selection"]
    item = {"do": "Smash more.", "why": "It wins.", "cue": "Hit up.",
            "evidence": ["you.mix.attack_share"]}
    return {"summary": three(), "match_profile": {"claims": [c, c]}, "your_game": game,
            "game_plan": {"items": [item] * 3, "training_focus": None},
            "scouting": {"claims": [c, c]}}


def check(doc, fmt="singles", pack=PACK):
    return validate_report(doc, pack, fmt)


def with_summary(text, *ids):
    doc = good_doc()
    doc["summary"] = {"claims": [claim(text, *ids)] * 3}
    return doc


def test_valid_report_has_no_errors():
    assert check(good_doc()) == {}


def test_invented_number():
    errors = check(with_summary("Attack 55% of the time.", "you.mix.attack_share"))
    assert list(errors) == ["summary"]
    assert "number 55% in summary[0] not in cited facts" in errors["summary"][0]


def test_percent_of_share_ok():
    assert check(with_summary("Attack 42% of strokes.", "you.mix.attack_share")) == {}
    assert check(with_summary("Attack 42 percent of strokes.", "you.mix.attack_share")) == {}


def test_rounding_ok():
    assert check(with_summary("Rallies last 6.3 s.", "match.rally_median_sec")) == {}
    assert "summary" in check(with_summary("Rallies last 6.4 s.", "match.rally_median_sec"))
    assert check(with_summary("Rallies last 6 s.", "match.rally_median_sec")) == {}


def test_number_must_come_from_cited_facts():
    errors = check(with_summary("20 pulled shots.", "match.rally_median_sec"))
    assert "summary" in errors
    assert check(with_summary("20 pulled shots.", "you.pulled.n")) == {}


def test_ordinal_exempt():
    assert check(with_summary("3 things matter, first 1 then 2.", "you.pulled.n")) == {}
    assert "summary" in check(with_summary("4 things matter.", "you.pulled.n"))


def test_identifier_digits_are_not_numbers():
    assert check(with_summary("P4 covers the rear.", "you.pulled.n")) == {}


def test_unknown_evidence_id():
    errors = check(with_summary("Fine.", "no.such.fact"))
    assert "no.such.fact" in errors["summary"][0]


def test_evidence_not_list():
    doc = good_doc()
    doc["summary"] = {"claims": [{"text": "Fine.", "evidence": "match.rallies"}] * 3}
    assert "summary" in check(doc)
    doc["summary"] = {"claims": [{"text": "Fine.", "evidence": 5}] * 3}
    assert "summary" in check(doc)
    doc["summary"] = {"claims": [{"text": "Fine.", "evidence": []}] * 3}
    assert "summary" in check(doc)
    doc["summary"] = {"claims": [{"text": "Fine.", "evidence": [7]}] * 3}
    assert "summary" in check(doc)


def test_claim_count_bounds():
    doc = good_doc()
    doc["summary"] = {"claims": [claim("Fine.", "you.pulled.n")] * 2}
    assert "summary" in check(doc)
    doc = good_doc()
    doc["your_game"]["strengths"] = {"claims": [claim("Fine.", "you.pulled.n")]}
    assert "your_game" in check(doc)
    doc = good_doc()
    doc["game_plan"]["items"] = doc["game_plan"]["items"][:2]
    assert "game_plan" in check(doc)


def test_fullwidth_digits():
    assert check(with_summary("命中率４２％", "you.mix.attack_share")) == {}
    assert "summary" in check(with_summary("命中率５５％", "you.mix.attack_share"))


def test_set_scores_from_list_value():
    assert check(with_summary("You won 30-4.", "match.result")) == {}
    assert "summary" in check(with_summary("You won 30-5.", "match.result"))


def test_unavailable_fact_needs_no_data_phrase():
    assert check(with_summary("Not enough data on serves.", "you.serve.short_share")) == {}
    assert "summary" in check(with_summary("Short serves are 30% of serves.",
                                           "you.serve.short_share"))


def test_doubles_schema_has_no_shot_selection():
    singles = report_schema("singles")["properties"]["your_game"]["properties"]
    doubles = report_schema("doubles")["properties"]["your_game"]["properties"]
    assert "shot_selection" in singles and "shot_selection" not in doubles
    assert check(good_doc("doubles"), "doubles") == {}
    assert "your_game" in check(good_doc("singles"), "doubles")      # extra key
    assert "your_game" in check(good_doc("doubles"), "singles")      # missing key


def test_extra_key_rejected():
    doc = good_doc()
    doc["summary"]["claims"][0] = {"text": "Fine.", "evidence": ["you.pulled.n"], "x": 1}
    assert "summary" in check(doc)
    doc = good_doc()
    doc["scouting"]["extra"] = 1
    assert "scouting" in check(doc)
    doc = good_doc()
    doc["surprise"] = {}
    assert check(doc)


def test_game_plan_numbers_and_training_focus():
    doc = good_doc()
    doc["game_plan"]["items"][0] = {"do": "Smash 55% more.", "why": "x", "cue": "y",
                                    "evidence": ["you.mix.attack_share"]}
    assert "game_plan" in check(doc)
    doc = good_doc()
    doc["game_plan"]["training_focus"] = claim("Drill clears.", "you.pulled.n")
    assert check(doc) == {}
    doc["game_plan"]["training_focus"] = claim("Drill 99 clears.", "you.pulled.n")
    assert "game_plan" in check(doc)


def test_fact_id_in_a_prose_field_is_rejected():
    # Seen live (qwen3.8:27b, zh): the cue held the evidence id instead of a cue.
    doc = good_doc()
    doc["game_plan"]["items"][0] = {"do": "增加进攻比例。", "why": "进攻少。",
                                    "cue": "you.mix.attack_share", "evidence": ["you.mix.attack_share"]}
    errors = check(doc)
    assert any("cue must be coaching text" in e for e in errors["game_plan"])
    assert "summary" in check(with_summary("you.pulled.n", "you.pulled.n"))


def test_malformed_documents_never_raise():
    for doc in (None, [], "x", {"summary": "x"}, {"summary": None, "your_game": []}):
        errors = check(doc)
        assert errors and set(errors) <= set(LLM_SECTIONS) | {"_root"}
    assert check(good_doc(), pack=None) is not None


def test_schema_and_prompts():
    schema = report_schema("singles")
    assert schema["additionalProperties"] is False
    assert list(schema["properties"]) == list(LLM_SECTIONS)
    summary = schema["properties"]["summary"]["properties"]["claims"]
    assert summary["minItems"] == summary["maxItems"] == 3
    evidence = summary["items"]["properties"]["evidence"]
    assert evidence["minItems"] == 1
    json.dumps(schema)
    zh = system_prompt("zh", "doubles")
    assert "简体中文" in zh and "未经验证" in zh
    en = system_prompt("en", "doubles")
    assert "estimated (not validated)" in en and "80 words" in en
    assert "estimated (not validated)" not in system_prompt("en", "singles")
    msg = json.loads(user_message(PACK))
    assert set(msg) == {"format", "focus", "header", "facts"}


def _facts_pack(*facts):
    pack = dict(PACK)
    pack["facts"] = list(PACK["facts"]) + list(facts)
    return pack


def test_cjk_numerals_checked():
    for text in ("你的进攻球占比约三成", "百分之九十", "3成"):
        errors = check(with_summary(text, "you.mix.attack_share"), pack=PACK_SHARE)
        assert "summary" in errors, text
    errors = check(with_summary("百分之九十", "you.mix.attack_share"), pack=PACK_SHARE)
    assert "write numbers as digits in summary[0]" in errors["summary"]


PACK_SHARE = dict(PACK, facts=[f for f in PACK["facts"] if f["id"] != "you.mix.attack_share"]
                  + [fact("you.mix.attack_share", 0.452, "share")])


def test_number_words_checked():
    errors = check(with_summary("You smashed ninety times.", "you.pulled.n"))
    assert "write numbers as digits in summary[0]" in errors["summary"]
    assert "summary" in check(with_summary("Twenty-five smashes.", "you.pulled.n"))
    assert "summary" in check(with_summary("HUNDRED smashes.", "you.pulled.n"))
    assert check(with_summary("The fourth rally was long.", "you.pulled.n")) == {}


def test_small_cjk_numerals_pass():
    assert check(with_summary("一些球，两个区域，三种选择。", "you.pulled.n")) == {}


def test_everyday_numeral_words_pass():
    assert check(with_summary("你的网前十分积极，万一被压后场要先回中。", "you.pulled.n")) == {}
    assert "summary" in check(with_summary("十分之九的球在后场。", "you.pulled.n"))


def test_rally_definition_numbers_ok():
    short = fact("match.short_rally_share", 0.3, "share")
    short["defs"] = [5]
    pack = _facts_pack(short)
    ok = with_summary("30% of rallies have 5 shots or fewer.", "match.short_rally_share")
    assert check(ok, pack=pack) == {}
    bad = with_summary("30% of rallies have 6 shots or fewer.", "match.short_rally_share")
    assert "summary" in check(bad, pack=pack)


def test_unavailable_with_player_id_ok():
    ids = ("you.serve.short_share",)
    assert check(with_summary("Not enough data on P1's hit share.", *ids)) == {}
    assert check(with_summary("P1 的击球占比数据不足。", *ids)) == {}
    assert "summary" in check(with_summary("Only 30 serves, not enough data.", *ids))


def test_glued_count_not_exempt():
    assert "summary" in check(with_summary("smash x99 happened.", "you.pulled.n"))
    assert "summary" in check(with_summary("It was 1e5 times.", "you.pulled.n"))
    seq = fact("you.seq2_top", {"a": 1})
    pack = _facts_pack(seq)
    assert check(with_summary("Your seq2 reply is key.", "you.seq2_top"), pack=pack) == {}
    assert "summary" in check(with_summary("Your seq2 reply is key.", "you.pulled.n"), pack=pack)


def test_percent_after_small_int_checked():
    assert "summary" in check(with_summary("Attack 3% of the time.", "you.pulled.n"))


def test_prompts_ask_for_digits():
    assert "Arabic digits" in system_prompt("en", "singles")
    assert "阿拉伯数字" in system_prompt("zh", "singles")


def test_tactics_facts_validate_with_their_numbers():
    eff = fact("you.eff.smash", {"effective": 7, "neutral": 3, "ineffective": 2, "unknown": 1,
                                 "n": 13}, "count")
    pack = dict(PACK, facts=PACK["facts"] + [eff])
    ok = with_summary("Of 13 smashes, 7 were effective and 2 were not.", "you.eff.smash")
    assert check(ok, pack=pack) == {}
    bad = with_summary("Of 13 smashes, 8 were effective.", "you.eff.smash")
    assert list(check(bad, pack=pack)) == ["summary"]


def test_user_message_keeps_tables_out_and_prompt_names_the_tally():
    pack = dict(PACK, tables={"effectiveness": {"you": {}}, "openings": {}})
    body = json.loads(user_message(pack))
    assert set(body) == {"format", "focus", "header", "facts"}
    for lang in ("en", "zh"):
        text = system_prompt(lang, "singles")
        assert "eff." in text and "openings." in text
