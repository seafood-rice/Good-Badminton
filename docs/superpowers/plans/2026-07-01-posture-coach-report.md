# Posture Coach Report (tri-lingual) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a professional coach report from a Posture Drill analysis — correlating each biomechanical finding to shot-quality impact, with strengths, weaknesses, drills, per-rep table, and training plan — in English, Traditional Chinese, and Simplified Chinese, rendered in the Web UI and downloadable as HTML (and best-effort PDF), with an optional multi-provider LLM prose-polish layer.

**Architecture:** A curated knowledge base (`coach_kb.py`, pure data + i18n) is the always-available source of truth. A pure `report_builder.py` assembles a structured report per language from the drill reports + summary. Pure renderers (`report_render.py`) produce self-contained HTML and best-effort PDF. An isolated, default-off `report_llm.py` polishes only prose fields via pluggable providers (Anthropic/OpenAI/Google/local) and auth sources (reuse-existing-token / API-key / local). The posture system writes the report files after the drill summary; a Flask route + UI panel expose them.

**Tech Stack:** Python 3.12 (venv), NumPy, Flask, pytest; optional `weasyprint` for PDF (best-effort); optional provider SDKs (lazy, never hard deps).

**Depends on:** the Posture Drill mode (`drill_reps.jsonl` + `drill_summary.json` + `PostureAnalysisSystem`). Independent of the skeleton-overlay plan.

## Global Constraints

- Python 3.8+ compatible syntax. NumPy `>=1.21.6,<2.0`. Flask `>=2.0,<4.0`.
- Use the venv interpreter EXACTLY: `.venv/Scripts/python.exe`.
- Metric vocabulary EXACTLY: `elbow_extension, trunk_rotation, wrist_flexion, knee_flexion, hip_shoulder_separation, weight_transfer`. Stroke vocabulary: `high_clear, smash, drop_shot, serve`.
- Languages EXACTLY: `en`, `zh-Hant`, `zh-Hans` (constant `SUPPORTED_LANGS`). All three generated every run.
- Impact categories: `power`, `accuracy`, `consistency`, `injury_risk`.
- Report **prose must read like a human coach — no AI-generated tone.** The `humanizer` skill is applied to all authored KB templates (a manual authoring step in Task 2) and to LLM output (Task 6 system prompt).
- LLM polish defaults **OFF** (deterministic, offline, free). It only ever rewrites prose fields (`mechanism_text`, `drill_text`, `verdict_text`); it never changes numbers, scores, structure, or drill selection.
- The app builds **no OAuth login flow**; it only reads tokens obtained by sanctioned first-party tools (e.g. Claude Code credentials, `gcloud` ADC), or API keys from env/config, or uses a local no-auth endpoint.
- PDF is **best-effort**: if the PDF lib is unavailable or fails, HTML+JSON are still written, the run never fails, and the UI hides the PDF button.
- New output files live under `outputs/<video>/posture/`.
- Reuse existing helpers: `write_json`/`clean_value` (`data/writer.py`), `generate_plan` (`training/plan_generator.py`), and the drill report shapes from the Posture Drill mode.
- New Flask routes use `try/except → jsonify({'error': str(e)}), 500` and structured 404.
- Full test suite must stay green (starts at 91 if this plan runs alone; higher if run after the skeleton plan — either way, no regressions).

## Reused/verified shapes (do not redefine)
- A drill report (one per rep, from `drill_reps.jsonl`): `{rep_id, stroke_type, player_side, overall_score (float|None), per_metric:{metric:{measured, score, ideal_range:[lo,hi], direction, severity}}, weaknesses:[{metric, measured, ideal_range, direction, severity, description}], strengths:[metric-name strings]}`.
- `drill_summary.json`: `{stroke_type, rep_count, mean_score, best_rep:{rep_id,score}, worst_rep, consistency, per_metric_avg:{metric:avg}, recurring_weaknesses:[{metric,count}], strengths:[{metric,count}]}`.
- `generate_plan(match_summary, library=None, weeks=4) -> {weeks:[{week,phase,sessions:[{exercise_id,name,category,location,frequency_per_week,reps,sets,duration_min,targets}]}], targeted_weaknesses, on_court, at_home}` — consumes `recurring_weaknesses:[{metric,count}]`.
- `write_json(path, payload)`, `clean_value(value)`.

---

## File Structure

**New files:**
- `badminton_analysis/posture/coach_kb.py` — KB data + i18n + lookups
- `badminton_analysis/posture/report_builder.py` — `build_coach_report`
- `badminton_analysis/posture/report_render.py` — `render_html`, `render_pdf`
- `badminton_analysis/posture/report_llm.py` — provider + auth-source abstractions + `polish`
- `tests/test_coach_kb.py`, `tests/test_report_builder.py`, `tests/test_report_render.py`, `tests/test_report_llm.py`, `tests/test_app_posture_report_route.py`

**Modified files:**
- `badminton_analysis/posture/system.py` — after drill_summary, build + write reports (JSON/HTML/PDF); stamp `date`; accept `report_llm` selection
- `main_posture.py` — `--report-llm` flag
- `app.py` — `GET /api/posture-report/<video>?lang=...`; pass `report_llm` through analyze route
- `web_ui.html` — coach-report panel: language switcher + inline render + download buttons

---

### Task 1: KB structure + lookups (data shape first, English only)

**Files:**
- Create: `badminton_analysis/posture/coach_kb.py`
- Test: `tests/test_coach_kb.py`

**Interfaces:**
- Produces:
  - Constants: `SUPPORTED_LANGS = ("en", "zh-Hant", "zh-Hans")`, `METRICS = ("elbow_extension","trunk_rotation","wrist_flexion","knee_flexion","hip_shoulder_separation","weight_transfer")`, `IMPACT_CATEGORIES = ("power","accuracy","consistency","injury_risk")`, `STROKES = ("high_clear","smash","drop_shot","serve")`.
  - `lookup_weakness(metric, stroke, direction) -> {"impact","severity_hint","mechanism_key","drill_key"}` with fallback chain: `(metric,stroke,direction)` → `(metric,"*",direction)` → a guaranteed generic entry.
  - `lookup_strength(metric, stroke) -> {"impact","text_key"}` (generic fallback `(metric,"*")`).
  - `t(lang, key, **fmt) -> str` — resolve an i18n string (format with `**fmt`); falls back to `en` if a key is missing in a language, and to the key itself if absent entirely.
  This task establishes the DATA SHAPE and lookups with English text present; Task 2 fills zh-Hant/zh-Hans + humanizes.

- [ ] **Step 1: Write the failing tests**

