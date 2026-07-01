# Posture Drill — Skeleton Overlay + Professional Coach Report — Design

**Date:** 2026-07-01
**Status:** Approved (pending written-spec review)
**Builds on:** the Posture Drill mode (`docs/superpowers/specs/2026-07-01-posture-drill-mode-design.md`).

## Goal

Two additive enhancements to the existing court-free Posture Drill mode:

1. **On-video pose overlay** — draw the human skeleton + keypoints on the annotated output video, with a **selectable pose family** (Ultralytics YOLO Pose / RTMPose / RTMO), matching the capability the README already advertises for match mode.
2. **Professional coach report** — a written analysis correlating each biomechanical finding to its impact on shot quality (power / accuracy / consistency / injury risk), with strengths, weaknesses, corrective drills, a per-rep table, and the training plan. Produced in **English, Traditional Chinese, and Simplified Chinese**, selectable live in the UI, downloadable as HTML and (best-effort) PDF. Prose must read like a real human coach — **no AI-generated tone**.

## Decisions (from brainstorming)

- **Skeleton overlay:** always drawn on the posture output video (skeleton + keypoints), on top of which the existing angle overlay is drawn.
- **Pose family:** selectable (`yolo-pose` default | `rtmpose` | `rtmo`) via CLI flag + UI dropdown, reusing the existing processors.
- **Report correlation content:** **hybrid** — a curated expert knowledge base (KB) is the always-available source of truth; optional LLM polish rewrites only the prose when a provider is configured.
- **Report format:** structured JSON (data) + Web UI rendering + downloadable self-contained HTML + best-effort PDF.
- **Languages:** all three (`en`, `zh-Hant`, `zh-Hans`) generated at once (KB makes this cheap); UI language switcher flips instantly; downloads per language. Traditional and Simplified authored separately (not auto-converted) for idiomatic phrasing.
- **Report scope:** Full report — Summary/verdict, Strengths, Weaknesses (measured-vs-ideal + impact + mechanism + drill), per-rep table, training plan.
- **Prose quality:** the `humanizer` skill is applied to all authored KB templates and to any LLM-polished output; a natural human-coach voice with no AI tells is a hard requirement.
- **LLM polish is multi-provider and optional:** Anthropic (Claude), OpenAI (GPT), Google (Gemini), and local/self-hosted (OpenAI-compatible, e.g. Ollama). Default **off** (KB-only, deterministic, free).
- **Auth:** an auth-source abstraction with three source types — (1) **reuse an existing subscription/OAuth token** that a provider's own sanctioned CLI already stored (e.g. Claude Code credentials, `gcloud` ADC); (2) **API key** from env/config; (3) **local** (no auth). **This app builds NO OAuth login flow of its own** — it only consumes tokens obtained by sanctioned first-party tools. An in-app OAuth login flow, if ever wanted, is a separate future spec.

## Chosen Approach

**Approach A — layered, pure, individually-testable units.** A shared skeleton drawer extracted from existing code; a curated KB (pure data + i18n); a pure report builder; pure renderers (HTML, best-effort PDF); and an isolated optional LLM-polish layer behind provider + auth-source abstractions. No new hard dependency for the core path (PDF is the one optional dependency, isolated and best-effort). The working match pipeline and the existing posture pipeline are extended additively, not restructured.

## Architecture

```
POSTURE PIPELINE (extended)
  PostureAnalysisSystem
    - pose family selectable: YOLOPoseProcessor | RTMPoseProcessor(rtmpose|rtmo)   [NEW]
    - per frame: draw_skeleton(frame, kp, conf) + existing angle overlay           [NEW]
    - post-loop: existing reps -> reports -> drill_summary (unchanged)
    - THEN build coach report:
          build_coach_report(reports, summary, meta) -> {lang: report_dict}
          (optional) report_llm.polish(report_dict, lang, provider)
          render_html / render_pdf per language
                                                     │
COACH REPORT SUBSYSTEM (new, pure/testable)          ▼
  coach_kb.py       (metric,stroke,direction)->{impact,mechanism_key,drill_key}, STRENGTH_KB, I18N[3 langs]
  report_builder.py reports+summary+meta+KB -> structured report dict per language
  report_render.py  report dict -> self-contained HTML ; HTML -> PDF (best-effort CJK)
  report_llm.py     optional prose polish: LLMProvider x AuthSource, default OFF
```

