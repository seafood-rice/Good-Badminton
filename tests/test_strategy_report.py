"""Strategy report generation, storage, Markdown and the CLI (stub LLM server only)."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from badminton_analysis.library import athletes as athlete_store
from badminton_analysis.strategy.config import StrategyConfig
from badminton_analysis.strategy.facts import build_facts
from badminton_analysis.strategy.prompts import LLM_SECTIONS
from badminton_analysis.strategy.report import (REPORT_FILE, fallback_sections, generate,
                                                is_stale, plain_sections, read_report,
                                                render_markdown, write_atomic)
from badminton_analysis.strategy.validate import validate_report
from tests.insights_fixtures import doubles_hit, make_doubles_run, make_run
from tests.strategy_stub import chat_reply, stub  # noqa: F401  (pytest fixture)

ROOT = Path(__file__).resolve().parent.parent
MODEL = "qwen3.8:27b"
STORE = {"athletes": {"a1": {"name": "Val"}}, "matches": {}}
RECORD = {"upper": {"name": "Sally"}, "lower": {"athlete_id": "a1"},
          "result": {"winner": "lower", "sets": [[4, 30]]}}
STROKES = [("upper", "clear", 10), ("lower", "smash", 20), ("upper", "lift", 200),
           ("lower", "smash", 230)]


def cfg(url, langs=("en", "zh")):
    return StrategyConfig(enabled=True, url=url, model=MODEL, key="ollama", timeout=5.0,
                          langs=langs)


def singles_dir(tmp_path):
    return make_run(tmp_path, rallies=[(0, 5), (6, 20)], strokes=STROKES, frames=700)


def valid_doc(pack, lang):
    return plain_sections(fallback_sections(pack, lang, pack["format"]))


def serve(stub, pack, *, mutate=None):
    """Queue one router: the model list on GET, a valid (or ``mutate``d) report on POST."""
    calls = {}

    def router(body):
        if body is None:
            return {"data": [{"id": MODEL}]}
        lang = "zh" if "简体中文" in body["messages"][0]["content"] else "en"
        calls[lang] = calls.get(lang, 0) + 1
        doc = valid_doc(pack, lang)
        if mutate is not None:
            doc = mutate(doc, lang, calls[lang])
        return chat_reply(doc)

    stub.respond(200, router)
    return calls


@pytest.fixture
def run(tmp_path):
    out = singles_dir(tmp_path)
    return out, build_facts(out, RECORD, STORE)


def test_fallback_sections_validate_and_are_marked(run, tmp_path):
    _out, pack = run
    sections = fallback_sections(pack, "en", "singles")
    assert set(sections) == set(LLM_SECTIONS)
    assert all(s["llm"] is False and s["status"] == "fallback" for s in sections.values())
    assert validate_report(plain_sections(sections), pack, "singles") == {}
    zh = fallback_sections(pack, "zh", "singles")
    assert validate_report(plain_sections(zh), pack, "singles") == {}
    assert any("回合" in c["text"] for c in zh["match_profile"]["claims"])


def test_fallback_sections_for_doubles_and_empty_runs(tmp_path):
    doubles = make_doubles_run(tmp_path, hits=[doubles_hit(10, "P1"), doubles_hit(20, "P2")])
    rec = {"format": "doubles", "players": {"P1": {"athlete_id": "a1"}},
           "result": {"winner_team": 1, "sets": [[21, 10]]}}
    pack = build_facts(doubles, rec, STORE)
    for lang in ("en", "zh"):
        assert validate_report(plain_sections(fallback_sections(pack, lang, "doubles")),
                               pack, "doubles") == {}
    empty = tmp_path / "outputs" / "empty"
    empty.mkdir(parents=True)
    pack = build_facts(empty, {}, STORE)
    for lang in ("en", "zh"):
        assert validate_report(plain_sections(fallback_sections(pack, lang, "singles")),
                               pack, "singles") == {}
        text = json.dumps(fallback_sections(pack, lang, "singles"), ensure_ascii=False)
        assert "match.result" in text


def test_generate_ok_both_langs(run, stub):
    out, pack = run
    calls = serve(stub, pack)
    doc = generate(out, RECORD, STORE, cfg(stub.url))
    assert doc["status"] == "ok" and doc["reason"] is None
    assert doc["schema"] == 1 and doc["format"] == "singles" and doc["model"] == MODEL
    assert doc["endpoint"] == stub.url
    assert set(doc["by_lang"]) == {"en", "zh"} and calls == {"en": 1, "zh": 1}
    for lang in ("en", "zh"):
        report = doc["by_lang"][lang]
        assert list(report) == ["header", "summary", "match_profile", "your_game", "game_plan",
                                "scouting", "caveats"]
        assert all(report[s]["llm"] is True and report[s]["status"] == "ok"
                   for s in LLM_SECTIONS)
        assert report["header"]["llm"] is False and report["header"]["you"] == "Val"
        assert report["caveats"]["items"] == pack["caveats"]
    assert doc["facts"] == pack["facts"] and doc["inputs"] == pack["inputs"]
    assert doc["focus"] == pack["focus"]
    assert len(stub.chat_requests()) == 2
    assert stub.requests[0]["path"].endswith("/models")
    json.dumps(doc)


def test_retry_then_ok(run, stub):
    out, pack = run

    def mutate(doc, lang, n):
        if n == 1:
            doc["summary"]["claims"][0]["text"] = "Attack 99% of the time."
            doc["summary"]["claims"][0]["evidence"] = ["match.rallies"]
        return doc

    calls = serve(stub, pack, mutate=mutate)
    doc = generate(out, RECORD, STORE, cfg(stub.url))
    assert doc["status"] == "ok" and calls == {"en": 2, "zh": 2}
    retry = stub.chat_requests()[1]["body"]["messages"]
    assert [m["role"] for m in retry] == ["system", "user", "assistant", "user"]
    assert "Fix these problems" in retry[3]["content"] and "99" in retry[3]["content"]


def test_retry_then_section_fallback(run, stub):
    out, pack = run

    def mutate(doc, lang, n):
        doc["scouting"]["claims"][0]["evidence"] = ["no.such.fact"]
        return doc

    serve(stub, pack, mutate=mutate)
    doc = generate(out, RECORD, STORE, cfg(stub.url))
    assert doc["status"] == "partial"
    assert doc["reason"] == {"code": "sections_fallback",
                             "params": {"en": ["scouting"], "zh": ["scouting"]}}
    en = doc["by_lang"]["en"]
    assert en["scouting"]["llm"] is False and en["scouting"]["status"] == "fallback"
    assert en["summary"]["llm"] is True


def test_evidence_not_list_falls_back_without_crash(run, stub):
    out, pack = run

    def mutate(doc, lang, n):
        doc["summary"]["claims"][0]["evidence"] = "match.rallies"
        return doc

    serve(stub, pack, mutate=mutate)
    doc = generate(out, RECORD, STORE, cfg(stub.url, langs=("en",)))
    assert doc["status"] == "partial"
    assert doc["by_lang"]["en"]["summary"]["llm"] is False
    assert doc["by_lang"]["en"]["summary"]["claims"]


def test_extra_top_level_key_is_dropped_without_retry(run, stub):
    out, pack = run

    def mutate(doc, lang, n):
        doc["notes"] = "hello"
        return doc

    calls = serve(stub, pack, mutate=mutate)
    doc = generate(out, RECORD, STORE, cfg(stub.url, langs=("en",)))
    assert doc["status"] == "ok" and calls == {"en": 1}
    assert "notes" not in doc["by_lang"]["en"]


def test_not_an_object_reply_falls_back(run, stub):
    out, pack = run
    stub.respond(200, lambda body: {"data": [{"id": MODEL}]} if body is None
                 else {"choices": [{"message": {"content": "[1, 2]"}}]})
    doc = generate(out, RECORD, STORE, cfg(stub.url, langs=("en",)))
    assert doc["status"] == "failed" and doc["reason"]["code"] == "bad_response"
    assert doc["by_lang"]["en"]["summary"]["llm"] is False


def test_unreachable_gives_failed_with_fallback(run):
    out, _pack = run
    doc = generate(out, RECORD, STORE, cfg("http://127.0.0.1:9/v1"))
    assert doc["status"] == "failed"
    assert doc["reason"] == {"code": "llm_unreachable", "params": {}}
    for lang in ("en", "zh"):
        assert doc["by_lang"][lang]["summary"]["claims"]
        assert doc["by_lang"][lang]["summary"]["llm"] is False


def test_model_missing_failed(run, stub):
    out, _pack = run
    stub.respond(200, {"data": [{"id": "gemma4:31b"}]})
    doc = generate(out, RECORD, STORE, cfg(stub.url))
    assert doc["status"] == "failed"
    assert doc["reason"] == {"code": "model_missing", "params": {"model": MODEL}}
    assert not stub.chat_requests()
    assert doc["by_lang"]["zh"]["summary"]["llm"] is False


def test_one_language_failing_keeps_the_other(run, stub):
    out, pack = run

    def router(body):
        if body is None:
            return {"data": [{"id": MODEL}]}
        if "简体中文" in body["messages"][0]["content"]:
            return {"choices": []}
        return chat_reply(valid_doc(pack, "en"))

    stub.respond(200, router)
    doc = generate(out, RECORD, STORE, cfg(stub.url))
    assert doc["status"] == "failed" and doc["reason"]["code"] == "bad_response"
    assert doc["by_lang"]["en"]["summary"]["llm"] is True
    assert doc["by_lang"]["zh"]["summary"]["llm"] is False


def test_generate_never_raises_on_a_missing_dir(tmp_path):
    doc = generate(tmp_path / "nope", None, None, cfg("http://127.0.0.1:9/v1"))
    assert doc["status"] == "failed" and set(doc["by_lang"]) == {"en", "zh"}


def test_write_atomic_and_read(tmp_path):
    doc = {"schema": 1, "by_lang": {"en": {}}, "status": "ok", "text": "中文"}
    write_atomic(tmp_path, doc)
    assert read_report(tmp_path) == doc
    write_atomic(tmp_path, dict(doc, status="partial"))
    assert read_report(tmp_path)["status"] == "partial"
    assert sorted(p.name for p in tmp_path.iterdir()) == [REPORT_FILE]


def test_read_report_malformed_is_none(tmp_path):
    assert read_report(tmp_path) is None
    (tmp_path / REPORT_FILE).write_text("{nope", encoding="utf-8")
    assert read_report(tmp_path) is None
    (tmp_path / REPORT_FILE).write_text("[1]", encoding="utf-8")
    assert read_report(tmp_path) is None
    (tmp_path / REPORT_FILE).write_text(json.dumps({"schema": 1}), encoding="utf-8")
    assert read_report(tmp_path) is None
    assert read_report(tmp_path / "missing") is None


def test_is_stale_after_input_touch(run, stub):
    out, pack = run
    serve(stub, pack)
    doc = generate(out, RECORD, STORE, cfg(stub.url, langs=("en",)))
    assert is_stale(out, doc, RECORD) is False
    path = out / "insights.json"
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert is_stale(out, doc, RECORD) is True


def test_is_stale_after_record_change(run, stub):
    out, pack = run
    serve(stub, pack)
    doc = generate(out, RECORD, STORE, cfg(stub.url, langs=("en",)))
    other = copy.deepcopy(RECORD)
    other["result"]["winner"] = "upper"
    assert is_stale(out, doc, other) is True
    assert is_stale(out, doc, RECORD) is False
    assert is_stale(out, {"inputs": None}, RECORD) is True
    assert is_stale(out, None, RECORD) is True


def test_markdown_has_sections_and_evidence(run, stub):
    out, pack = run
    serve(stub, pack)
    doc = generate(out, RECORD, STORE, cfg(stub.url))
    md = render_markdown(doc, "en")
    for heading in ("## Summary", "## Match profile", "## Your game", "## Game plan",
                    "## Scouting", "## Caveats", "## Evidence"):
        assert heading in md
    evidence = md.split("## Evidence", 1)[1]
    assert "match.rally_median_sec" in evidence
    zh = render_markdown(doc, "zh")
    assert "## 摘要" in zh and "## 证据" in zh
    assert render_markdown(doc, "fr") == md                 # unknown language -> English
    assert render_markdown({}, "en")                        # junk in, text out


def test_markdown_marks_fallback_sections(run):
    out, _pack = run
    doc = generate(out, RECORD, STORE, cfg("http://127.0.0.1:9/v1", langs=("en",)))
    assert "not written by the AI" in render_markdown(doc, "en")


def _cli_files(tmp_path):
    data = athlete_store.empty()
    data, aid = athlete_store.add_athlete(data, "Val")
    out = singles_dir(tmp_path)
    data = athlete_store.set_match(data, out.name, {
        "upper": {"name": "Sally"}, "lower": {"athlete_id": aid},
        "result": {"winner": "lower", "sets": [[4, 30]]}})
    path = tmp_path / "athletes.json"
    athlete_store.save(data, path)
    return out, path, data


def _cli(out, athletes, url, *extra):
    env = dict(os.environ, PYTHONUTF8="1")
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "main_strategy.py"), str(out), "--url", url,
         "--athletes", str(athletes), "--settings", str(out.parent / "none.json"), *extra],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)


def test_cli_writes_report(tmp_path, stub):
    out, athletes, data = _cli_files(tmp_path)
    pack = build_facts(out, athlete_store.resolve_record(data, out.name), data)
    serve(stub, pack)
    proc = _cli(out, athletes, stub.url, "--langs", "en")
    assert proc.returncode == 0, proc.stderr
    doc = read_report(out)
    assert doc["status"] == "ok" and list(doc["by_lang"]) == ["en"]
    assert doc["by_lang"]["en"]["header"]["you"] == "Val"


def test_cli_exit_code_on_failed(tmp_path):
    out, athletes, _data = _cli_files(tmp_path)
    proc = _cli(out, athletes, "http://127.0.0.1:9/v1")
    assert proc.returncode == 2, proc.stderr
    doc = read_report(out)
    assert doc["status"] == "failed" and doc["reason"]["code"] == "llm_unreachable"


def test_cli_missing_dir_exits_1(tmp_path):
    proc = _cli(tmp_path / "nope", tmp_path / "a.json", "http://127.0.0.1:9/v1")
    assert proc.returncode == 1


def test_markdown_escapes_model_text():
    claim = {"text": "<script>alert(1)</script> and [x](http://e) *b* _i_ `c` \\",
             "evidence": ["match.rallies"]}
    doc = {"model": "m_1", "by_lang": {"en": {
        "header": {"you": "<b>Val</b>", "opponent": "A*B"},
        "summary": {"claims": [claim]},
        "game_plan": {"items": [{"do": "<i>x", "why": "[w]", "cue": "_c_",
                                 "evidence": ["match.rallies"]}],
                      "training_focus": dict(claim)}}}}
    md = render_markdown(doc, "en")
    assert "<script>" not in md and "<b>" not in md and "<i>" not in md
    assert r"\<script\>alert(1)\</script\>" in md
    assert r"\[x\](http://e)" in md and r"\*b\*" in md and r"\_i\_" in md
    assert r"\`c\`" in md and "\\\\" in md
    assert r"\<b\>Val\</b\>" in md and r"A\*B" in md and r"\[w\]" in md
    assert r"m\_1" in md


def test_markdown_evidence_wording_follows_lang():
    doc = {"by_lang": {"zh": {"summary": {"claims": [
        {"text": "x", "evidence": ["match.tempo_shots_per_sec", "you.gone"]}]}}},
        "facts": [{"id": "match.tempo_shots_per_sec", "value": 1.2, "unit": "shots_per_s"},
                  {"id": "you.gone", "value": None, "unit": "share", "status": "unavailable"}]}
    zh = render_markdown(doc, "zh").split("## 证据", 1)[1]
    assert "n/a" not in zh and "shots/s" not in zh and "无数据" in zh and "拍/秒" in zh
    en = render_markdown({"by_lang": {"en": doc["by_lang"]["zh"]}, "facts": doc["facts"]}, "en")
    assert "n/a" in en and "shots/s" in en


def test_markdown_renders_focus_assumed_team_caveat():
    doc = {"by_lang": {"en": {"caveats": {"items": [{"code": "focus_assumed_team",
                                                      "params": {}}]}},
                       "zh": {"caveats": {"items": [{"code": "focus_assumed_team",
                                                      "params": {}}]}}}}
    assert "Team 1 is assumed to be your team" in render_markdown(doc, "en")
    assert "默认将第 1 队视为你方" in render_markdown(doc, "zh")