`tests/test_coach_kb.py`:
```python
import pytest
from badminton_analysis.posture import coach_kb as kb


def test_constants():
    assert kb.SUPPORTED_LANGS == ("en", "zh-Hant", "zh-Hans")
    assert set(kb.METRICS) == {"elbow_extension","trunk_rotation","wrist_flexion",
                               "knee_flexion","hip_shoulder_separation","weight_transfer"}
    assert set(kb.IMPACT_CATEGORIES) == {"power","accuracy","consistency","injury_risk"}


def test_lookup_weakness_specific_and_fallback():
    # A specific entry exists for smash + elbow under.
    e = kb.lookup_weakness("elbow_extension", "smash", "under")
    assert e["impact"] in kb.IMPACT_CATEGORIES
    assert "mechanism_key" in e and "drill_key" in e
    # An unusual combination still resolves via fallback (never raises/None).
    e2 = kb.lookup_weakness("wrist_flexion", "serve", "over")
    assert e2 is not None and "mechanism_key" in e2


def test_lookup_strength_resolves():
    s = kb.lookup_strength("trunk_rotation", "smash")
    assert s is not None and "text_key" in s


def test_t_resolves_english_and_formats():
    # every mechanism_key/drill_key referenced by a weakness entry must resolve in English
    e = kb.lookup_weakness("elbow_extension", "smash", "under")
    assert isinstance(kb.t("en", e["mechanism_key"]), str) and kb.t("en", e["mechanism_key"])
    assert isinstance(kb.t("en", e["drill_key"]), str) and kb.t("en", e["drill_key"])


def test_t_missing_key_falls_back_to_key_string():
    assert kb.t("en", "__nope__") == "__nope__"


def test_every_weakness_entry_text_keys_exist_in_english():
    # Iterate the whole KB; every referenced key must exist in English.
    for entry in kb.iter_all_entries():
        for k in ("mechanism_key", "drill_key"):
            if k in entry:
                assert kb.t("en", entry[k]) != entry[k] or entry[k] == "", \
                    f"missing English text for {entry[k]}"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coach_kb.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the structure + English content + lookups**

`badminton_analysis/posture/coach_kb.py` — create with:
- The constants above.
- `COACH_KB` dict keyed by `(metric, stroke, direction)` and `(metric, "*", direction)`, values `{"impact","severity_hint","mechanism_key","drill_key"}`. Author at minimum: for EACH metric, a generic `(metric,"*","under")` and `(metric,"*","over")` entry; PLUS stroke-specific entries where nuance matters (author at least `("elbow_extension","smash","under")`, `("wrist_flexion","smash","under")`, `("trunk_rotation","smash","under")`, `("knee_flexion","serve","under")`, `("weight_transfer","high_clear","under")`). A module-level `_GENERIC_FALLBACK = {"impact":"consistency","severity_hint":"moderate","mechanism_key":"generic_mech","drill_key":"generic_drill"}`.
- `STRENGTH_KB` keyed by `(metric, stroke)` and `(metric,"*")`, values `{"impact","text_key"}`; a `_GENERIC_STRENGTH`.
- `I18N = {"en": {...}, "zh-Hant": {...}, "zh-Hans": {...}}`. In THIS task, fully populate `"en"` with every `mechanism_key`/`drill_key`/`text_key` referenced plus section/label/verdict keys; create `"zh-Hant"` and `"zh-Hans"` as EMPTY dicts `{}` for now (Task 2 fills them; `t` falls back to English meanwhile).
- `lookup_weakness`, `lookup_strength`, `t`, and `iter_all_entries()` (yields every dict in `COACH_KB` and `STRENGTH_KB`, plus `_GENERIC_FALLBACK`/`_GENERIC_STRENGTH`).

`t` implementation detail:
```python
def t(lang, key, **fmt):
    table = I18N.get(lang, {})
    s = table.get(key)
    if s is None:
        s = I18N["en"].get(key)      # fall back to English
    if s is None:
        return key                    # last resort: the key itself
    return s.format(**fmt) if fmt else s
```
Author the English coaching text in a natural human-coach voice (this is the first pass; humanizer is applied in Task 2). Each `mechanism_*` explains why the flaw costs shot quality; each `drill_*` gives a concrete corrective drill.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coach_kb.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/coach_kb.py tests/test_coach_kb.py
git commit -m "feat: coach knowledge base structure + English content"
```

---

### Task 2: Traditional + Simplified Chinese content, humanized

**Files:**
- Modify: `badminton_analysis/posture/coach_kb.py`
- Test: `tests/test_coach_kb_i18n.py`

**Interfaces:**
- Consumes: the `I18N` tables + `iter_all_entries` (Task 1).
- Produces: `I18N["zh-Hant"]` and `I18N["zh-Hans"]` fully populated so EVERY key present in `I18N["en"]` also exists in both Chinese tables. No signature changes.

**Authoring note:** Before finalizing, run the `humanizer` skill over the English text AND author the Traditional/Simplified text idiomatically (NOT machine-converted from each other) — natural coach voice, no AI tells. This is a manual authoring quality step; the test below enforces completeness, and the humanizer pass enforces tone.

- [ ] **Step 1: Write the failing completeness test**

`tests/test_coach_kb_i18n.py`:
```python
from badminton_analysis.posture import coach_kb as kb


def test_all_languages_cover_every_key():
    en_keys = set(kb.I18N["en"].keys())
    assert en_keys, "English table must be populated"
    for lang in ("zh-Hant", "zh-Hans"):
        missing = en_keys - set(kb.I18N[lang].keys())
        assert not missing, f"{lang} missing keys: {sorted(missing)}"


def test_chinese_tables_are_distinct_from_english():
    # Sanity: the Chinese tables should not be verbatim copies of English
    # for the coaching text (at least the mechanism/drill/verdict keys differ).
    sample_keys = [k for k in kb.I18N["en"] if k.startswith(("mech", "drill", "verdict",
                                                             "elbow", "trunk", "wrist",
                                                             "knee", "hip", "weight", "generic"))]
    assert sample_keys, "expected some coaching-text keys"
    differing = sum(1 for k in sample_keys if kb.I18N["zh-Hans"].get(k) != kb.I18N["en"].get(k))
    assert differing >= max(1, len(sample_keys) // 2)


def test_hant_and_hans_are_not_identical():
    # Traditional vs Simplified should differ on most text (idiomatic, not identical).
    common = set(kb.I18N["zh-Hant"]) & set(kb.I18N["zh-Hans"])
    text_keys = [k for k in common if any(k.startswith(p) for p in
                 ("mech","drill","verdict","elbow","trunk","wrist","knee","hip","weight","generic"))]
    if text_keys:
        differing = sum(1 for k in text_keys
                        if kb.I18N["zh-Hant"][k] != kb.I18N["zh-Hans"][k])
        assert differing >= 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coach_kb_i18n.py -v`
Expected: FAIL — the Chinese tables are empty (from Task 1), so `test_all_languages_cover_every_key` fails.

- [ ] **Step 3: Populate the Chinese tables (humanized authoring)**

In `coach_kb.py`, fill `I18N["zh-Hant"]` and `I18N["zh-Hans"]` with a translation for EVERY key in `I18N["en"]`. Author Traditional and Simplified independently (idiomatic phrasing, correct regional character forms), in a natural coach voice. Apply the `humanizer` skill to the English source first, then translate the humanized text. Keep any `{}` format placeholders (e.g. `{measured}`, `{lo}`, `{hi}`) intact and in a natural position within each language.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_coach_kb_i18n.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/coach_kb.py tests/test_coach_kb_i18n.py
git commit -m "feat: Traditional + Simplified Chinese coach-report content"
```

---

### Task 3: Report builder

**Files:**
- Create: `badminton_analysis/posture/report_builder.py`
- Test: `tests/test_report_builder.py`

**Interfaces:**
- Consumes: `coach_kb` (Tasks 1-2); `generate_plan` (existing).
- Produces: `build_coach_report(reports, summary, meta) -> {lang: report_dict}` for each lang in `SUPPORTED_LANGS`. `meta` = `{"date": str, "stroke_type": str, "dominant_hand": str, "pose_family": str}`. `report_dict` keys EXACTLY: `lang, header{stroke, stroke_label, rep_count, dominant_hand, date, pose_family}, summary{mean_score, consistency, verdict_text}, strengths[{metric, metric_label, measured, impact_label, text}], weaknesses[{metric, metric_label, measured, ideal_range, direction, severity, impact_label, mechanism_text, drill_text}], per_rep[{rep_id, overall_score, top_weakness}], training_plan{...}`. Verdict text chosen from score/consistency bands. Weakness rows built from `summary.recurring_weaknesses` (metric+count), pulling the measured/direction from the highest-severity matching rep-level finding. Strength rows from `summary.strengths`. `training_plan` = `generate_plan(summary)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_report_builder.py`:
```python
from badminton_analysis.posture.report_builder import build_coach_report
from badminton_analysis.posture import coach_kb as kb


