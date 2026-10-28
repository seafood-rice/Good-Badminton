"""Generate, store and render the strategy report (spec sections 5, 6.3, 6.4).

``generate`` never raises. Per language it makes one LLM call, validates the answer, retries once
with the errors, and swaps any section that still fails for deterministic text built from the
facts (``fallback_sections``, marked ``llm: false``). Status:

- ``failed``: the model check or a language call raised ``LLMError`` (``reason`` is its
  ``{code, params}``); every affected language is all fallback.
- ``partial``: every call worked but some sections fell back
  (``reason = {"code": "sections_fallback", "params": {lang: [section, ...]}}``).
- ``ok``: every section of every language was written by the model.

Document shape: ``by_lang[lang]`` holds, in order, ``header`` and ``caveats`` (code-built;
``caveats`` is ``{"status", "llm", "items": [{code, params}]}``) and the five LLM sections
(``summary``, ``match_profile``, ``your_game``, ``game_plan``, ``scouting``), each
``{"status": "ok"|"fallback", "llm": bool, ...content}``. An unknown top-level key in a model
answer is dropped without a retry.
"""
import copy
import datetime
import json
import os
import re
import tempfile
import time

from . import SCHEMA_VERSION
from .facts import build_facts, current_inputs, fact_map
from .llm import LLMClient, LLMError
from .prompts import (CLAIM_BOUNDS, LLM_SECTIONS, PLAN_BOUNDS, YOUR_GAME_BOUNDS, report_schema,
                      system_prompt, user_message, your_game_keys)
from .validate import validate_report

REPORT_FILE = "strategy_report.json"
_FATAL = ("model_missing", "llm_unreachable", "timeout")
_REPORT_ORDER = ("header", "summary", "match_profile", "your_game", "game_plan", "scouting",
                 "caveats")

# ---------------------------------------------------------------- wording

_WORDS_ZH = {
    "smash": "杀球", "clear": "高远球", "drop": "吊球", "net": "网前球", "lift": "挑球",
    "drive": "平抽", "serve": "发球", "net_shot": "放网", "block": "挡网", "push": "推球",
    "cross_net": "勾对角", "net_rush": "扑球", "short_serve": "发短球",
    "long_serve": "发长球", "unknown": "未知", "front": "前场", "mid": "中场", "rear": "后场",
    "back": "后场", "left": "左", "centre": "中", "right": "右", "winner": "制胜球",
    "forced_error": "被迫失误", "unforced_error": "非受迫失误",
}
_UNIT = {"s": " s", "m": " m", "m_per_s": " m/s", "shots_per_s": " shots/s",
         "per_rally": " per rally", "score": " pts"}


def _word(text, lang):
    text = str(text)
    if lang != "zh":
        return text.replace("_", " ")
    if text in _WORDS_ZH:
        return _WORDS_ZH[text]
    return "".join(_WORDS_ZH.get(part, part) for part in text.split("_"))


def _num(v):
    return ("%.2f" % v).rstrip("0").rstrip(".")


def _fmt_value(value, unit, lang, key=None):
    """A short, number-faithful rendering of a fact value (every number is one the fact holds)."""
    none = "无" if lang == "zh" else "none"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{round(value * 100):d}%" if unit == "share" else _num(value)
    if isinstance(value, str):
        return _word(value, lang)
    if isinstance(value, dict):
        if set(value) == {"winner", "sets"}:
            return _fmt_result(value, lang)
        if not value:
            return none
        return ", ".join(f"{_word(k, lang)} {_fmt_value(v, None, lang)}"
                         for k, v in value.items())
    if isinstance(value, (list, tuple)):
        if not value:
            return none
        return ", ".join(_fmt_item(v, lang) for v in value)
    return none


def _fmt_item(item, lang):
    if isinstance(item, dict) and isinstance(item.get("seq"), list):
        seq = " > ".join(_word(s, lang) for s in item["seq"])
        return f"{seq} ({_fmt_value(item.get('n'), None, lang)})"
    return _fmt_value(item, None, lang)


def _fmt_result(value, lang):
    sets = ", ".join(f"{_num(p[0])}-{_num(p[1])}" for p in value.get("sets") or []
                     if isinstance(p, (list, tuple)) and len(p) == 2)
    won = value.get("winner") == "you"
    if lang == "zh":
        head = "你获胜" if won else "对手获胜"
        return f"{head}（比分 {sets}）" if sets else head
    head = "you won" if won else "the opponent won"
    return f"{head} (sets {sets})" if sets else head


_WHO = {"you": ("Your", "你的"), "opponent": ("The opponent's", "对手的")}
_TEAM = {"you": ("Your team's", "你们队的"), "opponent": ("The opposing team's", "对方队的")}