### New modules (under `badminton_analysis/`)
- `visualization/skeleton.py` — `draw_skeleton(frame, keypoints, conf=None, ...)`, the shared COCO-17 drawer.
- `posture/coach_kb.py` — curated KB + i18n string tables (en / zh-Hant / zh-Hans).
- `posture/report_builder.py` — `build_coach_report(...)`.
- `posture/report_render.py` — `render_html`, `render_pdf`.
- `posture/report_llm.py` — provider + auth-source abstractions; `polish(...)` (default no-op).

### Modified
- `visualization/player_pose.py` — `_draw_skeleton_on_frame` refactored to call the shared `draw_skeleton` (match mode behavior unchanged; removes duplicated bone list).
- `posture/system.py` — pose-family selection; call `draw_skeleton`; post-loop report build + writes.
- `main_posture.py` — `--pose-family`, `--pose-mode`, `--report-llm` flags.
- `app.py` — `GET /api/posture-report/<video>?lang=...`; HTML/PDF served by existing output route.
- `web_ui.html` — pose-family dropdown; coach-report panel with language switcher + downloads.

### Reused unchanged
`YOLOPoseProcessor`, `RTMPoseProcessor` (rtmpose/rtmo), `BiomechanicalAnalyzer`, drill reports/summary, `generate_plan`, `write_json`/`clean_value`, existing `/api/output/<video>/<path:subpath>` route.

## Component: shared skeleton drawer (`visualization/skeleton.py`)

`draw_skeleton(frame, keypoints, conf=None, conf_thresh=0.3, line_color=(255,191,0), point_color=(255,128,0)) -> frame`
- COCO-17 bone connections (the 12 existing in `PlayerPoseVisualizer`) + a dot per valid keypoint.
- Skips missing keypoints (`<=1` on both axes) and, when `conf` given, sub-threshold points.
- Mutates and returns the same frame ndarray. `keypoints` is a single `(17,2)` person.
- `PlayerPoseVisualizer._draw_skeleton_on_frame` is refactored to delegate to this (shared bone list; identical match-mode output).

In posture `_capture_frame`: after selecting the person, `draw_skeleton(frame, kp, conf_row)` (always), then the existing angle overlay on top.

## Component: pose-family selection (`posture/system.py`)

`PostureAnalysisSystem.__init__` gains `pose_family="yolo-pose"`, `pose_mode="balanced"`, `yolo_pose_model="weights/yolo11n-pose.pt"`. Construction mirrors the match system:
```
if pose_family == 'yolo-pose':
    processor = YOLOPoseProcessor(model_path=yolo_pose_model)
else:
    processor = RTMPoseProcessor(mode=pose_mode, pose_family=pose_family)  # rtmpose | rtmo
```
Both expose `process_frame(frame) -> (keypoints, scores)`, so the loop is otherwise unchanged. `metadata.json` records `pose_family`. RTMPose/RTMO download their ONNX weights on first use exactly as match mode does (documented). CLI `--pose-family` / `--pose-mode`; UI dropdown.

## Component: coach knowledge base (`coach_kb.py`)

Pure data + i18n. Correlates findings to shot-quality impact.

- `COACH_KB[(metric, stroke, direction)] = {"impact", "severity_hint", "mechanism_key", "drill_key"}` where `impact ∈ {power, accuracy, consistency, injury_risk}`, `direction ∈ {under, over}`.
- **Fallback chain:** specific `(metric,stroke,direction)` → generic `(metric,"*",direction)` → safe generic entry. Avoids a 48-cell explosion while allowing stroke-specific nuance.
- `STRENGTH_KB[(metric, stroke)] = {"impact", "text_key"}` for metrics scoring ≥90 ("what this does well for your shot").
- `I18N[lang][key] = "..."` for `en`, `zh-Hant`, `zh-Hans`: section titles, metric names, impact labels, verdict phrases, every `mechanism_key`/`drill_key`/`text_key`. Traditional and Simplified authored separately.
- Public (pure): `lookup_weakness(metric, stroke, direction)`, `lookup_strength(metric, stroke)`, `t(lang, key, **fmt)`, `METRICS`, `IMPACT_CATEGORIES`, `SUPPORTED_LANGS = ("en","zh-Hant","zh-Hans")`.
- **Metric vocabulary (must match analyzer):** `elbow_extension, trunk_rotation, wrist_flexion, knee_flexion, hip_shoulder_separation, weight_transfer`. **Stroke vocabulary:** `high_clear, smash, drop_shot, serve`.
- **Prose authored with the `humanizer` skill** — natural coach voice, no AI tells.

