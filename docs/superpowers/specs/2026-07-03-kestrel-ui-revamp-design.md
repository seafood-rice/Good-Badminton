# Kestrel — Web UI Revamp Design

_Date: 2026-07-03_

## Context

The current web UI (`web_ui.html`, ~910 lines) is a functional but plain dark-theme
single-page app: a linear stack of panels for video selection, court detection,
match analysis, and posture drills. All the analysis capability is there (match
tracking, technique biomechanics, posture drills, tri-lingual coach reports,
training plans), but the presentation is utilitarian and the flow is not guided.

A design prototype (`BirdEye Prototype.html`) reimagines the same feature set as a
premium **coach portal**: a light "paper" theme, a persistent sidebar, a guided
"New Analysis" wizard, and a card-based video library. This spec adapts that
prototype into a shippable revamp, rebranded **Kestrel**, and layers on several
end-user enhancements requested during brainstorming (first-frame thumbnails,
library filters, interactive staged progress, per-rep video analysis, and richly
documented training exercises with video guides).

The goal: a modern, guided, coach-facing UI that makes the existing analysis feel
professional and easy to act on — with no regression to the working backend.

## Locked decisions

- **Scope:** front-end reskin + information-architecture reorg, wired to the
  existing `/api/*` endpoints. Single-user. The coach/team persona is cosmetic
  branding, not a multi-athlete backend. Backend changes are additive and small.