_MATCH = {
    "match.rallies": ("Rallies played: {v}.", "回合数：{v}。"),
    "match.rally_median_sec": ("Median rally: {v}.", "回合中位时长：{v}。"),
    "match.rally_p90_sec": ("Long rallies last about: {v}.", "较长回合的时长：{v}。"),
    "match.rally_median_shots": ("Median shots per rally: {v}.", "每回合击球数中位数：{v}。"),
    "match.short_rally_share": ("Short rallies (five shots or fewer): {v}.",
                                "短回合（不超过五拍）占比：{v}。"),
    "match.long_rally_share": ("Long rallies (ten shots or more): {v}.",
                               "长回合（十拍及以上）占比：{v}。"),
    "match.tempo_shots_per_sec": ("Tempo: {v}.", "节奏：{v}。"),
    "match.degraded_share": ("Rallies with degraded tracking: {v}.",
                             "跟踪质量下降的回合占比：{v}。"),
    "match.result": ("Result: {v}.", "结果：{v}。"),
    "match.outcome_coverage": ("Rallies with a known winner: {v}.", "已知胜者的回合占比：{v}。"),
    "match.active_play_sec": ("Time the shuttle was in play: {v}.", "实际比赛时间：{v}。"),
    "match.rallies_under_9s_share": ("Rallies shorter than 9 seconds: {v}.",
                                     "短于 9 秒的回合占比：{v}。"),
    "match.shots_per_rally": ("Average shots per rally: {v}.", "每回合平均击球数：{v}。"),
    "match.owner_labelled_share": ("Shots whose type was set by the owner: {v}.",
                                   "击球类型由本人标注的比例：{v}。"),
    "match.openings.complete": ("Points whose first three shots were fully detected "
                                "(3 shots): {v}.", "前三拍完整识别的得分回合（3 拍）：{v}。"),
    "match.openings.total": ("Points counted for openings: {v}.", "统计开局的回合数：{v}。"),
}
_SIDE = {
    "mix.attack_share": ("{W} attacking strokes (smash, drive, drop), share of non-serve "
                         "strokes: {v}.", "{W}进攻球（杀球、平抽、吊球）占非发球击球的比例：{v}。"),
    "mix.neutral_share": ("{W} net-play strokes, share of non-serve strokes: {v}.",
                          "{W}网前过渡球占非发球击球的比例：{v}。"),
    "mix.defence_share": ("{W} defensive strokes (clear, lift, block), share of non-serve "
                          "strokes: {v}.", "{W}防守球（高远、挑球、挡网）占非发球击球的比例：{v}。"),
    "serve.count": ("{W} serves: {v}.", "{W}发球次数：{v}。"),
    "serve.short_share": ("{W} short serves, share of serves: {v}.",
                          "{W}发短球占发球的比例：{v}。"),
    "net_share": ("{W} net strokes, share of non-serve strokes: {v}.",
                  "{W}网前球占非发球击球的比例：{v}。"),
    "unknown_share": ("{W} strokes that could not be classified: {v}.",
                      "{W}无法识别的击球占比：{v}。"),
    "groups": ("{W} stroke counts: {v}.", "{W}击球统计：{v}。"),
    "seq2_top": ("{W} most common reply pairs (opponent stroke, then reply): {v}.",
                 "{W}最常见的接球组合（对手击球，然后回击）：{v}。"),
    "seq3_top": ("{W} most common three-stroke sequences: {v}.", "{W}最常见的三拍组合：{v}。"),
    "after_lift": ("{W} replies to a lift: {v}.", "{W}对挑球的回击：{v}。"),
    "last_stroke": ("{W} rally-ending strokes: {v}.", "{W}结束回合的击球：{v}。"),
    "distance_m": ("{W} distance covered: {v}.", "{W}跑动距离：{v}。"),
    "mean_speed_mps": ("{W} average speed: {v}.", "{W}平均速度：{v}。"),
    "recovery_median_sec": ("{W} median time to recover to base after a hit: {v}.",
                            "{W}击球后回到中心位置的时间中位数：{v}。"),
    "technique_index": ("{W} technique index: {v}.", "{W}技术指数：{v}。"),
    "technique_weak": ("{W} weakest techniques: {v}.", "{W}最弱的技术：{v}。"),
    "points_won": ("{W} points won: {v}.", "{W}得分：{v}。"),
    "won_by": ("{W} points won by ending stroke: {v}.", "{W}得分的终结击球：{v}。"),
    "lost_by": ("{W} points lost by outcome: {v}.", "{W}失分类型：{v}。"),
    "openings.won": ("{W} openings won (advantage after the first three shots): {v}.",
                     "{W}在前三拍中取得优势的次数：{v}。"),
    "opening_win_point": ("{W} openings won, and how many of those points were won "
                          "(n = points with a known winner): {v}.",
                          "{W}取得开局优势的回合中赢下该分的情况（n 为已知胜者的回合数）：{v}。"),
    "pulled.n": ("{W} shots counted for the pull zones: {v}.", "{W}用于统计调动区域的击球数：{v}。"),
}
for _z, (_en, _zh) in {"front": ("front court", "前场"), "mid": ("mid court", "中场"),
                       "rear": ("rear court", "后场")}.items():
    _SIDE[f"zone.{_z}"] = ("{W} time spent in the " + _en + ": {v}.", "{W}在" + _zh + "的时间占比：{v}。")