## Component: report builder (`report_builder.py`)

Pure. `build_coach_report(reports, summary, meta) -> {lang: report_dict}` for each of the 3 languages. Assembles a language-neutral structure once, resolves text per language via `coach_kb.t`. `meta` carries `date`, `stroke_type`, `dominant_hand`, `pose_family` (system stamps `date` — deterministic for tests). Each `report_dict`:
```
{
  "lang": "...",
  "header":   {stroke, stroke_label, rep_count, dominant_hand, date, pose_family},
  "summary":  {mean_score, consistency, verdict_text},        # verdict from score/consistency bands
  "strengths":[{metric, metric_label, measured, impact_label, text}],
  "weaknesses":[{metric, metric_label, measured, ideal_range, direction,
                 severity, impact_label, mechanism_text, drill_text}],
  "per_rep":  [{rep_id, overall_score, top_weakness}],
  "training_plan": {weeks...}                                  # from generate_plan(summary)
}
```
Every weakness ties measured-vs-ideal → impact → mechanism ("why it costs you") → drill ("how to fix") — the "correlate each point to shot outcome" requirement as data. Empty-reps → an "insufficient data" verdict, no crash.

## Component: renderers (`report_render.py`)

Pure formatting.
- `render_html(report_dict) -> str`: a self-contained, inline-CSS, print-friendly HTML document; CJK-safe. Plain Python f-strings, one function per section (no Jinja).
- `render_pdf(html, out_path) -> bool`: HTML→PDF, **best-effort**. Tries `weasyprint` if importable; on absence/failure, writes nothing, returns `False`, logs a note — the run never fails and HTML+JSON are always produced. Traditional-Chinese glyph coverage depends on an available CJK font; documented as best-effort. (The bundled `simhei.ttf` is Simplified; Traditional rendering in PDF is best-effort.)

## Component: optional LLM polish (`report_llm.py`)

Isolated, default **off**. Two small abstractions:

**Provider** — `LLMProvider.polish_fields(fields: dict, lang: str) -> dict` receives only prose fields (`mechanism_text`, `drill_text`, `verdict_text`) and returns rewritten prose; never touches numbers/structure/drill selection. Implementations (SDKs imported lazily, none a hard dependency): `AnthropicProvider` (latest Claude model), `OpenAIProvider`, `GoogleProvider`, `LocalOpenAICompatProvider` (base-url).

