"""What the model is asked: the system prompt, the user message and the JSON schema.

The schema (``report_schema``) is sent as ``response_format`` and mirrors the bounds
``validate.validate_report`` enforces. Prompts are plain module constants composed with
``str.format``; the ``zh`` rules are written in Chinese.
"""
import json

LLM_SECTIONS = ("summary", "match_profile", "your_game", "game_plan", "scouting")
WORD_CAPS = {"summary": 80, "match_profile": 80, "your_game": 300, "game_plan": 160,
             "scouting": 100}

# (min, max) claim counts; the single source for the schema and the validator.
CLAIM_BOUNDS = {"summary": (3, 3), "match_profile": (2, 4), "scouting": (2, 4)}
YOUR_GAME_BOUNDS = {"shot_selection": (1, 3), "movement": (1, 3), "patterns": (1, 3),
                    "strengths": (2, 3), "weaknesses": (2, 3)}
PLAN_BOUNDS = (3, 5)


def your_game_keys(fmt):
    keys = list(YOUR_GAME_BOUNDS)
    return keys if fmt == "singles" else [k for k in keys if k != "shot_selection"]


# ---------------------------------------------------------------- schema

def _claim_schema():
    return {"type": "object",
            "properties": {"text": {"type": "string"},
                           "evidence": {"type": "array", "items": {"type": "string"},
                                        "minItems": 1}},
            "required": ["text", "evidence"], "additionalProperties": False}


def _claims_schema(lo, hi):
    return {"type": "object",
            "properties": {"claims": {"type": "array", "items": _claim_schema(),
                                      "minItems": lo, "maxItems": hi}},
            "required": ["claims"], "additionalProperties": False}


def report_schema(fmt):
    """The JSON Schema of the LLM-written sections for ``fmt`` (``singles`` or ``doubles``)."""
    game = {k: _claims_schema(*YOUR_GAME_BOUNDS[k]) for k in your_game_keys(fmt)}
    item = {"type": "object",
            "properties": {"do": {"type": "string"}, "why": {"type": "string"},
                           "cue": {"type": "string"},
                           "evidence": {"type": "array", "items": {"type": "string"},
                                        "minItems": 1}},
            "required": ["do", "why", "cue", "evidence"], "additionalProperties": False}
    plan = {"type": "object",
            "properties": {"items": {"type": "array", "items": item,
                                     "minItems": PLAN_BOUNDS[0], "maxItems": PLAN_BOUNDS[1]},
                           "training_focus": {"anyOf": [_claim_schema(), {"type": "null"}]}},
            "required": ["items", "training_focus"], "additionalProperties": False}
    props = {
        "summary": _claims_schema(*CLAIM_BOUNDS["summary"]),
        "match_profile": _claims_schema(*CLAIM_BOUNDS["match_profile"]),
        "your_game": {"type": "object", "properties": game, "required": list(game),
                      "additionalProperties": False},
        "game_plan": plan,
        "scouting": _claims_schema(*CLAIM_BOUNDS["scouting"]),
    }
    return {"type": "object", "properties": props, "required": list(LLM_SECTIONS),
            "additionalProperties": False}


# ---------------------------------------------------------------- system prompt

_GLOSSARY = (
    "Fact ids: match.* = the whole match; you.* / opponent.* = the athlete you coach and the "
    "other side. mix.* = share of non-serve strokes (attack = smash, drive, drop; neutral = "
    "net and push play; defence = clear, lift, block). seq2_top = the opponent's stroke then "
    "this player's reply; after_lift = this player's replies to a lift. zone.* = share of time "
    "in a court zone (front/mid/rear from this side's net; left/centre/right). pulled.* = "
    "where this player's shots drag the OTHER player. recovery_median_sec = seconds to get "
    "back to base after a hit. short rally = 5 shots or fewer, long rally = 10 or more.")

_SYSTEM_EN = (
    "You are an experienced badminton coach talking directly to your athlete (use \"you\"). "
    "Write a {fmt} match strategy report from the measured facts you are given.\n"
    "Rules:\n"
    "1. Use only the facts given. Put the ids of the facts a claim rests on, exactly as "
    "written, in its evidence list.\n"
    "2. Never invent a number. Every number in your text must be held by a fact you cite "
    "(a share of 0.42 may be written 42%). Do not do arithmetic on facts.\n"
    "3. If a fact is unavailable or partial, say there is not enough data, with no digits, "
    "and do not cite it for a number.\n"
    "4. Never say who won or lost a rally unless the points_won, won_by or lost_by facts are "
    "given.\n"
    "5. {fmt_rule}\n"
    "6. Word limits: summary {summary} words in total, match_profile {match_profile}, "
    "your_game {your_game}, game_plan {game_plan}, scouting {scouting}.\n"
    "7. Write in English. Answer with the JSON object only.\n"
    "{glossary}")

_SYSTEM_ZH = (
    "你是一位经验丰富的羽毛球教练，直接对你的运动员说话（用“你”）。请根据给定的测量数据，"
    "撰写一份{fmt_zh}比赛策略报告。\n"
    "规则：\n"
    "1. 只使用给定的数据。每条结论依据的数据 id 必须原样写入 evidence 列表。\n"
    "2. 绝不编造数字。文字中的每个数字都必须出现在你引用的数据里（占比 0.42 可写成 42%），"
    "不要自行计算。\n"
    "3. 数据缺失或不完整时，写“数据不足”，且不要带数字，也不要为数字引用该数据。\n"
    "4. 除非给出 points_won、won_by 或 lost_by，否则不要说谁赢了或输了某个回合。\n"
    "5. {fmt_rule}\n"
    "6. 字数上限：summary 共 {summary} 字，match_profile {match_profile} 字，"
    "your_game {your_game} 字，game_plan {game_plan} 字，scouting {scouting} 字。\n"
    "7. 用简体中文书写。只输出 JSON 对象。\n"
    "{glossary}")

_FMT_RULES = {
    ("en", "singles"): "Stroke types are measured for both players; use them.",
    ("en", "doubles"): ("Doubles: stroke types are not available. Hit counts per player are "
                        "estimated (not validated); say so whenever you use them."),
    ("zh", "singles"): "双方的击球类型都有测量数据，请使用。",
    ("zh", "doubles"): "双打：没有击球类型数据。每位球员的击球次数是估计值（未经验证），引用时必须说明。",
}


def system_prompt(lang, fmt):
    """The system prompt for ``lang`` (``en`` or ``zh``) and ``fmt`` (``singles`` or ``doubles``)."""
    lang = "zh" if lang == "zh" else "en"
    fmt = "doubles" if fmt == "doubles" else "singles"
    template = _SYSTEM_ZH if lang == "zh" else _SYSTEM_EN
    return template.format(fmt=fmt, fmt_zh="双打" if fmt == "doubles" else "单打",
                           fmt_rule=_FMT_RULES[(lang, fmt)], glossary=_GLOSSARY, **WORD_CAPS)


def user_message(pack):
    """Compact JSON of the pack's ``format``, ``focus``, ``header`` and ``facts`` (no inputs)."""
    pack = pack if isinstance(pack, dict) else {}
    body = {k: pack.get(k) for k in ("format", "focus", "header", "facts")}
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))