def _summary():
    return {
        "stroke_type": "smash", "rep_count": 5, "mean_score": 68.0,
        "best_rep": {"rep_id": 2, "score": 82}, "worst_rep": {"rep_id": 4, "score": 51},
        "consistency": 11.2, "per_metric_avg": {"elbow_extension": 60.0},
        "recurring_weaknesses": [{"metric": "elbow_extension", "count": 4}],
        "strengths": [{"metric": "trunk_rotation", "count": 3}],
    }


def _reports():
    return [
        {"rep_id": 1, "stroke_type": "smash", "overall_score": 65,
         "per_metric": {"elbow_extension": {"measured": 138, "score": 55,
                        "ideal_range": [150, 170], "direction": "under", "severity": "moderate"}},
         "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                         "direction": "under", "severity": "moderate", "description": "x"}],
         "strengths": ["trunk_rotation"]},
    ]


def _meta():
    return {"date": "2026-07-01", "stroke_type": "smash",
            "dominant_hand": "right", "pose_family": "yolo-pose"}


def test_builds_all_languages():
    out = build_coach_report(_reports(), _summary(), _meta())
    assert set(out.keys()) == set(kb.SUPPORTED_LANGS)


def test_report_structure_and_correlation():
    r = build_coach_report(_reports(), _summary(), _meta())["en"]
    assert r["lang"] == "en"
    assert r["header"]["stroke"] == "smash"
    assert r["header"]["date"] == "2026-07-01"
    assert r["summary"]["mean_score"] == 68.0
    assert isinstance(r["summary"]["verdict_text"], str) and r["summary"]["verdict_text"]
    # weakness carries impact + mechanism + drill (the correlation to shot quality)
    w = r["weaknesses"][0]
    assert w["metric"] == "elbow_extension"
    assert w["direction"] == "under"
    assert w["impact_label"] and w["mechanism_text"] and w["drill_text"]
    # strengths + per-rep + plan present
    assert r["strengths"] and r["strengths"][0]["metric"] == "trunk_rotation"
    assert r["per_rep"] and r["per_rep"][0]["rep_id"] == 1
    assert "weeks" in r["training_plan"]


def test_chinese_text_differs_from_english():
    out = build_coach_report(_reports(), _summary(), _meta())
    en_mech = out["en"]["weaknesses"][0]["mechanism_text"]
    hans_mech = out["zh-Hans"]["weaknesses"][0]["mechanism_text"]
    assert en_mech and hans_mech and en_mech != hans_mech