**Auth source** — `resolve_auth(provider) -> credentials | None`, tried in order:
1. **SubscriptionToken** — read the OAuth/subscription token a sanctioned first-party CLI already stored (e.g. Claude Code credentials file; `gcloud` ADC). Read-only. No OAuth server built here.
2. **APIKey** — env (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`) or providers config file.
3. **Local** — none (base-url only).

`polish(report_dict, lang, provider=None) -> report_dict`: selection order = explicit UI/CLI choice → first provider whose auth resolves → **no-op** (KB prose unchanged). Any error (missing auth, network, SDK absent, refusal) → silent fallback to KB text. System prompt enforces natural human-coach voice + target language (humanizer principles). Latest Claude model id + SDK usage confirmed against the `claude-api` reference skill at implementation time.

**Config/selection:** a `providers` config (JSON) lists provider+model entries and each entry's auth source; CLI `--report-llm <provider:model>|off` (default off); UI dropdown ("Off — curated report" + configured providers).

**Security:** credentials are only read from standard existing locations/env; nothing is written, logged, or transmitted anywhere except the chosen provider's endpoint. Reusing a provider's subscription token is best-effort and subject to that provider's terms — the user's responsibility (documented).

## Output files (added to `outputs/<video>/posture/`)

```
coach_report_en.json       coach_report_en.html       coach_report_en.pdf        (best-effort)
coach_report_zh-Hant.json  coach_report_zh-Hant.html  coach_report_zh-Hant.pdf   (best-effort)
coach_report_zh-Hans.json  coach_report_zh-Hans.html  coach_report_zh-Hans.pdf   (best-effort)
```
`process_video`, after `drill_summary.json`: build the 3 report dicts, write 3 JSONs, render 3 HTMLs, attempt 3 PDFs. System stamps `date` into `meta`.

## Flask

`GET /api/posture-report/<video_name>?lang=en|zh-Hant|zh-Hans` → the report JSON for that language; 404 if absent; 500 on malformed; `lang` defaults to `en` and validates against `SUPPORTED_LANGS`. HTML/PDF downloads served by the existing `/api/output/<video>/<path:subpath>` route (handles the `posture/` subpath). Uses the standard `try/except → jsonify({'error':...}), 500` convention.

## Web UI (`web_ui.html`, posture panel)

- **Pose-family dropdown** (YOLO Pose / RTMPose / RTMO) next to stroke type; passed to `/api/posture/analyze`.
- **Coach Report panel** below drill results: a language switcher (**EN / 繁體 / 简体**) that fetches and renders the chosen language's report inline (summary verdict, strengths, weaknesses with impact + drill, per-rep table, training plan), plus **Download HTML** / **Download PDF** buttons per language (PDF button hidden when that PDF was not produced). Reuses existing `scoreColor` / `METRIC_LABEL`. Self-contained, no new front-end dependency.

## Error handling

- 0 reps / no pose → report still generated with "insufficient data" verdict; HTML/JSON valid; no crash.
- PDF unavailable (no weasyprint / no CJK font) → HTML+JSON still written; PDF omitted; button hidden; note logged. Never fails the run.
- RTMPose/RTMO weights missing → same fallback/download behavior the match system already has (documented).
- LLM polish error / no provider → silent fallback to KB prose.
- Report routes → structured 404 / JSON 500.

## Testing (TDD, pytest)

- **`draw_skeleton`**: draws on a numpy frame, skips missing/low-conf keypoints, returns the same frame; the refactored `PlayerPoseVisualizer` still draws identically (spot-checked).
- **`coach_kb`**: every KB entry resolves; every referenced text key exists in ALL 3 languages (no missing translations); fallback chain (specific→generic→safe) works.
- **`report_builder`**: structure/keys correct; per-language materialization; weakness impact/mechanism/drill wiring; strengths for ≥90; empty-reps "insufficient data" verdict; deterministic given a fixed `meta.date`.
- **`report_render`**: HTML contains expected sections + finding text; `render_pdf` returns `False` gracefully when the PDF lib is monkeypatched absent.
- **`report_llm`**: no-op with no configured provider / unresolvable auth; auth-source resolution order correct (fake env/credential paths); a fake provider changes only prose fields, leaving numbers/structure intact; provider/SDK errors fall back to KB. No real network.
- **Flask**: `/api/posture-report` via test client (`OUTPUTS` monkeypatched) — 200 per lang, invalid lang, 404, 500.
- **Prose quality gate:** the `humanizer` skill is run over the authored EN + zh-Hant + zh-Hans KB templates before finalizing (manual authoring step, noted in the plan).
- **Front-end:** manual + served-HTML assertion (pose-family dropdown, report panel, language switcher present).

## Non-Goals (first release)

- No in-app OAuth login flow (this app only consumes tokens obtained by sanctioned first-party tools; an in-app login flow is a separate future spec).
- No new provider beyond Anthropic / OpenAI / Google / local-OpenAI-compatible.
- PDF is best-effort, not guaranteed; Traditional-Chinese PDF glyph coverage is best-effort.
- No side-view-specific reference-range retuning (uses existing indicative ranges).
- No auto language detection; the three languages are fixed and all generated.
- The LLM never alters numbers, scores, structure, or drill selection — prose only.

## Future Extensions

- In-app OAuth login flow (separate feasibility spec per provider).
- Provider/model auto-discovery; per-language on-demand LLM polish to save cost.
- Bundled Traditional-Chinese font for reliable PDF glyphs.
- Rep-to-rep comparison (best vs worst) in the report.