for _z, (_en, _zh) in {"left": ("left", "左侧"), "centre": ("centre", "中路"),
                       "right": ("right", "右侧")}.items():
    _SIDE[f"zone.{_z}"] = ("{W} time spent in the " + _en + " of the court: {v}.",
                           "{W}在" + _zh + "的时间占比：{v}。")
for _z, (_en, _zh) in {"front": ("front", "前场"), "mid": ("middle", "中场"),
                       "back": ("rear", "后场"), "left": ("left side", "左侧"),
                       "right": ("right side", "右侧")}.items():
    _SIDE[f"pulled.{_z}"] = ("{W} shots pulled the other player to the " + _en + ": {v}.",
                             "{W}的击球把对方调动到" + _zh + "的比例：{v}。")
_TEAM_TEXT = {
    "formation.front_back_share": ("{W} share of time in a front-back formation: {v}.",
                                   "{W}前后站位的时间占比：{v}。"),
    "rotations_per_rally": ("{W} front-back role swaps per rally: {v}.",
                            "{W}每回合前后换位次数：{v}。"),
    "uncovered": ("{W} court zones left uncovered: {v}.", "{W}无人覆盖的区域：{v}。"),
}
_PLAYER_TEXT = {
    "hit_share": ("Estimated share of the team's hits by {P} (not validated): {v}.",
                  "{P}占本队击球的估计比例（未经验证）：{v}。"),
    "distance_m": ("Distance covered by {P}: {v}.", "{P}的跑动距离：{v}。"),
}
for _z, (_en, _zh) in {"front": ("front court", "前场"), "mid": ("mid court", "中场"),
                       "rear": ("rear court", "后场")}.items():
    _PLAYER_TEXT[f"zone.{_z}"] = ("Time {P} spent in the " + _en + ": {v}.",
                                  "{P}在" + _zh + "的时间占比：{v}。")

_PLAYER_ID = re.compile(r"^(you|opponent)\.(P\d)\.(.+)$")
_TEAM_ID = re.compile(r"^team\.(you|opponent)\.(.+)$")
_SIDE_ID = re.compile(r"^(you|opponent)\.(.+)$")


def _template(fid):
    """``(en, zh, params)`` for a fact id, or None."""
    if fid in _MATCH:
        return _MATCH[fid] + ({},)
    m = _PLAYER_ID.match(fid)
    if m and m.group(3) in _PLAYER_TEXT:
        return _PLAYER_TEXT[m.group(3)] + ({"P": m.group(2), "who": m.group(1)},)
    m = _TEAM_ID.match(fid)
    if m and m.group(2) in _TEAM_TEXT:
        return _TEAM_TEXT[m.group(2)] + ({"who": m.group(1), "team": True},)
    m = _SIDE_ID.match(fid)
    if m and m.group(2).startswith("eff.") and len(m.group(2)) > 4:
        stroke = m.group(2)[4:]
        return ("{W} " + stroke.replace("_", " ") + " attempts and how they turned out: {v}.",
                "{W}" + _word(stroke, "zh") + "的结果：{v}。", {"who": m.group(1)})
    if m and m.group(2) in _SIDE:
        return _SIDE[m.group(2)] + ({"who": m.group(1)},)
    return None


def _sentence(fact, lang):
    """The deterministic sentence for an available fact, or None."""
    tpl = _template(fact["id"])
    if tpl is None:
        return None
    en, zh, params = tpl
    text = (zh if lang == "zh" else en)
    who = params.get("who")
    words = (_TEAM if params.get("team") else _WHO).get(who, ("", ""))
    value = _fmt_value(fact.get("value"), fact.get("unit"), lang)
    if fact.get("unit") in _UNIT and isinstance(fact.get("value"), (int, float)):
        value += _UNIT[fact["unit"]]
    return text.format(W=words[1] if lang == "zh" else words[0], P=params.get("P", ""),
                       v=value)


_NO_DATA = {"en": "Not enough data for this point.", "zh": "此项数据不足。"}
_PLAN_DO = {"en": "Work on this in your next match.", "zh": "下一场比赛重点改进这一点。"}
_PLAN_CUE = {"en": "Check the same measure in the next report.", "zh": "下次报告中再对比这项数据。"}


def _available(fact):
    return (isinstance(fact, dict) and fact.get("status") in ("ok", "partial")
            and fact.get("value") is not None)


def _dyn(pack, who, suffix):
    """Ids of ``who``'s per-player doubles facts ending in ``suffix`` (``you.P1.distance_m``)."""
    pat = re.compile(rf"^{who}\.P\d\.{re.escape(suffix)}$")
    return sorted(f["id"] for f in pack.get("facts") or [] if pat.match(f.get("id", "")))


