# Technique Analysis & Training Plan Feature — Design

**Date:** 2026-06-30
**Status:** Approved (pending written-spec review)
**Scope:** Add biomechanical stroke analysis and personalized training plan generation to Good-Badminton.

## Goal

Extend Good-Badminton from a tracking/stats tool into a **coaching aid for serious players**. For each detected stroke, measure joint angles, classify the stroke type, score the technique against sport-science reference ranges, identify strengths and weaknesses, and generate a personalized hybrid training plan (on-court + at-home).

## Non-Goals (first release)

- No ML-based stroke classification (rule-based scoring first; ML is a later refinement).
- No full phase-state machine (Approach A now; Approach C — Preparation → Backswing → Forward Swing → Contact → Follow-through → Recovery — is a documented later extension).
- No strokes beyond the four fundamentals (high clear, smash, drop shot, serve).
- No finger/grip-level analysis (COCO 17 has no finger keypoints; racket pose is inferred from the new racket detector).
- No PDF export in the first release (JSON report + Web UI rendering only).

## Scope: Four Fundamental Strokes

High clear, drop shot, smash, serve — chosen for the most common techniques with distinct biomechanical signatures.

## Chosen Approach

**Approach A: Shuttlecock-Contact Driven**, with **Approach C (phase-state machine)** documented as a later extension.

Contact events are detected by correlating racket-head and shuttlecock positions; a window around contact is extracted and classified; the biomechanical engine scores each stroke. This ties analysis to the objective signal (impact) and reuses the existing shuttlecock tracker.

## Architecture

```
                     Video Frame Stream
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   Pose Detector      Racket Detector    Shuttlecock Detector
   (COCO 17, exists)  (NEW YOLO)         (existing YOLO)
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                  StrokeEventDetector  (NEW)
        - racket↔shuttlecock proximity → contact frames
        - extract ±N frame windows around contact
        - classify stroke type (rule-based scoring)
                            │
                            ▼
                BiomechanicalAnalyzer  (NEW)
        - compute joint angles from keypoints + racket pose
        - score each stroke vs ideal ranges
        - identify strengths, weaknesses, flaws
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  Video Overlay       Web UI Viewer      Training Plan
  (extend)            (NEW pages)        Generator (NEW)
```

### New module layout (under `badminton_analysis/`)

- `stroke/` — `StrokeEventDetector` + stroke classifier
- `analysis/` — `BiomechanicalAnalyzer` + `reference_ranges.py`
- `training/` — `plan_generator.py` + `exercise_library.py`
- `visualization/technique_overlay.py` — in-video technique overlay

The existing `system.py` pipeline gains a single new step between shuttlecock detection and visualization drawing.

## Component 1: Stroke Detection & Classification

`stroke/` — `StrokeEventDetector`

1. **Contact detection** — For each frame, compute distance between racket head and shuttlecock. A contact event fires when distance drops below a tunable threshold (~80px) AND the shuttlecock trajectory shows an abrupt direction/velocity change within the next 2–3 frames (confirming impact, not mere proximity).
2. **Window extraction** — On confirmed contact at frame `T`, extract frames `[T-20, T+15]` (~1s at 30fps): preparation through follow-through.
3. **Stroke classification** — Rule-based scoring; each stroke type has conditions producing a 0–1 score, highest wins. Extensible to ML later without interface change.

| Stroke | Key discriminators |
|--------|---|
| High clear | Racket swings from below shoulder, high arc (>120° elbow), weight shifts forward, follow-through ends high and extended |
| Smash | Racket head above head at contact, downward racket velocity, trunk rotation >30°, explosive forward weight transfer |
| Drop shot | Clear-like preparation but shorter forward swing (<90° elbow), lighter racket velocity, racket face angled down at contact |
| Serve | Low-stance preparation, stationary opponent, distinct racket arc (short = low trajectory, long = high arc) |

**Output:** `StrokeEvent` — `{stroke_type, contact_frame, window_start, window_end, player_side, confidence}`

## Component 2: Biomechanical Analysis Engine

`analysis/` — `BiomechanicalAnalyzer` + `reference_ranges.py`

**Joint angles** computed from COCO 17 keypoints + racket head:

| Angle | Keypoints | Why it matters |
|-------|---|---|
| Elbow extension | Shoulder, elbow, wrist | Power generation |
| Shoulder abduction | Neck→shoulder, elbow→shoulder, wrist→shoulder | Swing plane (overhead vs flat) |
| Trunk rotation | L/R shoulder, L/R hip | Rotational power |
| Knee flexion | Hip, knee, ankle | Stance stability, power transfer |
| Hip-shoulder separation | Hip midpoint vs shoulder midpoint | Torque generation |
| Wrist flexion | Elbow, wrist, racket head | Racket face angle at contact |

**Ideal ranges per stroke** in `reference_ranges.py` (min/max/ideal per angle per stroke, from sports biomechanics literature). Indicative starting values:

```
High Clear:  elbow 140-160°, trunk 20-40°, knee 30-60°, wrist 80-100°
Smash:       elbow 150-170°, trunk 30-50°, knee 40-70°, wrist 100-120°
Drop Shot:   elbow 120-140°, trunk 15-30°, knee 25-50°, wrist 90-110°
Serve:       elbow 130-150°, trunk 10-25°, knee 50-80°, wrist 70-90°
```

