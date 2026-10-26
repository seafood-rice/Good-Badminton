"""Check a model-written report against the facts pack (spec section 6.3).

``validate_report(doc, pack, fmt)`` returns ``{section: [error, ...]}``; an empty dict means
the document is valid. It is total: any junk ``doc`` yields errors, never an exception.

Rules, per claim ``{"text", "evidence"}``:
- structure: exactly those keys, claim counts inside the schema bounds, ``evidence`` a non-empty
  list of strings;
- every evidence id is in the pack; a cited fact that is ``unavailable`` is only allowed when
  the text holds no digit (it says "not enough data");
- every number token in the text (after NFKC, so full-width digits and ``％`` count) is held by
  a cited fact: equal within half a unit of its last decimal place, a ``%`` token also matches
  100 x a fact, and a share fact matches 100 x its value. Integers 1-3 without ``%`` and digits
  glued to an identifier (``P4``, ``seq2``) are exempt.

Errors under the key ``_root`` (an unknown top-level key) are not about a section: the caller
may drop the extra key instead of falling back.
"""
import math
import re
import string
import unicodedata

from .facts import fact_map
from .prompts import (CLAIM_BOUNDS, LLM_SECTIONS, PLAN_BOUNDS, YOUR_GAME_BOUNDS,
                      your_game_keys)

_NUMBER = re.compile(r"\d+(?:\.\d+)?%?")
_IDENT_CHARS = set(string.ascii_letters + "_")
_EPS = 1e-9


def _leaves(value, out):
    """Numeric leaves of a nested value (dict values and list items; bools are not numbers)."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if math.isfinite(value):
            out.append(float(value))
    elif isinstance(value, dict):
        for v in value.values():
            _leaves(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _leaves(v, out)


def _tokens(text):
    """The checked number tokens of ``text`` (NFKC-normalised), exemptions removed."""
    text = unicodedata.normalize("NFKC", text)
    out = []
    for m in _NUMBER.finditer(text):
        tok = m.group(0)
        if m.start() > 0 and text[m.start() - 1] in _IDENT_CHARS:
            continue                          # P4, seq2, p90 inside an id
        if not tok.endswith("%") and "." not in tok and 1 <= int(tok) <= 3:
            continue                          # ordinals and list counts
        out.append(tok)
    return out


def _held(tok, facts):
    pct = tok.endswith("%")
    digits = tok.rstrip("%")
    value = float(digits)
    tol = 0.5 * 10 ** -(len(digits.split(".")[1]) if "." in digits else 0) + _EPS
    for fact in facts:
        leaves = []
        _leaves(fact.get("value"), leaves)
        share = fact.get("unit") == "share"
        for v in leaves:
            if abs(value - v) <= tol:
                return True
            if (pct or share) and abs(value - 100 * v) <= tol:
                return True
    return False


def _claim(item, label, fmap, errors, extra_text=()):
    """Validate one ``{"text", "evidence"}``-like dict; ``extra_text`` are its other strings."""
    keys = {"text", "evidence"} if not extra_text else {"do", "why", "cue", "evidence"}
    if not isinstance(item, dict):
        errors.append(f"{label}: not an object")
        return
    if set(item) != keys:
        errors.append(f"{label}: keys must be exactly {sorted(keys)}")
        return
    names = ("text",) if not extra_text else ("do", "why", "cue")
    texts = [item[n] for n in names]
    if not all(isinstance(t, str) and t.strip() for t in texts):
        errors.append(f"{label}: text must be a non-empty string")
        return
    evidence = item["evidence"]
    if (not isinstance(evidence, list) or not evidence
            or not all(isinstance(e, str) for e in evidence)):
        errors.append(f"{label}: evidence must be a non-empty list of fact ids")
        return
    unknown = [e for e in evidence if e not in fmap]
    if unknown:
        errors.append(f"{label}: unknown evidence id {', '.join(unknown)}")
        return
    cited = [fmap[e] for e in evidence]
    text = unicodedata.normalize("NFKC", " ".join(texts))
    if any(f.get("status") == "unavailable" for f in cited) and re.search(r"\d", text):
        gone = [f["id"] for f in cited if f.get("status") == "unavailable"]
        errors.append(f"{label}: cites unavailable fact {', '.join(gone)} but states a number; "
                      "say there is not enough data instead")
    for tok in _tokens(text):
        if not _held(tok, cited):
            errors.append(f"number {tok} in {label} not in cited facts")


def _claims(block, section, key, bounds, fmap, errors):
    label = section if key is None else f"{section}.{key}"
    if not isinstance(block, dict) or set(block) != {"claims"}:
        errors.append(f"{label}: must be an object with only 'claims'")
        return
    claims = block["claims"]
    if not isinstance(claims, list):
        errors.append(f"{label}: claims must be a list")
        return
    lo, hi = bounds
    if not lo <= len(claims) <= hi:
        errors.append(f"{label}: {len(claims)} claims, need {lo} to {hi}")
    for i, c in enumerate(claims):
        _claim(c, f"{label}[{i}]", fmap, errors)


def _your_game(block, fmap, fmt, errors):
    keys = your_game_keys(fmt)
    if not isinstance(block, dict) or set(block) != set(keys):
        errors.append(f"your_game: keys must be exactly {sorted(keys)}")
        return
    for key in keys:
        _claims(block[key], "your_game", key, YOUR_GAME_BOUNDS[key], fmap, errors)


def _game_plan(block, fmap, errors):
    if not isinstance(block, dict) or not {"items"} <= set(block) <= {"items", "training_focus"}:
        errors.append("game_plan: keys must be 'items' and 'training_focus'")
        return
    items = block["items"]
    if not isinstance(items, list):
        errors.append("game_plan: items must be a list")
        return
    lo, hi = PLAN_BOUNDS
    if not lo <= len(items) <= hi:
        errors.append(f"game_plan: {len(items)} items, need {lo} to {hi}")
    for i, item in enumerate(items):
        _claim(item, f"game_plan.items[{i}]", fmap, errors, extra_text=("do", "why", "cue"))
    focus = block.get("training_focus")
    if focus is not None:
        _claim(focus, "game_plan.training_focus", fmap, errors)


def validate_report(doc, pack, fmt):
    """``{section: [error, ...]}`` for the LLM sections of ``doc``; ``{}`` when valid."""
    if not isinstance(doc, dict):
        return {s: ["the report is not a JSON object"] for s in LLM_SECTIONS}
    try:
        fmap = fact_map(pack)
    except Exception:
        fmap = {}
    out = {}
    for section in LLM_SECTIONS:
        errors = []
        try:
            if section not in doc:
                errors.append(f"{section}: missing")
            elif section == "your_game":
                _your_game(doc[section], fmap, fmt, errors)
            elif section == "game_plan":
                _game_plan(doc[section], fmap, errors)
            else:
                _claims(doc[section], section, None, CLAIM_BOUNDS[section], fmap, errors)
        except Exception as exc:               # a validator bug must not pass a section
            errors.append(f"{section}: could not be checked ({type(exc).__name__})")
        if errors:
            out[section] = errors
    extra = [k for k in doc if k not in LLM_SECTIONS]
    if extra:
        out["_root"] = [f"unknown top-level key {', '.join(str(k) for k in extra)}"]
    return out