def _candidates(pack, fmt):
    """``{section key: [fact id, ...]}`` the fallback draws its sentences from."""
    if fmt == "doubles":
        form, rot, unc = ("team.you.formation.front_back_share", "team.you.rotations_per_rally",
                          "team.you.uncovered")
        return {
            "summary": ["match.result", "match.rallies", "match.rally_median_sec", form],
            "match_profile": ["match.rally_median_sec", "match.rally_median_shots",
                              "match.short_rally_share", "match.long_rally_share",
                              "match.tempo_shots_per_sec", "match.degraded_share"],
            "movement": _dyn(pack, "you", "distance_m") + _dyn(pack, "you", "zone.front"),
            "patterns": [form, rot, unc],
            "strengths": [form] + _dyn(pack, "you", "hit_share"),
            "weaknesses": [unc, rot],
            "plan": [unc, form, rot, "team.opponent.uncovered"],
            "scouting": ["team.opponent.formation.front_back_share",
                         "team.opponent.rotations_per_rally", "team.opponent.uncovered"]}
    return {
        "summary": ["match.result", "match.rallies", "match.rally_median_sec", "you.points_won",
                    "you.mix.attack_share"],
        "match_profile": ["match.rally_median_sec", "match.rally_median_shots",
                          "match.short_rally_share", "match.long_rally_share",
                          "match.tempo_shots_per_sec", "match.degraded_share"],
        "shot_selection": ["you.mix.attack_share", "you.mix.neutral_share",
                           "you.mix.defence_share", "you.serve.short_share", "you.net_share"],
        "movement": ["you.distance_m", "you.mean_speed_mps", "you.zone.front", "you.zone.mid",
                     "you.zone.rear", "you.recovery_median_sec"],
        "patterns": ["you.seq2_top", "you.seq3_top", "you.after_lift", "you.last_stroke"],
        "strengths": ["you.technique_index", "you.points_won", "you.recovery_median_sec",
                      "you.mix.attack_share"],
        "weaknesses": ["you.technique_weak", "you.unknown_share", "you.lost_by",
                       "opponent.pulled.back"],
        "plan": ["you.technique_weak", "opponent.pulled.back", "you.recovery_median_sec",
                 "you.mix.defence_share", "you.after_lift"],
        "scouting": ["opponent.mix.attack_share", "opponent.last_stroke", "you.pulled.back",
                     "opponent.zone.rear", "opponent.seq2_top"]}


def _picks(pack, ids, lo, hi, lang):
    """``[(sentence, fact id)]``: one per available fact, padded with "not enough data"."""
    fmap = fact_map(pack)
    out = []
    for fid in ids:
        fact = fmap.get(fid)
        if _available(fact):
            text = _sentence(fact, lang)
            if text:
                out.append((text, fid))
        if len(out) >= hi:
            break
    anchor = next((i for i in ids if i in fmap and not _available(fmap[i])), None)
    if anchor is None:
        anchor = next((i for i in ids if i in fmap), None) or next(iter(fmap), None)
    while len(out) < lo:
        out.append((_NO_DATA[lang], anchor))
    return out


def _claims(pack, ids, bounds, lang):
    return {"claims": [{"text": t, "evidence": [f] if f else []}
                       for t, f in _picks(pack, ids, bounds[0], bounds[1], lang)]}


def fallback_sections(pack, lang, fmt):
    """Deterministic replacements for the five LLM sections, built from available facts.

    One claim per available key fact, in fixed bilingual wording, evidence = that fact id. Each
    section is ``{"status": "fallback", "llm": False, ...}``; ``plain_sections`` strips the flags
    to give a document ``validate_report`` accepts.
    """
    lang = "zh" if lang == "zh" else "en"
    pack = pack if isinstance(pack, dict) else {}
    cand = _candidates(pack, fmt)
    game = {k: _claims(pack, cand[k], YOUR_GAME_BOUNDS[k], lang) for k in your_game_keys(fmt)}
    items = [{"do": _PLAN_DO[lang], "why": text, "cue": _PLAN_CUE[lang],
              "evidence": [fid] if fid else []}
             for text, fid in _picks(pack, cand["plan"], PLAN_BOUNDS[0], PLAN_BOUNDS[1], lang)]
    sections = {
        "summary": _claims(pack, cand["summary"], CLAIM_BOUNDS["summary"], lang),
        "match_profile": _claims(pack, cand["match_profile"], CLAIM_BOUNDS["match_profile"],
                                 lang),
        "your_game": game,
        "game_plan": {"items": items, "training_focus": None},
        "scouting": _claims(pack, cand["scouting"], CLAIM_BOUNDS["scouting"], lang),
    }
    return {name: dict({"status": "fallback", "llm": False}, **sections[name])
            for name in LLM_SECTIONS}


def plain_sections(sections):
    """``sections`` without the per-section ``status`` / ``llm`` flags (a model-shaped document)."""
    return {name: {k: copy.deepcopy(v) for k, v in body.items() if k not in ("status", "llm")}
            for name, body in sections.items() if isinstance(body, dict)}


# ---------------------------------------------------------------- generation

def _wrap(section, content, llm):
    body = {k: copy.deepcopy(v) for k, v in content.items()}
    if section == "game_plan":
        body.setdefault("training_focus", None)
    return dict({"status": "ok" if llm else "fallback", "llm": llm}, **body)