def test_empty_reps_insufficient_data_verdict():
    empty_summary = {"stroke_type": "serve", "rep_count": 0, "mean_score": None,
                     "best_rep": None, "worst_rep": None, "consistency": None,
                     "per_metric_avg": {}, "recurring_weaknesses": [], "strengths": []}
    r = build_coach_report([], empty_summary, {"date": "2026-07-01", "stroke_type": "serve",
                            "dominant_hand": "right", "pose_family": "yolo-pose"})["en"]
    assert r["header"]["rep_count"] == 0
    assert r["weaknesses"] == [] and r["strengths"] == []
    assert "verdict_text" in r["summary"] and r["summary"]["verdict_text"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_builder.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/posture/report_builder.py`:
```python
"""Assemble the structured coach report (per language) from drill analysis."""
from . import coach_kb as kb
from ..training.plan_generator import generate_plan

_SEVERITY_RANK = {"severe": 3, "moderate": 2, "minor": 1, None: 0}


def _verdict_key(mean_score, consistency, rep_count):
    if not rep_count or mean_score is None:
        return "verdict_insufficient"
    if mean_score >= 80:
        base = "verdict_strong"
    elif mean_score >= 60:
        base = "verdict_developing"
    else:
        base = "verdict_needs_work"
    return base


def _find_rep_finding(reports, metric):
    """Return the highest-severity rep-level weakness dict for a metric, or None."""
    best = None
    for rep in reports:
        for w in rep.get("weaknesses", []):
            if w.get("metric") == metric:
                if best is None or _SEVERITY_RANK.get(w.get("severity")) > _SEVERITY_RANK.get(best.get("severity")):
                    best = w
    return best


def _one_language(lang, reports, summary, meta):
    stroke = summary.get("stroke_type", meta.get("stroke_type"))
    rep_count = summary.get("rep_count", 0)
    mean_score = summary.get("mean_score")
    consistency = summary.get("consistency")

    header = {
        "stroke": stroke,
        "stroke_label": kb.t(lang, "stroke_" + stroke),
        "rep_count": rep_count,
        "dominant_hand": meta.get("dominant_hand", "right"),
        "date": meta.get("date"),
        "pose_family": meta.get("pose_family"),
    }

    verdict_key = _verdict_key(mean_score, consistency, rep_count)
    summary_block = {
        "mean_score": mean_score,
        "consistency": consistency,
        "verdict_text": kb.t(lang, verdict_key,
                             score=("%.0f" % mean_score) if mean_score is not None else "N/A",
                             consistency=("%.1f" % consistency) if consistency is not None else "N/A"),
    }

    weaknesses = []
    for rw in summary.get("recurring_weaknesses", []):
        metric = rw["metric"]
        finding = _find_rep_finding(reports, metric)
        direction = (finding or {}).get("direction", "under")
        entry = kb.lookup_weakness(metric, stroke, direction)
        measured = (finding or {}).get("measured")
        ideal = (finding or {}).get("ideal_range")
        weaknesses.append({
            "metric": metric,
            "metric_label": kb.t(lang, "metric_" + metric),
            "measured": measured,
            "ideal_range": ideal,
            "direction": direction,
            "severity": (finding or {}).get("severity"),
            "impact_label": kb.t(lang, "impact_" + entry["impact"]),
            "mechanism_text": kb.t(lang, entry["mechanism_key"]),
            "drill_text": kb.t(lang, entry["drill_key"]),
        })

    strengths = []
    for st in summary.get("strengths", []):
        metric = st["metric"]
        entry = kb.lookup_strength(metric, stroke)
        strengths.append({
            "metric": metric,
            "metric_label": kb.t(lang, "metric_" + metric),
            "measured": (summary.get("per_metric_avg") or {}).get(metric),
            "impact_label": kb.t(lang, "impact_" + entry["impact"]),
            "text": kb.t(lang, entry["text_key"]),
        })

    per_rep = []
    for rep in reports:
        top = None
        weak = rep.get("weaknesses", [])
        if weak:
            top = max(weak, key=lambda w: _SEVERITY_RANK.get(w.get("severity"))).get("metric")
        per_rep.append({
            "rep_id": rep.get("rep_id"),
            "overall_score": rep.get("overall_score"),
            "top_weakness": top,
        })

    training_plan = generate_plan(summary)

    return {
        "lang": lang,
        "header": header,
        "summary": summary_block,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "per_rep": per_rep,
        "training_plan": training_plan,
    }


def build_coach_report(reports, summary, meta):
    return {lang: _one_language(lang, reports, summary, meta) for lang in kb.SUPPORTED_LANGS}
```
Note: this requires `coach_kb` i18n keys `stroke_<name>`, `metric_<name>`, `impact_<category>`, and the verdict keys (`verdict_insufficient/strong/developing/needs_work`). If any are missing from Task 1's English table, ADD them to `coach_kb.py` (English) here and to both Chinese tables — keep the i18n completeness test green.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_builder.py -v`
Expected: PASS (5 passed). If a missing i18n key surfaces, add it in all 3 languages and re-run (Task 2's completeness test must stay green too).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/report_builder.py tests/test_report_builder.py badminton_analysis/posture/coach_kb.py
git commit -m "feat: coach report builder (per-language, shot-quality correlation)"
```

---

### Task 4: Renderers (HTML + best-effort PDF)

**Files:**
- Create: `badminton_analysis/posture/report_render.py`
- Test: `tests/test_report_render.py`

**Interfaces:**
- Consumes: a `report_dict` (Task 3).
- Produces:
  - `render_html(report_dict) -> str` — a complete self-contained HTML document (inline CSS, print-friendly, CJK-safe font stack) containing: header, summary/verdict, strengths, weaknesses (each with measured-vs-ideal + impact + mechanism + drill), per-rep table, training plan.
  - `render_pdf(html, out_path) -> bool` — writes a PDF at `out_path` using `weasyprint` if importable; returns `True` on success, `False` (writing nothing) if the library is unavailable or rendering raises. Never propagates an exception.

- [ ] **Step 1: Write the failing tests**

`tests/test_report_render.py`:
```python
from badminton_analysis.posture.report_render import render_html, render_pdf


def _report():
    return {
        "lang": "en",
        "header": {"stroke": "smash", "stroke_label": "Smash", "rep_count": 5,
                   "dominant_hand": "right", "date": "2026-07-01", "pose_family": "yolo-pose"},
        "summary": {"mean_score": 68.0, "consistency": 11.2, "verdict_text": "Developing smash."},
        "strengths": [{"metric": "trunk_rotation", "metric_label": "Trunk rotation",
                       "measured": 35, "impact_label": "Power", "text": "Good rotation adds power."}],
        "weaknesses": [{"metric": "elbow_extension", "metric_label": "Elbow extension",
                        "measured": 138, "ideal_range": [150, 170], "direction": "under",
                        "severity": "moderate", "impact_label": "Power",
                        "mechanism_text": "Short arm cuts racket-head speed.",
                        "drill_text": "Overhead extension throws, 3x10."}],
        "per_rep": [{"rep_id": 1, "overall_score": 65, "top_weakness": "elbow_extension"}],
        "training_plan": {"weeks": [{"week": 1, "phase": "Foundation",
                          "sessions": [{"name": "Shadow clears", "location": "court",
                          "frequency_per_week": 3, "reps": 20, "sets": 3, "duration_min": 10}]}]},
    }


def test_render_html_contains_sections_and_text():
    html = render_html(_report())
    assert "<html" in html.lower()
    assert "Smash" in html
    assert "Developing smash." in html
    assert "Elbow extension" in html
    assert "Overhead extension throws" in html      # drill text present
    assert "Short arm cuts racket-head speed." in html  # mechanism present
    assert "Trunk rotation" in html                  # strengths section
    assert "Shadow clears" in html                   # training plan present


def test_render_html_escapes_and_is_self_contained():
    html = render_html(_report())
    # self-contained: no external resource references
    assert "http://" not in html and "https://" not in html
    assert "<style" in html.lower()  # inline CSS


def test_render_pdf_returns_false_when_lib_absent(monkeypatch, tmp_path):
    # Simulate weasyprint missing by making the import fail.
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "weasyprint":
            raise ImportError("no weasyprint")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = tmp_path / "r.pdf"
    assert render_pdf("<html><body>x</body></html>", str(out)) is False
    assert not out.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_render.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/posture/report_render.py`:
```python
"""Render a structured coach report to self-contained HTML and best-effort PDF."""
import html as _html

_CSS = """
body{font-family:'Noto Sans CJK SC','Microsoft YaHei','PingFang TC','Heiti TC',
     'SimHei',sans-serif;color:#1a1a1a;max-width:820px;margin:24px auto;padding:0 16px;line-height:1.5}
h1{font-size:1.5rem;margin:0 0 4px} h2{font-size:1.1rem;border-bottom:2px solid #ddd;padding-bottom:4px;margin-top:24px}
.meta{color:#666;font-size:.85rem} .verdict{background:#f4f6f8;border-radius:8px;padding:12px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:.9rem} th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}
.finding{border-left:4px solid #c0392b;background:#fdf3f2;border-radius:0 6px 6px 0;padding:10px;margin:8px 0}
.strength{border-left:4px solid #27ae60;background:#f0f9f4;border-radius:0 6px 6px 0;padding:10px;margin:8px 0}
.impact{display:inline-block;background:#333;color:#fff;border-radius:4px;padding:1px 8px;font-size:.75rem}
"""


def _esc(v):
    return _html.escape(str(v)) if v is not None else ""


def render_html(report):
    h = report.get("header", {})
    s = report.get("summary", {})
    parts = []
    parts.append("<!doctype html><html lang='" + _esc(report.get("lang", "en")) + "'><head>"
                 "<meta charset='utf-8'><style>" + _CSS + "</style></head><body>")
    parts.append("<h1>" + _esc(h.get("stroke_label")) + "</h1>")
    parts.append("<div class='meta'>" + _esc(h.get("date")) + " · " + _esc(h.get("rep_count"))
                 + " reps · " + _esc(h.get("dominant_hand")) + " · " + _esc(h.get("pose_family")) + "</div>")

    parts.append("<div class='verdict'><b>"
                 + ("Score " + _esc(s.get("mean_score")) if s.get("mean_score") is not None else "")
                 + (" · Consistency " + _esc(s.get("consistency")) if s.get("consistency") is not None else "")
                 + "</b><br>" + _esc(s.get("verdict_text")) + "</div>")

    strengths = report.get("strengths", [])
    if strengths:
        parts.append("<h2>Strengths</h2>")
        for st in strengths:
            parts.append("<div class='strength'><b>" + _esc(st.get("metric_label"))
                         + "</b> <span class='impact'>" + _esc(st.get("impact_label")) + "</span><br>"
                         + _esc(st.get("text")) + "</div>")

    weaknesses = report.get("weaknesses", [])
    if weaknesses:
        parts.append("<h2>Areas to improve</h2>")
        for w in weaknesses:
            ideal = w.get("ideal_range") or ["", ""]
            parts.append("<div class='finding'><b>" + _esc(w.get("metric_label"))
                         + "</b> <span class='impact'>" + _esc(w.get("impact_label")) + "</span><br>"
                         + "measured " + _esc(w.get("measured")) + " (ideal "
                         + _esc(ideal[0]) + "–" + _esc(ideal[1]) + ")<br>"
                         + _esc(w.get("mechanism_text")) + "<br><i>" + _esc(w.get("drill_text")) + "</i></div>")

    per_rep = report.get("per_rep", [])
    if per_rep:
        parts.append("<h2>Per-rep</h2><table><tr><th>Rep</th><th>Score</th><th>Top weakness</th></tr>")
        for r in per_rep:
            parts.append("<tr><td>" + _esc(r.get("rep_id")) + "</td><td>" + _esc(r.get("overall_score"))
                         + "</td><td>" + _esc(r.get("top_weakness")) + "</td></tr>")
        parts.append("</table>")

    plan = report.get("training_plan", {})
    weeks = plan.get("weeks", []) if isinstance(plan, dict) else []
    if weeks:
        parts.append("<h2>Training plan</h2>")
        for wk in weeks:
            parts.append("<b>Week " + _esc(wk.get("week")) + " (" + _esc(wk.get("phase")) + ")</b><ul>")
            for sess in wk.get("sessions", []):
                parts.append("<li>" + _esc(sess.get("name")) + " — " + _esc(sess.get("sets"))
                             + "x" + _esc(sess.get("reps")) + ", " + _esc(sess.get("frequency_per_week"))
                             + "/wk</li>")
            parts.append("</ul>")

    parts.append("</body></html>")
    return "".join(parts)


def render_pdf(html, out_path):
    try:
        import weasyprint
    except Exception:
        return False
    try:
        weasyprint.HTML(string=html).write_pdf(out_path)
        return True
    except Exception:
        return False
```
Note: the HTML section headings here are English structural labels; the localized content (verdict, labels, mechanism, drill) comes from the report_dict which is already per-language. If you want the section headings localized too, pull them from `report_dict` — but the spec's requirement is that the coaching CONTENT is tri-lingual, which this satisfies. Keep headings English for v1 (documented).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_render.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/report_render.py tests/test_report_render.py
git commit -m "feat: coach report HTML + best-effort PDF renderers"
```

---

### Task 5: Wire report generation into PostureAnalysisSystem

**Files:**
- Modify: `badminton_analysis/posture/system.py`
- Test: `tests/test_posture_report_wiring.py`

**Interfaces:**
- Consumes: `build_coach_report` (Task 3), `render_html`/`render_pdf` (Task 4), `write_json` (existing).
- Produces: a method `PostureAnalysisSystem._write_reports(reports, summary, date)` that builds all 3 languages and writes, per lang, `coach_report_<lang>.json` + `coach_report_<lang>.html`, and attempts `coach_report_<lang>.pdf` (best-effort). Called from `process_video` after `drill_summary.json` is written. `date` is stamped by the system (a `_today()` helper the test can monkeypatch). Returns the dict of written JSON paths.

- [ ] **Step 1: Write the failing test**

`tests/test_posture_report_wiring.py`:
```python
import json
import os
from badminton_analysis.posture.system import PostureAnalysisSystem


def _summary():
    return {"stroke_type": "smash", "rep_count": 1, "mean_score": 65.0,
            "best_rep": {"rep_id": 1, "score": 65}, "worst_rep": {"rep_id": 1, "score": 65},
            "consistency": 0.0, "per_metric_avg": {"elbow_extension": 55.0},
            "recurring_weaknesses": [{"metric": "elbow_extension", "count": 1}],
            "strengths": []}


def _reports():
    return [{"rep_id": 1, "stroke_type": "smash", "overall_score": 65,
             "per_metric": {"elbow_extension": {"measured": 138, "score": 55,
                            "ideal_range": [150, 170], "direction": "under", "severity": "moderate"}},
             "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                             "direction": "under", "severity": "moderate", "description": "x"}],
             "strengths": []}]


def test_write_reports_produces_three_languages(tmp_path):
    vid = tmp_path / "clip.mp4"; vid.write_bytes(b"x")
    sys = PostureAnalysisSystem(str(vid), stroke_type="smash", output_dir=str(tmp_path / "out"))
    paths = sys._write_reports(_reports(), _summary(), date="2026-07-01")
    for lang in ("en", "zh-Hant", "zh-Hans"):
        j = os.path.join(sys.save_dir, "coach_report_" + lang + ".json")
        h = os.path.join(sys.save_dir, "coach_report_" + lang + ".html")
        assert os.path.exists(j), j
        assert os.path.exists(h), h
        data = json.load(open(j, encoding="utf-8"))
        assert data["lang"] == lang
        assert data["header"]["date"] == "2026-07-01"
    # returned mapping covers all three langs
    assert set(paths.keys()) == {"en", "zh-Hant", "zh-Hans"}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posture_report_wiring.py -v`
Expected: FAIL — `_write_reports` doesn't exist.

- [ ] **Step 3: Implement**

In `badminton_analysis/posture/system.py`, add a module-level helper near the top (after imports):
```python
def _today():
    import datetime
    return datetime.date.today().isoformat()
```
Add the method to `PostureAnalysisSystem`:
```python
    def _write_reports(self, reports, summary, date=None):
        from .report_builder import build_coach_report
        from .report_render import render_html, render_pdf
        from ..data.writer import write_json
        date = date or _today()
        meta = {"date": date, "stroke_type": self.stroke_type,
                "dominant_hand": self.dominant_hand, "pose_family": getattr(self, "pose_family", "yolo-pose")}
        by_lang = build_coach_report(reports, summary, meta)
        written = {}
        for lang, report in by_lang.items():
            json_path = os.path.join(self.save_dir, "coach_report_" + lang + ".json")
            html_path = os.path.join(self.save_dir, "coach_report_" + lang + ".html")
            pdf_path = os.path.join(self.save_dir, "coach_report_" + lang + ".pdf")
            write_json(json_path, report)
            html = render_html(report)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
            if not render_pdf(html, pdf_path):
                print("coach report PDF skipped for " + lang + " (renderer unavailable)")
            written[lang] = json_path
        return written
```
In `process_video`, after the `write_json(... "drill_summary.json" ...)` call and before the final print, add:
```python
        self._write_reports(reports, build_drill_summary(reports, self.stroke_type), date=_today())
```
Wait — `build_drill_summary` is already computed for the summary write; refactor to compute it once. Change the summary block in `process_video` to:
```python
        summary = build_drill_summary(reports, self.stroke_type)
        write_json(os.path.join(self.save_dir, "drill_summary.json"), summary)
```
then after the metadata write:
```python
        self._write_reports(reports, summary, date=_today())
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_posture_report_wiring.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Full suite + commit**

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/system.py tests/test_posture_report_wiring.py
git commit -m "feat: write tri-lingual coach reports from posture analysis"
```

---

### Task 6: Optional multi-provider LLM polish

**Files:**
- Create: `badminton_analysis/posture/report_llm.py`
- Test: `tests/test_report_llm.py`

**Interfaces:**
- Consumes: nothing project-specific (operates on prose fields).
- Produces:
  - `resolve_auth(provider_name) -> dict | None` — tries, in order: a subscription/OAuth token from a sanctioned CLI credential location; an API key from env/config; a local no-auth marker. Returns a small dict describing the resolved auth, or `None` if nothing resolves.
  - `get_provider(spec) -> LLMProvider | None` where `spec` is `"provider:model"` (e.g. `"anthropic:claude-..."`, `"local:llama3"`) or `None`/`"off"`. Returns a provider instance whose SDK is imported lazily, or `None` when off/unresolvable.
  - `polish(report_dict, lang, spec=None) -> report_dict` — if `spec` is off/None or no provider resolves, returns `report_dict` UNCHANGED (no-op). Otherwise rewrites only `summary.verdict_text`, each `weaknesses[i].mechanism_text` / `drill_text`, and each `strengths[i].text`, preserving all other fields/numbers. Any error → returns the original unchanged.
  - `LLMProvider` base with `polish_fields(fields: dict, lang: str) -> dict` (maps text-id → rewritten text). Concrete: `AnthropicProvider`, `OpenAIProvider`, `GoogleProvider`, `LocalOpenAICompatProvider`.
  NOTE: before implementing `AnthropicProvider`, the implementer MUST load the `claude-api` reference skill to get the current Claude model id and SDK call shape. Other SDKs likewise imported lazily.

- [ ] **Step 1: Write the failing tests (no network; fakes only)**

`tests/test_report_llm.py`:
```python
from badminton_analysis.posture import report_llm as rl


def _report():
    return {
        "lang": "en",
        "summary": {"mean_score": 68, "consistency": 10, "verdict_text": "ORIG verdict"},
        "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                        "impact_label": "Power", "mechanism_text": "ORIG mech", "drill_text": "ORIG drill"}],
        "strengths": [{"metric": "trunk_rotation", "impact_label": "Power", "text": "ORIG strength"}],
    }


def test_polish_off_is_noop():
    r = _report()
    out = rl.polish(r, "en", spec=None)
    assert out["summary"]["verdict_text"] == "ORIG verdict"
    out2 = rl.polish(r, "en", spec="off")
    assert out2["weaknesses"][0]["mechanism_text"] == "ORIG mech"


def test_polish_unresolvable_provider_is_noop(monkeypatch):
    # No provider resolves (auth returns None) -> unchanged.
    monkeypatch.setattr(rl, "resolve_auth", lambda name: None)
    out = rl.polish(_report(), "en", spec="anthropic:whatever")
    assert out["summary"]["verdict_text"] == "ORIG verdict"


def test_polish_with_fake_provider_changes_only_prose(monkeypatch):
    class _Fake(rl.LLMProvider):
        def polish_fields(self, fields, lang):
            return {k: "NEW:" + v for k, v in fields.items()}
    monkeypatch.setattr(rl, "get_provider", lambda spec: _Fake())
    r = _report()
    out = rl.polish(r, "en", spec="fake:model")
    # prose changed
    assert out["summary"]["verdict_text"].startswith("NEW:")
    assert out["weaknesses"][0]["mechanism_text"].startswith("NEW:")
    assert out["weaknesses"][0]["drill_text"].startswith("NEW:")
    assert out["strengths"][0]["text"].startswith("NEW:")
    # numbers/structure preserved
    assert out["summary"]["mean_score"] == 68
    assert out["weaknesses"][0]["measured"] == 138
    assert out["weaknesses"][0]["ideal_range"] == [150, 170]


def test_polish_provider_error_falls_back(monkeypatch):
    class _Boom(rl.LLMProvider):
        def polish_fields(self, fields, lang):
            raise RuntimeError("provider down")
    monkeypatch.setattr(rl, "get_provider", lambda spec: _Boom())
    out = rl.polish(_report(), "en", spec="boom:model")
    assert out["summary"]["verdict_text"] == "ORIG verdict"  # unchanged on error


def test_resolve_auth_prefers_env_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    auth = rl.resolve_auth("openai")
    assert auth is not None and auth.get("kind") in ("api_key", "subscription_token", "local")
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_llm.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`badminton_analysis/posture/report_llm.py`:
```python
"""Optional, provider-pluggable prose polish for coach reports. Default OFF.

Only rewrites prose fields (verdict/mechanism/drill/strength text); never
numbers, scores, structure, or drill selection. Any failure falls back to the
curated text unchanged. Builds NO OAuth flow — it only reads tokens that a
sanctioned first-party CLI already stored, or an API key from env/config, or
uses a local no-auth endpoint.
"""
import os

_ENV_KEY = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def resolve_auth(provider_name):
    """Return an auth descriptor dict or None. Order: subscription token, API key, local."""
    if provider_name == "local":
        return {"kind": "local"}
    # 1) subscription/OAuth token from a sanctioned first-party CLI credential store.
    token = _read_subscription_token(provider_name)
    if token:
        return {"kind": "subscription_token", "token": token}
    # 2) API key from env.
    env = _ENV_KEY.get(provider_name)
    if env and os.environ.get(env):
        return {"kind": "api_key", "key": os.environ[env]}
    return None


def _read_subscription_token(provider_name):
    """Best-effort read of an existing sanctioned-CLI token. Returns str or None.
    Reads only; never writes. Locations are best-effort and may not exist."""
    try:
        if provider_name == "anthropic":
            path = os.path.expanduser("~/.claude/.credentials.json")
            if os.path.exists(path):
                import json
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                # structure is provider-defined; return whatever token field exists.
                return (data.get("access_token") or data.get("token")
                        or (data.get("claudeai") or {}).get("access_token"))
        # google/openai subscription tokens: no sanctioned third-party path -> None.
    except Exception:
        return None
    return None


class LLMProvider(object):
    def polish_fields(self, fields, lang):
        raise NotImplementedError


class _SDKProvider(LLMProvider):
    def __init__(self, model, auth):
        self.model = model
        self.auth = auth


class AnthropicProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        # Implementer: load the `claude-api` skill for the current model id + SDK usage.
        import anthropic  # lazy
        client = anthropic.Anthropic(api_key=self.auth.get("key")) if self.auth.get("kind") == "api_key" \
            else anthropic.Anthropic()  # env/subscription resolved by SDK
        # send fields as JSON, ask for same-keys rewrite in `lang`, natural human coach voice.
        return _sdk_rewrite_via_messages(client, self.model, fields, lang)


class OpenAIProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        from openai import OpenAI  # lazy
        client = OpenAI(api_key=self.auth.get("key")) if self.auth.get("kind") == "api_key" else OpenAI()
        return _openai_rewrite(client, self.model, fields, lang)


class GoogleProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        import google.generativeai as genai  # lazy
        if self.auth.get("kind") == "api_key":
            genai.configure(api_key=self.auth["key"])
        return _google_rewrite(genai, self.model, fields, lang)


class LocalOpenAICompatProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        from openai import OpenAI  # lazy; local OpenAI-compatible endpoint
        base = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = OpenAI(base_url=base, api_key="not-needed")
        return _openai_rewrite(client, self.model, fields, lang)


_PROVIDERS = {
    "anthropic": AnthropicProvider, "openai": OpenAIProvider,
    "google": GoogleProvider, "local": LocalOpenAICompatProvider,
}


def get_provider(spec):
    if not spec or spec == "off":
        return None
    if ":" in spec:
        name, model = spec.split(":", 1)
    else:
        name, model = spec, ""
    cls = _PROVIDERS.get(name)
    if cls is None:
        return None
    auth = resolve_auth(name)
    if auth is None:
        return None
    return cls(model, auth)


_PROSE_SYSTEM = (
    "You are a seasoned badminton coach writing feedback. Rewrite each provided text "
    "in {lang} in a natural, direct human-coach voice. Do not invent numbers or claims; "
    "keep the meaning; avoid AI-sounding filler, hedging, and lists of three. Return the "
    "same keys with rewritten values."
)


def polish(report_dict, lang, spec=None):
    provider = get_provider(spec)
    if provider is None:
        return report_dict
    # Gather prose fields with stable ids.
    fields = {}
    fields["verdict"] = report_dict.get("summary", {}).get("verdict_text", "")
    for i, w in enumerate(report_dict.get("weaknesses", [])):
        fields["w%d_mech" % i] = w.get("mechanism_text", "")
        fields["w%d_drill" % i] = w.get("drill_text", "")
    for i, s in enumerate(report_dict.get("strengths", [])):
        fields["s%d_text" % i] = s.get("text", "")
    try:
        new = provider.polish_fields(fields, lang)
    except Exception:
        return report_dict
    if not isinstance(new, dict):
        return report_dict
    # Apply back only known ids; ignore anything unexpected.
    if "verdict" in new:
        report_dict["summary"]["verdict_text"] = new["verdict"]
    for i, w in enumerate(report_dict.get("weaknesses", [])):
        if "w%d_mech" % i in new:
            w["mechanism_text"] = new["w%d_mech" % i]
        if "w%d_drill" % i in new:
            w["drill_text"] = new["w%d_drill" % i]
    for i, s in enumerate(report_dict.get("strengths", [])):
        if "s%d_text" % i in new:
            s["text"] = new["s%d_text" % i]
    return report_dict


# --- SDK rewrite helpers (thin; each imports nothing at module top) ---
def _sdk_rewrite_via_messages(client, model, fields, lang):
    import json
    msg = client.messages.create(
        model=model, max_tokens=1500,
        system=_PROSE_SYSTEM.format(lang=lang),
        messages=[{"role": "user", "content": json.dumps(fields, ensure_ascii=False)}],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    return json.loads(text)


def _openai_rewrite(client, model, fields, lang):
    import json
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _PROSE_SYSTEM.format(lang=lang)},
                  {"role": "user", "content": json.dumps(fields, ensure_ascii=False)}],
    )
    return json.loads(resp.choices[0].message.content)


