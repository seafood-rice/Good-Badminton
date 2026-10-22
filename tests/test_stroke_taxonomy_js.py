"""The UI's stroke lists agree with badminton_analysis/stroke/stroke_types.py (spec 2026-10-22 section 3)."""
import os
import re

from badminton_analysis.stroke.stroke_types import GROUP_OF, GROUPS, STROKE_TYPES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, "static", "kestrel", "core", "i18n.js")
LEGACY = os.path.join(ROOT, "static", "kestrel", "legacy", "strokes.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _block(text, start):
    m = re.search(re.escape(start) + r"(.*?)[\]}]\)?;", text, re.S)
    assert m, f"{start!r} not found"
    return m.group(1)


def _pairs(block):
    """`key: 'value'` pairs of a flat JS object literal."""
    return dict(re.findall(r"(\w+):\s*'([^']*)'", block))


def _label_table(block):
    """`id: ['zh', 'en']` entries of a JS object literal."""
    return {k: (zh, en) for k, zh, en in re.findall(r"(\w+):\s*\['([^']*)',\s*'([^']*)'\]", block)}


def _strings(text, lang):
    body = text[text.index("export const STRINGS"):]
    zh_at, en_at = body.index("\n  zh: {"), body.index("\n  en: {")
    part = body[zh_at:en_at] if lang == "zh" else body[en_at:]
    return dict(re.findall(r"'(stroke\.\w+)':\s*'([^']*)'", part))


def test_i18n_type_ids_and_groups_match_python():
    text = _read(I18N)
    ids = re.findall(r"'(\w+)'", _block(text, "export const STROKE_TYPE_IDS = Object.freeze(["))
    assert tuple(ids) == STROKE_TYPES
    groups = _pairs(_block(text, "export const STROKE_GROUP = Object.freeze({"))
    assert groups == GROUP_OF
    tech = re.findall(r"'(\w+)'", _block(text, "export const TECHNIQUE_IDS = Object.freeze(["))
    assert tuple(tech) == GROUPS


def test_i18n_has_every_stroke_name_in_both_languages():
    text = _read(I18N)
    for lang in ("zh", "en"):
        names = _strings(text, lang)
        missing = [s for s in STROKE_TYPES if f"stroke.{s}" not in names]
        assert not missing, f"{lang} lacks {missing}"


def test_legacy_tables_match_python_and_i18n():
    text = _read(LEGACY)
    labels = _label_table(_block(text, "export const STROKE_LABELS_12 = {"))
    assert set(labels) == set(STROKE_TYPES)
    i18n = _read(I18N)
    zh, en = _strings(i18n, "zh"), _strings(i18n, "en")
    for s in STROKE_TYPES:
        assert labels[s] == (zh[f"stroke.{s}"], en[f"stroke.{s}"]), s
    assert set(_label_table(_block(text, "export const GROUP_LABELS = {"))) == set(GROUPS)
    order = re.findall(r"'(\w+)'", _block(text, "export const GROUP_ORDER = ["))
    assert sorted(order) == sorted(GROUPS) and len(order) == len(GROUPS)