def _call(client, pack, fmt, lang):
    """``(sections, fallback_names)`` for one language; raises ``LLMError``."""
    system, user, schema = system_prompt(lang, fmt), user_message(pack), report_schema(fmt)
    first = client.complete_json(system, user, schema)
    errors1 = validate_report(first, pack, fmt)
    bad1 = {k for k in errors1 if k in LLM_SECTIONS}
    second, bad2 = None, set(LLM_SECTIONS)
    if bad1:
        problems = "; ".join(e for errs in errors1.values() for e in errs)
        second = client.complete_json(system, user, schema, extra_messages=[
            {"role": "assistant", "content": json.dumps(first, ensure_ascii=False)},
            {"role": "user",
             "content": "Fix these problems and return the full JSON again: " + problems}])
        bad2 = {k for k in validate_report(second, pack, fmt) if k in LLM_SECTIONS}
    fallback = fallback_sections(pack, lang, fmt)
    sections, fell = {}, []
    for name in LLM_SECTIONS:
        if second is not None and name not in bad2:
            sections[name] = _wrap(name, second[name], True)
        elif name not in bad1:
            sections[name] = _wrap(name, first[name], True)
        else:
            sections[name] = fallback[name]
            fell.append(name)
    return sections, fell


def _code_sections(pack):
    return {"header": dict({"status": "ok", "llm": False}, **copy.deepcopy(pack["header"])),
            "caveats": {"status": "ok", "llm": False, "items": copy.deepcopy(pack["caveats"])}}


def _assemble(pack, llm_sections):
    code = _code_sections(pack)
    merged = dict(code, **llm_sections)
    return {name: merged[name] for name in _REPORT_ORDER}


def _error_reason(exc):
    if isinstance(exc, LLMError):
        return {"code": exc.code, "params": dict(exc.params)}
    return {"code": "bad_response", "params": {"why": "unexpected"}}


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def generate(out_dir, record, athletes, config, *, focus=None, client=None, clock=time.time):
    """The full report document for ``out_dir``; never raises (see the module docstring)."""
    started = clock()
    pack = build_facts(out_dir, record, athletes, focus)
    fmt = pack.get("format", "singles")
    doc = {"schema": SCHEMA_VERSION, "format": fmt, "status": "ok", "reason": None,
           "model": config.model, "endpoint": config.url, "generated_at": _iso(started),
           "duration_sec": 0.0, "inputs": pack.get("inputs", {}), "focus": pack.get("focus"),
           "facts": pack.get("facts", []), "tables": pack.get("tables") or {}, "by_lang": {}}
    failure = None
    fell_by_lang = {}
    try:
        client = client or LLMClient(config)
        try:
            client.check_model()
        except Exception as exc:
            failure = _error_reason(exc)
            if failure["code"] not in _FATAL:
                failure = None      # e.g. a server without /models: let the real call decide
        for lang in config.langs:
            if failure is not None and failure["code"] in _FATAL:
                llm_sections = fallback_sections(pack, lang, fmt)
            else:
                try:
                    llm_sections, fell = _call(client, pack, fmt, lang)
                    if fell:
                        fell_by_lang[lang] = fell
                except Exception as exc:
                    failure = failure or _error_reason(exc)
                    llm_sections = fallback_sections(pack, lang, fmt)
            doc["by_lang"][lang] = _assemble(pack, llm_sections)
    except Exception as exc:                                  # never raise to the caller
        failure = failure or _error_reason(exc)
        for lang in config.langs:
            if lang not in doc["by_lang"]:
                doc["by_lang"][lang] = _assemble(pack, fallback_sections(pack, lang, fmt))
    if failure is not None:
        doc["status"], doc["reason"] = "failed", failure
    elif fell_by_lang:
        doc["status"] = "partial"
        doc["reason"] = {"code": "sections_fallback", "params": fell_by_lang}
    doc["duration_sec"] = round(max(clock() - started, 0.0), 1)
    return doc


# ---------------------------------------------------------------- storage