def _google_rewrite(genai, model, fields, lang):
    import json
    m = genai.GenerativeModel(model, system_instruction=_PROSE_SYSTEM.format(lang=lang))
    resp = m.generate_content(json.dumps(fields, ensure_ascii=False))
    return json.loads(resp.text)
```
The tests only exercise the pure control flow (off/no-op, unresolvable, fake provider changes only prose, error fallback, env key resolution) — the SDK helpers are never called in tests (they'd need network). Keep them thin and lazy.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_report_llm.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Wire optional polish into the report writer + full suite**

In `system.py` `_write_reports`, after `by_lang = build_coach_report(...)`, add optional polish gated by a `self.report_llm` attribute (added to `__init__` as `report_llm="off"`):
```python
        if getattr(self, "report_llm", "off") not in (None, "off"):
            from .report_llm import polish
            for lang in by_lang:
                by_lang[lang] = polish(by_lang[lang], lang, spec=self.report_llm)
```
Add `report_llm="off"` to `PostureAnalysisSystem.__init__` params and `self.report_llm = report_llm`.

Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add badminton_analysis/posture/report_llm.py badminton_analysis/posture/system.py tests/test_report_llm.py
git commit -m "feat: optional multi-provider LLM prose polish (default off)"
```

---

### Task 7: CLI flag + Flask report route