**Scoring** — Each angle → 0–100 by deviation from ideal range. Weighted aggregate per stroke: elbow 25%, trunk 20%, wrist 20%, knee 15%, hip-shoulder separation 10%, forward weight transfer 10%.

**Weakness detection** — Out-of-range angle produces a finding:
```json
{
  "angle": "elbow_extension",
  "stroke_type": "smash",
  "measured": 135,
  "ideal_range": [150, 170],
  "deviation": "under_extended",
  "severity": "moderate",
  "description": "Elbow not fully extended during smash (135° vs ideal 150-170°). Reduces power transfer. Practice overhead extension drills."
}
```

**Strength identification** — Angles consistently within ±5° of ideal across multiple attempts flagged as strengths; per-player, per-stroke consistency tracked.

**Output:** `TechniqueReport` — overall score (0–100), per-angle breakdown, strengths, weaknesses with severity, natural-language suggestions.

## Component 3: Video Overlay & Web UI

`visualization/technique_overlay.py`

- **During stroke** — joint angles near relevant joints, racket angle indicator, stroke phase label.
- **After contact** — stroke type label + technique score badge (green ≥75, yellow 50–74, red <50).
- **Persistent** — weakness highlight icon next to out-of-range joints.

**Web UI viewer** (new pages/routes in `app.py`):

1. **Stroke timeline** — strokes per rally, color-coded by type, click to jump to video position.
2. **Stroke detail panel** — joint angle chart, score breakdown, comparison to ideal ranges, text feedback.
3. **Match summary** — strokes by type, avg score per type, recurring weaknesses.
4. **Training plan tab** — generated plan, on-court / at-home toggle.

## Component 4: Training Plan Generator

`training/plan_generator.py` + `training/exercise_library.py`

**Exercise library** — JSON, ~30–40 exercises categorized:

| Category | Examples |
|----------|----------|
| On-court drills | Shadow clear, multi-shuttle smash, drop shot placement, serve target practice |
| Mobility | Shoulder dislocates, thoracic rotation, wrist flexor stretch, ankle mobility |
| Strength | Plank variations, band rotator cuff, medicine ball throws, single-leg squats |
| Flexibility | Pectoral, hamstring, hip flexor stretches |

Each exercise: `{id, name, category, target_weaknesses[], description, equipment, difficulty, duration_min, reps, sets, image_url}`

**Mapping** — Each finding maps to 2–3 exercises. Generator collects findings, deduplicates, builds a progressive weekly plan:

```
Week 1-2 (Foundation):
  On-court: Shadow clear drill (20 min) — 3x/week
  Strength: Band overhead press (3x12) — 3x/week
  Mobility: Shoulder dislocates (2x15) — daily
  Flexibility: Thoracic rotation stretch (2x30s) — daily

Week 3-4 (Progression):
  On-court: Multi-shuttle smash drill (15 min) — 3x/week
  Strength: Medicine ball overhead throws (3x10) — 3x/week
  ...
```

**Hybrid behavior** — auto-generated plan; user can swap exercises from the library, adjust frequency, export printable schedule.

**Output:** `training_plan.json` + Web UI rendering.

## Data Flow & Output Files

New output files under `outputs/<video>/`:

```
strokes.jsonl          # one StrokeEvent + TechniqueReport per detected stroke
technique_summary.json # match-level aggregate, strengths, weaknesses
training_plan.json     # generated plan
```

These sit alongside the existing `detections.jsonl`, `rally_segments.json`, `metadata.json`.

## Error Handling

- **Racket not detected at contact** — fall back to kinematic inference (racket along forearm axis from wrist+elbow); flag report entry `racket_inferred: true`.
- **Shuttlecock occluded** — no contact event for that frame; missed strokes logged, not fatal.
- **Low pose confidence** — angles with contributing keypoints below confidence threshold are marked `null`/`low_confidence`, excluded from scoring rather than scored wrongly.
- **Too few attempts for a stroke type** — strength/weakness flagged as `insufficient_data` rather than asserting a conclusion.
- **No strokes detected** — technique pages show an empty-state message; training plan falls back to a general-fitness baseline.

## Testing Strategy

- **Unit** — joint-angle math against hand-computed fixtures; scoring against synthetic angle inputs at/around range boundaries; classifier against synthetic stroke windows; plan generator against fixed finding sets.
- **Integration** — run pipeline on a short clip with known strokes; assert stroke count, types, and that reports/plan files are produced and schema-valid.
- **Regression** — existing tracking/stats outputs unchanged when technique analysis disabled (feature behind a flag, e.g. `--analyze-technique`).

## New Dependencies

- A YOLO racket-detection model weight (trained or sourced), placed in `weights/`, configurable via a CLI/UI argument like the ball model.
- No new Python packages anticipated beyond the existing OpenCV/NumPy/Ultralytics/Flask stack.

## Later Extensions (out of scope now)

- Approach C phase-state machine for mid-swing breakdown.
- ML-based stroke classifier swapped behind the existing classifier interface.
- Additional strokes (defensive lift, drive, block, net shot).
- PDF / printable report export.