def write_atomic(out_dir, doc):
    """Write ``strategy_report.json`` (temp file in the same dir, then ``os.replace``).

    Raises ``OSError`` when the directory is not writable; the temp file is removed.
    """
    out_dir = str(out_dir)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix="strategy.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, allow_nan=False)
        os.replace(tmp, os.path.join(out_dir, REPORT_FILE))
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def read_report(out_dir):
    """The stored report, or None when it is missing, malformed or not this schema."""
    try:
        with open(os.path.join(str(out_dir), REPORT_FILE), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return None
    if (not isinstance(doc, dict) or doc.get("schema") != SCHEMA_VERSION
            or not isinstance(doc.get("by_lang"), dict)):
        return None
    return doc


def is_stale(out_dir, doc, record):
    """True when an input file or the match record changed since ``doc`` was written."""
    inputs = doc.get("inputs") if isinstance(doc, dict) else None
    if not isinstance(inputs, dict):
        return True
    try:
        return inputs != current_inputs(out_dir, record)
    except Exception:
        return True


# ---------------------------------------------------------------- Markdown

_TITLES = {
    "en": {"title": "Match strategy report", "summary": "Summary",
           "match_profile": "Match profile", "your_game": "Your game",
           "shot_selection": "Shot selection", "movement": "Movement", "patterns": "Patterns",
           "strengths": "Strengths", "weaknesses": "Weaknesses", "game_plan": "Game plan",
           "scouting": "Scouting", "caveats": "Caveats", "evidence": "Evidence", "do": "Do",
           "why": "Why", "cue": "Cue", "training": "Training focus", "confidence": "Confidence",
           "date": "Date", "result": "Result", "vs": "vs",
           "fallback": "_This section was not written by the AI; it only lists measured "
                       "numbers._",
           "footer": "AI-written from measured data", "tactics": "Rally tactics",
           "eff": "Stroke effectiveness", "eff_cols": ("Stroke", "Effective", "Neutral",
                                                       "Ineffective", "Unknown", "Attempts"),
           "eff_few": "fewer than 5 attempts", "eff_none": "No stroke types were measured.",
           "you": "You", "opponent": "Opponent", "neutral": "neutral",
           "openings": "Openings (first three shots)",
           "open_cols": ("Rally", "Advantage", "Point won by", "Shots"),
           "open_sum": "Openings fully detected: {complete} of {total}. Advantage after the "
                       "first three shots: you {you}, opponent {opp}, neither {neutral}. "
                       "Points won by the side with the advantage: {won} of {n}.",
           "open_few": "Fewer than 5 complete openings: shown for reference, no opening claims.",
           "open_none": "No opening could be fully detected."},
    "zh": {"title": "比赛策略报告", "summary": "摘要", "match_profile": "比赛概况",
           "your_game": "你的表现", "shot_selection": "选球", "movement": "跑动",
           "patterns": "战术模式", "strengths": "优势", "weaknesses": "不足",
           "game_plan": "比赛计划", "scouting": "对手分析", "caveats": "注意事项",
           "evidence": "证据", "do": "做", "why": "原因", "cue": "提示", "training": "训练重点",
           "confidence": "可信度", "date": "日期", "result": "结果", "vs": "对",
           "fallback": "_本节不是 AI 撰写，仅列出测量数据。_",
           "footer": "AI 根据测量数据撰写", "tactics": "回合战术",
           "eff": "击球效果", "eff_cols": ("击球", "有效", "中性", "无效", "未知", "次数"),
           "eff_few": "少于 5 次", "eff_none": "没有测量到击球类型。",
           "you": "你", "opponent": "对手", "neutral": "均势",
           "openings": "开局（前三拍）",
           "open_cols": ("回合", "优势方", "得分方", "击球"),
           "open_sum": "完整识别的开局：{total} 个中的 {complete} 个。前三拍后的优势：你 {you}，"
                       "对手 {opp}，均势 {neutral}。有优势的一方赢下该分：{n} 个中的 {won} 个。",
           "open_few": "完整开局少于 5 个：仅供参考，不对开局下结论。",
           "open_none": "没有可完整识别的开局。"},
}
_CAVEATS = {
    "no_rally_outcomes": ("Who won each rally was not recorded, so there are no win or error "
                          "claims.", "没有记录每个回合的胜者，因此没有得分或失误结论。"),
    "doubles_strokes_withheld": ("Doubles runs have no stroke types.", "双打没有击球类型数据。"),
    "doubles_hits_unvalidated": ("Who hit the shuttle in doubles is estimated, not validated.",
                                 "双打中谁击球是估计值，未经验证。"),
    "degraded_rallies": ("Share of rallies with degraded tracking: {share}.",
                         "跟踪质量下降的回合占比：{share}。"),
    "rally_signal": ("Rally windows come from the {signal} signal, so counts are not claimed.",
                     "回合窗口来自 {signal} 信号，因此不给出回合数。"),
    "focus_assumed": ("No registered athlete in this match: the lower side is assumed to be "
                      "you.", "本场比赛没有登记运动员：默认下半场为你。"),
    "focus_assumed_team": ("No registered athlete in this match: Team 1 is assumed to be "
                           "your team.", "本场比赛没有登记运动员：默认将第 1 队视为你方。"),
    "missing": ("Missing input files: {files}.", "缺少输入文件：{files}。"),
    "outcomes_unreconciled": ("Rally winners do not yet add up to the recorded score "
                              "(reconciliation: {status}), so point counts may be incomplete.",
                              "回合胜负尚未与记录的比分对上（核对状态：{status}），得分统计可能不完整。"),
    "stroke_model": ("Stroke types come from a model that scores {accuracy} on the owner's "
                     "labelled shots{bar}.", "击球类型来自在本人标注击球上准确率为 {accuracy} 的模型{bar}。"),
    "owner_labelled": ("{share} of the shots carry stroke types set by the owner.",
                       "{share} 的击球类型由本人标注。"),
    "positions_unusable": ("Player positions were not usable for: {sides}; zones and rushed "
                           "replies are missing for them.",
                           "以下球员的位置数据不可用：{sides}，缺少落点区域与被迫回球判断。"),
    "openings_incomplete": ("Only {complete} of {total} points have a fully detected opening, "
                            "so no opening claims are made.",
                            "{total} 个回合中只有 {complete} 个完整识别了前三拍，因此不对开局下结论。"),
}


def _caveat_text(item, lang):
    code = item.get("code") if isinstance(item, dict) else None
    params = item.get("params") if isinstance(item, dict) and isinstance(
        item.get("params"), dict) else {}
    tpl = _CAVEATS.get(code)
    if tpl is None:
        return str(code)
    params = dict(params)
    if isinstance(params.get("files"), list):
        params["files"] = ", ".join(str(f) for f in params["files"])
    for key in ("share", "accuracy"):
        if isinstance(params.get(key), (int, float)) and not isinstance(params[key], bool):
            params[key] = f"{round(params[key] * 100):d}%"
    if "bar_met" in params:
        met = params.pop("bar_met")
        params["bar"] = ("" if not isinstance(met, bool) else
                         ("（达标）" if met else "（未达标）") if lang == "zh" else
                         (", bar met" if met else ", bar not met"))
    if isinstance(params.get("sides"), list):
        names = {"you": "你", "opponent": "对手"} if lang == "zh" else {}
        params["sides"] = ", ".join(names.get(x, str(x)) for x in params["sides"])
    try:
        return tpl[1 if lang == "zh" else 0].format(**params)
    except (KeyError, IndexError, ValueError):
        return str(code)


_MD_SPECIAL = str.maketrans({c: "\\" + c for c in "\\<>[]*_`"})


def _esc(value):
    """Markdown-escape inserted text (model text, names): no HTML, links or emphasis."""
    return str(value if value is not None else "").translate(_MD_SPECIAL)


def _cell(value):
    """``_esc`` plus what a table cell needs: a literal ``|`` must not end the cell."""
    return _esc(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


class _Footnotes:
    """Number each distinct evidence id in first-use order."""

    def __init__(self):
        self.ids = []

    def marks(self, evidence):
        out = []
        for e in evidence if isinstance(evidence, list) else []:
            if e not in self.ids:
                self.ids.append(e)
            out.append(f"[{self.ids.index(e) + 1}]")
        return "".join(out)


def _md_claims(claims, notes):
    lines = []
    for c in claims if isinstance(claims, list) else []:
        if isinstance(c, dict):
            lines.append(f"- {_esc(c.get('text', ''))} {notes.marks(c.get('evidence'))}".rstrip())
    return lines


_UNIT_ZH = {"s": " s", "m": " m", "m_per_s": " m/s", "shots_per_s": " 拍/秒",
            "per_rally": " 次/回合", "score": " 分"}


def _md_count(v):
    return str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else "-"


def _md_zone(zone, lang):
    if not isinstance(zone, dict):
        return "?"
    return f"{_word(zone.get('depth', '?'), lang)} {_word(zone.get('side', '?'), lang)}"


def _md_who(who, t):
    return t.get(who, who) if who in ("you", "opponent") else str(who or "-")


def _md_tactics(tables, lang, t):
    """The effectiveness and opening tables as Markdown lines (empty when the report has none)."""
    if not isinstance(tables, dict) or not ("effectiveness" in tables or "openings" in tables):
        return []
    out = ["", f"## {t['tactics']}", "", f"### {t['eff']}"]
    eff = tables.get("effectiveness") if isinstance(tables.get("effectiveness"), dict) else {}
    shown = False
    for who in ("you", "opponent"):
        cells = eff.get(who) if isinstance(eff.get(who), dict) else {}
        if not cells:
            continue
        shown = True
        out.extend(["", f"**{_esc(t[who])}**", "", "| " + " | ".join(t["eff_cols"]) + " |",
                    "|" + " --- |" * len(t["eff_cols"])])
        for stroke in sorted(cells):
            c = cells[stroke] if isinstance(cells[stroke], dict) else {}
            n = c.get("n")
            few = f" ({t['eff_few']})" if isinstance(n, (int, float)) and n < 5 else ""
            out.append(f"| {_cell(_word(stroke, lang))}{few} | "
                       + " | ".join(_md_count(c.get(k)) for k in
                                    ("effective", "neutral", "ineffective", "unknown", "n"))
                       + " |")
    if not shown:
        out.extend(["", t["eff_none"]])
    out.extend(["", f"### {t['openings']}", ""])
    op = tables.get("openings") if isinstance(tables.get("openings"), dict) else {}
    won_by = op.get("won_by") if isinstance(op.get("won_by"), dict) else {}
    wp = op.get("opening_winner_won_point") if isinstance(
        op.get("opening_winner_won_point"), dict) else {}
    rows = [r for r in op.get("rows") or [] if isinstance(r, dict)]
    out.append(t["open_sum"].format(
        complete=_md_count(op.get("complete")), total=_md_count(op.get("total")),
        you=_md_count(won_by.get("you")), opp=_md_count(won_by.get("opponent")),
        neutral=_md_count(op.get("neutral")), won=_md_count(wp.get("won")),
        n=_md_count(wp.get("n"))))
    if op.get("insufficient"):
        out.extend(["", t["open_few"]])
    if not rows:
        out.extend(["", t["open_none"]])
        return out
    out.extend(["", "| " + " | ".join(t["open_cols"]) + " |", "|" + " --- |" * len(t["open_cols"])])
    for r in rows:
        adv = t["neutral"] if r.get("advantage") == "neutral" else _md_who(r.get("advantage"), t)
        shots = "; ".join(
            f"{_md_who(s.get('hitter'), t)} {_word(s.get('stroke') or '?', lang)} "
            f"({_md_zone(s.get('zone'), lang)} > {_md_zone(s.get('sent_to'), lang)})"
            for s in r.get("shots") or [] if isinstance(s, dict))
        out.append(f"| {_cell(r.get('rally'))} | {_cell(adv)} | "
                   f"{_cell(_md_who(r.get('point_winner'), t))} | {_cell(shots)} |")
    return out


def _evidence_line(i, fid, fact, lang):
    if not isinstance(fact, dict):
        return f"[{i}] `{fid}`"
    value = _fmt_value(fact.get("value"), fact.get("unit"), lang)
    unit = fact.get("unit")
    if unit in _UNIT and isinstance(fact.get("value"), (int, float)):
        value += (_UNIT_ZH if lang == "zh" else _UNIT)[unit]
    if fact.get("status") == "unavailable":
        value = "无数据" if lang == "zh" else "n/a"
    return f"[{i}] `{fid}` = {_esc(value)}"


def render_markdown(doc, lang):
    """The report in ``lang`` (unknown language -> English) as Markdown; total."""
    doc = doc if isinstance(doc, dict) else {}
    by_lang = doc.get("by_lang") if isinstance(doc.get("by_lang"), dict) else {}
    lang = lang if lang in _TITLES else "en"
    report = by_lang.get(lang)
    if not isinstance(report, dict):
        report = next((r for r in by_lang.values() if isinstance(r, dict)), {})
    t = _TITLES[lang]
    notes = _Footnotes()
    out = [f"# {t['title']}"]
    header = report.get("header") if isinstance(report.get("header"), dict) else {}
    if header.get("you") or header.get("opponent"):
        out.append(f"**{_esc(header.get('you') or '?')} {t['vs']} "
                   f"{_esc(header.get('opponent') or '?')}**")
    meta = []
    if header.get("date"):
        meta.append(f"{t['date']}: {_esc(header['date'])}")
    if header.get("winner"):
        meta.append(f"{t['result']}: " + _fmt_result(
            {"winner": header["winner"], "sets": header.get("sets") or []}, lang))
    if header.get("confidence"):
        meta.append(f"{t['confidence']}: {_esc(header['confidence'])}")
    if meta:
        out.append(" · ".join(meta))

    def heading(level, name, section):
        out.extend(["", f"{'#' * level} {t[name]}"])
        if isinstance(section, dict) and section.get("llm") is False:
            out.extend(["", t["fallback"]])
        out.append("")

    for name in ("summary", "match_profile"):
        section = report.get(name)
        if isinstance(section, dict):
            heading(2, name, section)
            out.extend(_md_claims(section.get("claims"), notes))
    game = report.get("your_game")
    if isinstance(game, dict):
        heading(2, "your_game", game)
        for key in ("shot_selection", "movement", "patterns", "strengths", "weaknesses"):
            if isinstance(game.get(key), dict):
                out.extend(["", f"### {t[key]}", ""])
                out.extend(_md_claims(game[key].get("claims"), notes))
    plan = report.get("game_plan")
    if isinstance(plan, dict):
        heading(2, "game_plan", plan)
        for i, item in enumerate(plan.get("items") or [], 1):
            if isinstance(item, dict):
                out.append(f"{i}. **{t['do']}:** {_esc(item.get('do', ''))} **{t['why']}:** "
                           f"{_esc(item.get('why', ''))} **{t['cue']}:** "
                           f"{_esc(item.get('cue', ''))} "
                           f"{notes.marks(item.get('evidence'))}".rstrip())
        focus = plan.get("training_focus")
        if isinstance(focus, dict):
            out.extend(["", f"**{t['training']}:** {_esc(focus.get('text', ''))} "
                            f"{notes.marks(focus.get('evidence'))}".rstrip()])
    scouting = report.get("scouting")
    if isinstance(scouting, dict):
        heading(2, "scouting", scouting)
        out.extend(_md_claims(scouting.get("claims"), notes))
    out.extend(_md_tactics(doc.get("tables"), lang, t))
    caveats = report.get("caveats")
    items = caveats.get("items") if isinstance(caveats, dict) else None
    if items:
        heading(2, "caveats", None)
        out.extend(f"- {_esc(_caveat_text(i, lang))}" for i in items)
    heading(2, "evidence", None)
    fmap = fact_map(doc)
    out.extend(_evidence_line(i, fid, fmap.get(fid), lang) + "  "
               for i, fid in enumerate(notes.ids, 1))
    out.extend(["", f"_{t['footer']} · {_esc(doc.get('model', ''))} · "
               f"{_esc(doc.get('generated_at', ''))}_"])
    return "\n".join(out) + "\n"