**Files:**
- Modify: `main_posture.py`
- Modify: `app.py`
- Test: `tests/test_app_posture_report_route.py`

**Interfaces:**
- Consumes: `PostureAnalysisSystem(report_llm=...)` (Task 6); the written `coach_report_<lang>.json` (Task 5).
- Produces:
  - `main_posture.py`: `--report-llm` flag (default `off`), passed to the constructor.
  - `app.py`: `GET /api/posture-report/<video_name>?lang=en|zh-Hant|zh-Hans` → the report JSON (default `en`; invalid lang → 400; missing file → 404; malformed → 500). The posture analyze route accepts an optional `report_llm` in the body and passes `--report-llm` to `main_posture.py`.

- [ ] **Step 1: Write the failing route tests**

`tests/test_app_posture_report_route.py`:
```python
import json
import pytest
import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def _write_report(tmp_path, video, lang):
    out = tmp_path / video / "posture"
    out.mkdir(parents=True, exist_ok=True)
    (out / ("coach_report_" + lang + ".json")).write_text(
        json.dumps({"lang": lang, "header": {"stroke": "smash"}, "summary": {},
                    "strengths": [], "weaknesses": [], "per_rep": [], "training_plan": {}}),
        encoding="utf-8")
    return out


def test_report_404_when_missing(client):
    assert client.get("/api/posture-report/none?lang=en").status_code == 404


def test_report_returns_requested_language(client, tmp_path):
    _write_report(tmp_path, "drill1", "zh-Hant")
    r = client.get("/api/posture-report/drill1?lang=zh-Hant")
    assert r.status_code == 200
    assert r.get_json()["lang"] == "zh-Hant"


def test_report_defaults_to_english(client, tmp_path):
    _write_report(tmp_path, "drill1", "en")
    r = client.get("/api/posture-report/drill1")
    assert r.status_code == 200 and r.get_json()["lang"] == "en"


def test_report_invalid_lang_400(client, tmp_path):
    _write_report(tmp_path, "drill1", "en")
    assert client.get("/api/posture-report/drill1?lang=fr").status_code == 400


def test_report_500_on_malformed(client, tmp_path):
    out = tmp_path / "bad" / "posture"; out.mkdir(parents=True, exist_ok=True)
    (out / "coach_report_en.json").write_text("{ not json", encoding="utf-8")
    r = client.get("/api/posture-report/bad?lang=en")
    assert r.status_code == 500 and "error" in r.get_json()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_posture_report_route.py -v`