- **Product name:** **Kestrel** (a raptor with acute eyesight that hovers and
  watches from above — a literal bird's-eye analysis metaphor; pairs with the
  prototype's orange accent and "Falcons" motif). Replaces "BirdEye" in the UI.
- **Language:** keep bilingual **zh/en** with a sidebar toggle; default `zh`
  (current behaviour). Coach report keeps its own EN/繁體/簡體 switcher.
- **Build approach:** split assets served by Flask's built-in `/static/` handler —
  `web_ui.html` shell + `static/kestrel.css` + `static/kestrel.js` +
  `static/fonts/` (local IBM Plex Mono woff2) + `static/img/`. No CDN, offline-safe.
- **Theme:** light "paper" (prototype default) **plus a dark theme toggle** in the
  sidebar. Choice persisted to `localStorage`; first load honours the OS
  `prefers-color-scheme`, falling back to light.
- **Exercise video guides:** curated **external links** per exercise (embedded when
  online; graceful "unavailable offline" fallback). We do not produce videos.
- **Per-rep clip:** in-browser **bounded scrubber** for analysis, **plus** an
  on-demand server-side crop for "Download this rep".

## File layout

```
web_ui.html                 thin shell: <head> links, sidebar markup, screen
                            containers, mounts kestrel.js
static/
  kestrel.css               theme tokens, components, screen layouts
  kestrel.js                router, i18n dictionary, API calls, wizard state
  fonts/*.woff2             IBM Plex Mono (latin subsets, ~3 weights)
  img/kestrel-mark.svg      orange rounded-square logo mark
```

`index()` in `app.py` keeps returning `web_ui.html`. Flask already serves
`static/` at `/static/` (default `Flask(__name__)` behaviour) — no custom route
needed.

## Design system

CSS custom properties on `:root` (ported from the prototype, kept themeable):

- Accent: `--accent:#FF5A36`, `--accent-dark` (hover), `--accent-soft` (nav highlight)
- Sidebar: `--sidebar-bg:#14161A`
- Surfaces: `--paper:#FAFAF8`, `--card:#fff`, `--border:#EBE8E2`, `--fill:#F3F1EC`
- Ink: `--ink:#1A1D1F`, `--muted:#6B7280`, `--faint:#8A8F98`
- Scores: `--good:#3FAE6A`, `--mid:#E8A93B`, `--bad:#E5484D` (+ pastel chip backgrounds)
- Scale: `--radius-sm/md/lg/pill`, `--space` (spacing multiplier)

**Theming (light + dark).** The values above are the light theme (`:root`). A dark
theme is a second token set applied via `[data-theme="dark"]` on the root container
— only the surface/ink/border/chip tokens change; **accent and score hues stay
constant** (they read well on both). Indicative dark values: `--paper:#0F1114`,
`--card:#1A1D1F`, `--border:#2A2D33`, `--fill:#22262B`, `--ink:#F3F1EC`,
`--muted:#9A9EA6`, `--faint:#787D85`; pastel score chips become translucent tints.
The dark sidebar (`--sidebar-bg`) is unchanged across themes. A **theme toggle** in
the sidebar footer (beside the language toggle) flips `data-theme`; the choice is
persisted to `localStorage`, and first load honours `prefers-color-scheme`
(defaulting to light). All components read tokens only, so no per-component dark CSS
is required.

Typography:

- UI text: system sans stack (`-apple-system, "Segoe UI", …`) — covers CJK for the
  bilingual UI, zero bundling.
- Data/numbers/labels: **IBM Plex Mono** (bundled woff2, latin), `ui-monospace`
  fallback. Used for stats, durations, scores, codes — the prototype's signature.

Reusable components (CSS classes): card (white, bordered, hover-lift), stat tile,
video card (thumb + badge + action), primary/secondary/ghost buttons, pill badge,
score chip, wizard stepper, dashed dropzone, section header, toast/inline-error,
bounded video scrubber.

i18n: `kestrel.js` holds `T = { zh:{…}, en:{…} }`; every label rendered via
`t(key)`. Toggle in the sidebar footer; choice persisted to `localStorage`.

## Information architecture

### Persistent sidebar (232px, dark, sticky)
Kestrel mark + wordmark, "Coach Portal" label, animated nav highlight, footer with
coach-persona avatar + **language toggle (中/EN)** + **theme toggle (light/dark)**.
Nav: **Dashboard** · **New Analysis** · (Results opens contextually when a video is
entered).

### Screen 1 — Dashboard / Video Library
- **4 stat tiles** (mono): Total Videos · Analyzed · Rallies Detected · Avg
  Technique Score — from `/api/stats`.
- **Filters bar** (client-side over `/api/videos`): search by name, mode filter
  (All / Match / Drill), status filter (All / Analyzed / In-setup), sort (date / name).
- **Card grid** (`auto-fill minmax(260px,1fr)`): each card = **first-frame
  thumbnail**, mode badge, duration, name, date, status badge
  (New → Court Set → Analyzed), action button (**View Results** if analyzed, else
  **Continue Setup**). Card click routes into the video (results or wizard resume).
- `+ New Analysis` button → wizard.

### Screen 2 — New Analysis wizard (stepper)
Adapts by mode:
1. **Mode** — choice cards: *Match Analysis* vs *Posture Drill*.
2. **Upload** — dashed dropzone → `POST /api/upload`; or select an existing library
   video to skip.
3. **Court setup** *(match only)* — auto-detect (`POST /api/detect`) with confidence
   readout + corner dots; **Adjust Manually** reveals the existing 4-point canvas
   annotator (`POST /api/manual-annotate`), restyled.
4. **Config** —
   - Match: language, pose model, **Technique-analysis toggle** → `POST /api/analyze`
   - Posture: stroke type, dominant hand, pose model, LLM-polish on/off →
     `POST /api/posture/analyze`
5. **Progress** — interactive **0–100% bar with named stages** shown as a checklist
   so progress is always visible; polls `/api/status/<id>` or
   `/api/posture-analyze-status/<id>`; auto-advances to Results on completion.

### Screen 3 — Results (per video; layout by mode)
- **Match:** annotated video player · rally summary + **clip generation**
  (highlights/clips → `/api/clip`) · position **heatmap** + **scatter** ·
  **Technique Analysis** (summary, stroke list, per-stroke metric bars +
  suggestions) · **Training Plan** (On-Court/At-Home tabs, regenerate) · Notes ·
  Export.
- **Posture:** annotated video · drill summary · **rep list** + per-rep detail with
  a **bounded scrubber** (play just the rep window; rewind/forward + frame-step;
  "Download this rep") · **Training Plan** · **Coach Report** (EN/繁體/簡體, download
  HTML/PDF) · Notes · Export.

Opening any analyzed video from the library loads its existing results directly
(the restore-on-select behaviour already implemented in the current UI, now
first-class in the IA).

**Notes:** localStorage-only for v1 (no backend state). **Export:** bundles the
existing download links (report HTML/PDF, clips, visualizations).

## Backend additions and endpoint mapping

All additive; the match pipeline and existing routes are untouched except for the
noted enrichments.

| Capability | Existing route reused | New / changed backend |
|---|---|---|
| Library cards + filters | `/api/videos` | Add per-item `date` (mtime), `duration` (cv2), `thumb` URL, `mode`, `status`. **First-frame thumbnail** generated on `/api/upload` (cv2 first frame → `outputs/<stem>/thumb.jpg`), lazily backfilled when listing. |
| 4 stat tiles | — | New `GET /api/stats` aggregating existing output files (video count, analyzed count, total rallies from `rally_segments.json`, avg technique score from `technique_summary.json`). |
| Match progress stages | `/api/analyze`, `/api/status` | Add named stage labels to job `message` (Detecting → Analyzing frames X/Y → Visualizing → Transcoding → Done). % already frame-based. |
| Posture progress | `/api/posture/analyze`, `/api/posture-analyze-status` | `PostureAnalysisSystem.process_video` emits periodic `PROGRESS <pct> <stage>` lines; `app.py` `track()` parses them → real 0–100% + stages (Loading → Analyzing frames → Scoring reps → Building report → Transcoding → Done). |
| Per-rep scrubber | `/api/posture` (reps) | Persist `start_frame`/`end_frame` per rep (from the rep segmenter, which already computes windows) into `drill_reps.jsonl`. Client converts to seconds via `fps` in `metadata.json`. |
| Download this rep | — | New `POST /api/posture-rep-clip/<video>` `{rep_id}` → ffmpeg-crop the rep window from the annotated posture video (reuses the `api_clip` ffmpeg pattern) → returns URL. On-demand only. |
| Exercise detail + video guide | `/api/training-plan`, `/api/posture-plan` | Expand `exercise_library.json`: per exercise add `instructions[]`, `coaching_cues[]`, `common_mistakes[]`, `video_url` — bilingual (zh/en), plain-language coaching voice. `plan_generator.generate_plan` carries the enriched detail through into plan session items so the UI can render it. |

## States and resilience

- Empty library → upload prompt.
- Analysis error → clear message + retry (existing job `error` status).
- Not-yet-analyzed video → graceful "no results yet" (matches existing 404 handling
  in `/api/technique`, `/api/posture`, `/api/posture-report`).
- Video guide offline / missing → "video unavailable offline" card; links open in a
  new tab and embed when online.
- Web font fails to load → `ui-monospace` fallback.

## Testing

- **Backend (pytest, following `tests/test_app_*` patterns):** enriched
  `/api/videos` fields; `/api/stats` aggregation; `/api/posture-rep-clip` validation
  + 404s; plan payload includes exercise detail; posture `PROGRESS` line parsing.
  **All 141 existing tests must stay green** (match pipeline untouched).
- **Frontend:** browser dogfood against the running Flask app using the existing
  `IMG_1270` (posture + reports) and `IMG_1537` (technique) outputs — verify library,
  wizard, staged progress, results, rep scrubber, and exercise cards.
- **Content:** run exercise instructions/cues through the humanizer skill for plain,
  natural, non-jargon wording.

## Phasing (for the implementation plan)

1. Design system (light + dark tokens, theme toggle) + shell + sidebar +
   **Library** (filters, thumbnails, stats).
2. **New Analysis wizard** + staged progress (incl. posture progress backend).
3. **Results** — match + posture, rep bounded scrubber + on-demand crop.
4. **Training plan + coach report** + enriched bilingual exercise content + video guides.
5. i18n polish, error states, docs (`README`, `WINDOWS_SETUP`).

## Out of scope (deferred)

- Multi-athlete / coach-roster backend (persona stays cosmetic).
- Server-persisted notes (localStorage only for v1).
- Self-produced exercise demo videos (curated external links instead).