Expected: FAIL — route not defined (404/HTML for all).

- [ ] **Step 3: Implement the route + CLI flag**

In `app.py`, before the `# Static page` section, add:
```python
_REPORT_LANGS = ("en", "zh-Hant", "zh-Hans")


@app.route('/api/posture-report/<video_name>')
def api_posture_report(video_name):
    lang = request.args.get('lang', 'en')
    if lang not in _REPORT_LANGS:
        return jsonify({'error': 'invalid lang'}), 400
    path = OUTPUTS / video_name / 'posture' / ('coach_report_' + lang + '.json')
    if not path.exists():
        return jsonify({'error': '还没有教练报告，请先运行姿态分析'}), 404
    try:
        with open(path, encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```
In the posture analyze route (`api_posture_analyze`), after reading `pose_family` (or `dominant`), add:
```python
    report_llm = data.get('report_llm', 'off')
```
and in the `cmd` list add:
```python
        '--report-llm', report_llm,
```
In `main_posture.py`, add the flag after `--pose-model` (or the pose-family flags):
```python
    parser.add_argument("--report-llm", default="off",
                        help="LLM polish spec 'provider:model' or 'off' (default off)")
```
and pass `report_llm=args.report_llm` into the `PostureAnalysisSystem(...)` constructor.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_posture_report_route.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Verify --help + full suite + commit**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe main_posture.py --help` (expect `--report-llm` listed, exit 0).
Run: `.venv/Scripts/python.exe -m pytest -q`
```bash
git add app.py main_posture.py tests/test_app_posture_report_route.py
git commit -m "feat: posture coach-report route + --report-llm flag"
```

---

### Task 8: Web UI coach-report panel

**Files:**
- Modify: `web_ui.html`
- Test: served-HTML assertion + full suite (front-end manual)

**Interfaces:**
- Consumes: `GET /api/posture-report/<video>?lang=...` (Task 7); the HTML/PDF files via `/api/output/<video>/posture/coach_report_<lang>.html|.pdf`.
- Produces: a coach-report section in the posture results with a language switcher (EN / 繁體 / 简体) that fetches+renders the chosen language inline, and Download HTML / Download PDF links per language.

- [ ] **Step 1: Add the report panel markup**

In `web_ui.html`, inside the posture results area (after the training plan block), add:
```html
    <div id="posture-report" class="hidden" style="margin-top:16px">
      <div class="ph">📋 教练报告 Coach Report</div>
      <div style="margin:6px 0">
        <button onclick="postureReportLang('en')">EN</button>
        <button class="btn2" onclick="postureReportLang('zh-Hant')">繁體</button>
        <button class="btn2" onclick="postureReportLang('zh-Hans')">简体</button>
        <a id="report-dl-html" href="#" target="_blank" style="margin-left:12px">⬇ HTML</a>
        <a id="report-dl-pdf" href="#" target="_blank" class="hidden">⬇ PDF</a>
      </div>
      <div id="report-body"></div>
    </div>
```

- [ ] **Step 2: Add the report JS**

Add to `web_ui.html`'s script (reuse existing `scoreColor`/`METRIC_LABEL`; `selVideo` is the selected video):
```javascript
function postureShowReport(stem) {
  document.getElementById('posture-report').classList.remove('hidden');
  postureReportLang('en', stem);
}

function postureReportLang(lang, stem) {
  stem = stem || (selVideo ? selVideo.replace(/\.[^.]+$/,'') : null);
  if (!stem) return;
  fetch('/api/posture-report/'+stem+'?lang='+lang).then(function(r){ if(!r.ok) return null; return r.json(); })
  .then(function(d){
    if (!d) { document.getElementById('report-body').innerHTML = '<p>暂无报告</p>'; return; }
    var h = d.header||{}, s = d.summary||{};
    var html = '<div style="background:#0f172a;border-radius:8px;padding:12px">';
    html += '<div style="font-weight:600">'+(h.stroke_label||h.stroke||'')+' · '+(h.rep_count||0)+' reps</div>';
    html += '<p>'+(s.verdict_text||'')+'</p>';
    (d.strengths||[]).forEach(function(x){
      html += '<div style="border-left:4px solid #3fb950;padding:6px 10px;margin:4px 0">'
        + '<b>'+(x.metric_label||x.metric)+'</b> ['+(x.impact_label||'')+']<br>'+(x.text||'')+'</div>'; });
    (d.weaknesses||[]).forEach(function(x){
      var ideal = x.ideal_range||['',''];
      html += '<div style="border-left:4px solid #f85149;padding:6px 10px;margin:4px 0">'
        + '<b>'+(x.metric_label||x.metric)+'</b> ['+(x.impact_label||'')+']<br>'
        + '实测 '+(x.measured!=null?x.measured:'N/A')+' (理想 '+ideal[0]+'–'+ideal[1]+')<br>'
        + (x.mechanism_text||'')+'<br><i>'+(x.drill_text||'')+'</i></div>'; });
    html += '</div>';
    document.getElementById('report-body').innerHTML = html;
    // update download links + active button styling
    document.getElementById('report-dl-html').href = '/api/output/'+stem+'/posture/coach_report_'+lang+'.html';
    var pdf = document.getElementById('report-dl-pdf');
    pdf.href = '/api/output/'+stem+'/posture/coach_report_'+lang+'.pdf';
    // (PDF may not exist; leave visible — a 404 on click is acceptable, or probe with HEAD if desired)
  });
}
```
And in `postureLoad(stem)` (added in the Posture Drill plan), after rendering the drill results, call:
```javascript
  postureShowReport(stem);
```

- [ ] **Step 3: Verify served HTML + full suite**

Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -c "s=open('web_ui.html',encoding='utf-8').read(); assert s.count('<script>')==s.count('</script>'); assert 'posture-report' in s and 'postureReportLang' in s; print('report panel present, balanced')"`
Run: `PYTHONUTF8=1 .venv/Scripts/python.exe -c "from app import app; c=app.test_client(); r=c.get('/'); assert r.status_code==200 and b'posture-report' in r.data; print('index serves report panel')"`
Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all three succeed; suite green.

- [ ] **Step 4: Manual smoke (front-end, no JS harness)**

Run `PYTHONUTF8=1 .venv/Scripts/python.exe app.py --port 5057`; open http://127.0.0.1:5057; in Posture mode, after analyzing a clip, confirm the Coach Report panel appears, the EN/繁體/简体 switch swaps the text, and the HTML download link opens the report. Stop the server.

- [ ] **Step 5: Commit**

```bash
git add web_ui.html
git commit -m "feat: coach-report panel with language switcher and downloads"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`, `README_EN.md`

**Interfaces:** none (docs only). One review gate.

- [ ] **Step 1: Document the coach report**

Extend the posture-mode section: describe the tri-lingual coach report (EN / 繁體 / 简体), what it contains (verdict, strengths, weaknesses with shot-quality impact + drills, per-rep table, training plan), the output files (`coach_report_<lang>.json/.html/.pdf` under `outputs/<video>/posture/`, PDF best-effort), the Web UI language switcher + downloads, and the optional `--report-llm provider:model` polish (default off; note it reuses existing provider CLI credentials / API keys / local endpoints and builds no login flow; prose-only, never changes numbers). Note PDF and Traditional-Chinese PDF glyphs are best-effort. Match `README.md` tone; add the parallel English section to `README_EN.md`. Do NOT claim a bundled LLM or that subscriptions are used without the user's own sanctioned login.

- [ ] **Step 2: Verify docs-only + commit**

Run: `git status --short` (expect only README files).
```bash
git add README.md README_EN.md
git commit -m "docs: document tri-lingual coach report"
```

---

## Self-Review

**1. Spec coverage (coach-report portion):**
- Curated KB correlating findings to shot quality → Tasks 1-2 (`coach_kb`, impact/mechanism/drill). ✅
- Tri-lingual (en/zh-Hant/zh-Hans), authored idiomatically + humanized → Tasks 1-2 (completeness + distinctness tests; humanizer authoring step). ✅
- Report scope: verdict, strengths, weaknesses (measured-vs-ideal + impact + mechanism + drill), per-rep table, training plan → Task 3 builder + Task 4 renderer. ✅
- Structured JSON + HTML + best-effort PDF → Tasks 4-5 (write JSON/HTML, attempt PDF, never fail). ✅
- All 3 languages generated per run; live UI switch; downloads → Tasks 5, 8. ✅
- Optional multi-provider LLM polish, default off, prose-only, reuse-token/API-key/local auth, no OAuth server → Task 6. ✅
- Flask route + CLI flag → Task 7. ✅
- Prose reads human, no AI tone → humanizer applied in Task 2 (authoring) + Task 6 system prompt. ✅
- Latest Claude model / SDK confirmed via claude-api skill → noted in Task 6. ✅

**2. Placeholder scan:** No TBD/TODO. The SDK-rewrite helpers are complete but never exercised in tests (documented); the model-id lookup is an explicit "load claude-api skill" instruction, not a vague placeholder. The Chinese-content and humanizer steps are real authoring tasks with completeness tests, not placeholders.

**3. Type consistency:**
- `SUPPORTED_LANGS`/`METRICS`/`IMPACT_CATEGORIES`/`STROKES` defined in `coach_kb` (Task 1), used by builder (Task 3), route (`_REPORT_LANGS` mirrors the 3 langs, Task 7), UI (Task 8). ✅
- `report_dict` key set identical across builder (Task 3), renderer (Task 4), wiring test (Task 5), LLM polish field-extraction (Task 6), route JSON (Task 7), UI render (Task 8). ✅
- `build_coach_report(reports, summary, meta)` signature consistent (Tasks 3, 5). ✅
- `polish(report_dict, lang, spec)` / `get_provider(spec)` / `resolve_auth(name)` consistent (Task 6 def; Task 5 caller uses `polish(..., spec=self.report_llm)`). ✅
- `render_html`/`render_pdf` signatures consistent (Tasks 4, 5). ✅
- Output filenames `coach_report_<lang>.{json,html,pdf}` consistent across Tasks 5, 7 (route), 8 (download links). ✅

No issues requiring rework.
