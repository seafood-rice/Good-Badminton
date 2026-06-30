# Posture Drill Mode — Design

**Date:** 2026-07-01
**Status:** Approved (pending written-spec review)
**Scope:** Add a standalone, court-free "Posture Drill" analysis mode for single-player side-view technique clips, with its own UI section, separate from the existing court-match rally analysis.

## Problem

The existing pipeline is built entirely around a **court**: `BadmintonAnalysisSystem._process_frame` gates every frame on `is_court_view` (template match), crops to a court ROI, splits players into upper/lower halves, maps to court coordinates, and detects rallies. A side-view single-player drill clip (e.g. `IMG_1270.mov`, one player performing repeated clears) has none of these, so the Web UI nonsensically demands court annotation and the analysis produces nothing. Posture/technique practice is a genuinely different use case and deserves its own mode.

## Goal

A separate **Posture Drill** mode: the user uploads a side-view clip of one player doing repeated reps of a chosen stroke; the system segments each rep from swing motion, scores each rep's technique with the already-built biomechanical analyzer, and produces per-rep reports, a drill aggregate (incl. consistency), an annotated video, and a training plan — with no court anywhere in the flow.

## Decisions (from brainstorming)

- **Clip content:** one player, drill with repeated reps; **user picks the stroke type** up front (no stroke classification).
- **Shuttle:** mixed — segment primarily by swing motion (always available); refine with shuttle proximity when a shuttle is detected. Works for shadow and fed drills.
- **Camera view:** **side/profile view** tuned and accurate now; other views documented as future work.
- **UI:** a **top-level mode switch** (🏟 Match Analysis | 🧍 Posture Drill); selecting Posture Drill hides the court workflow entirely. Match flow unchanged.
- **Output:** per-rep reports + aggregate + annotated video + training plan (reusing Plan 2). No heatmaps/court maps.

## Chosen Approach

**Approach A — separate `PostureAnalysisSystem` that reuses the analysis library.** A new court-free pipeline that runs the existing pose detector full-frame and feeds rep windows into the already-tested `BiomechanicalAnalyzer` / `scoring` / `reference_ranges` (Plan 1) and `plan_generator` (Plan 2). The working court-match pipeline is left untouched. The only shared extraction is a small video read/write helper. (Approach B — a `court_mode` flag threaded through the 600-line existing class — was rejected as too risky to the working flow; Approach C — base-class refactor — as premature for two modes.)

## Architecture

```
       Posture Drill clip (side view, 1 player, N reps)
                          │
                          ▼
        PostureAnalysisSystem  (NEW posture/system.py)
        - read video (shared media/video_io helper)
        - per frame: EXISTING pose detector FULL-FRAME
          (no court gate, no ROI crop, no is_court, no rally)
        - buffer: keypoints, conf, wrist, shuttle(optional), centroid
        - draw live technique overlay; write annotated frame
                          │
                          ▼
        RepSegmenter  (NEW posture/rep_segmenter.py)
        - rep windows from dominant-wrist swing-velocity peaks
        - refine peak via shuttle proximity when shuttle present
                          │
                          ▼
        per rep → StrokeEvent(stroke_type=chosen, player_side="single")
                → REUSE BiomechanicalAnalyzer (Plan 1)
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                 ▼
   drill_reps.jsonl  annotated video   drill_summary.json
   (per-rep report)  (angle overlay)   + training_plan.json
                                       (REUSE plan_generator, Plan 2)
```

### New module layout
- `badminton_analysis/posture/__init__.py`
- `badminton_analysis/posture/system.py` — `PostureAnalysisSystem`
- `badminton_analysis/posture/rep_segmenter.py` — `segment_reps`, `RepWindow`
- `badminton_analysis/posture/writer.py` — `write_rep_reports`, `build_drill_summary`
- `badminton_analysis/media/video_io.py` — small shared helper: video reader + H.264 writer. The match system already has `media/video_audio.py` with `setup_video_writer`; the posture system reuses that existing helper directly. A new `video_io.py` is added ONLY if posture needs read/write scaffolding not already covered — to avoid touching the working match code, prefer reusing `video_audio.py` over refactoring it.
- `main_posture.py` — CLI entry
- new Flask routes in `app.py`; mode switch + posture panel in `web_ui.html`
- tests under `tests/`

### Reused unchanged
`BiomechanicalAnalyzer`, `scoring`, `reference_ranges`, `joint_angles` (incl. `infer_racket_head`), the YOLO pose detector / `PlayerPoseVisualizer`, `ShuttlecockTracker` (full-frame instead of court ROI), `technique_overlay.draw_technique_overlay`, `plan_generator.generate_plan`, `exercise_library`.

## Component: RepSegmenter

`segment_reps(track, fps, min_gap_sec=0.8, pre=20, post=15, k=1.0, max_reps=50) -> list[RepWindow]`

- `track`: ordered per-frame dicts `{frame, wrist:(x,y)|None, shuttle:(x,y)|None}`.
- **Swing signal:** dominant-hand wrist speed = smoothed per-frame pixel displacement.
- **Peak detection:** local maxima above adaptive threshold `mean + k·std` of wrist speed, enforcing a minimum gap (`min_gap_sec * fps` frames) between accepted peaks (keep the higher peak when too close).
- **Window:** each peak → `[max(0, peak-pre), peak+post]` (frame counts scale with fps relative to a 30fps baseline).
- **Shuttle refinement:** when shuttle present in the window, snap `peak_frame` to the frame where shuttle is closest to the wrist within the window.
- **Output:** `RepWindow = {rep_id, peak_frame, window_start, window_end, prominence}` (a dataclass with `to_dict()`).
- **Edge cases:** too short / no supra-threshold peaks / player static → empty list (valid, not an error). `max_reps` safety cap, log if exceeded.

Each `RepWindow` is wrapped into the existing `StrokeEvent` shape (`stroke_type` = user choice, `player_side="single"`, `confidence`=normalized prominence) so `BiomechanicalAnalyzer.analyze(stroke_event, window_frames)` is reused unchanged.

## Component: PostureAnalysisSystem

`PostureAnalysisSystem(video_path, stroke_type, dominant_hand="right", output_dir=None, ball_model_path=None, show_display=False, show_overlay=True, save_dir_name="posture")` — **no template/court argument exists.**

Per-frame loop (no `is_court`, no ROI crop, no rally/court mapping):
1. Full-frame pose detection (existing detector, offset 0,0); pick the single highest-confidence/largest person.
2. Dominant-hand wrist position; optional shuttle detection (full-frame) if `ball_model_path` given.
3. Buffer per-frame record (keypoints, conf, wrist, shuttle, centroid). Racket head inferred later via `infer_racket_head` (drills have no racket model).
4. Draw live `draw_technique_overlay` with instantaneous angles; write annotated frame via the shared writer.

Post-loop: `segment_reps` → per-rep `StrokeEvent` → `BiomechanicalAnalyzer.analyze` → `write_rep_reports` + `build_drill_summary`. Annotated video re-encoded to H.264 (shared helper / route does this, matching the match flow).

## Component: drill writer

`write_rep_reports(path, reports)` — one technique report per line (JSONL), via existing `clean_value`.
`build_drill_summary(reports, stroke_type) -> dict`:
```
{
  "stroke_type": <chosen>,
  "rep_count": int,
  "mean_score": float|None,
  "best_rep": {rep_id, score}|None,
  "worst_rep": {rep_id, score}|None,
  "consistency": float|None,        # stddev of overall scores across reps (lower = more consistent)
  "per_metric_avg": {metric: avg_score|None},
  "recurring_weaknesses": [{metric, count}],   # same shape generate_plan consumes
  "strengths": [{metric, count}]
}
```
`recurring_weaknesses` reuses the exact shape Plan 2's `generate_plan` already consumes, so the training plan slots in unchanged.

## CLI

```
python main_posture.py --video-path videos/IMG_1270.mov --stroke-type high_clear --dominant-hand right
```
Args: `--video-path` (req), `--stroke-type` (req; choices high_clear/smash/drop_shot/serve), `--dominant-hand` (right/left, default right), `--output-dir`, `--ball-model` (optional), `--display` (default false). No court/template args.

## Flask routes (added to app.py)

- `POST /api/posture/analyze` — `{video, stroke_type, dominant_hand}` → launch `main_posture.py` subprocess with a tracked job (mirrors existing `/api/analyze`, incl. H.264 re-encode). No court step.
- `GET /api/posture/status/<job_id>` — progress (reuses existing job shape).
- `GET /api/posture/<video>` — `{summary: drill_summary.json, reps: [...]}`; 404 if no summary.
- `GET|POST /api/posture-plan/<video>` — training plan from `drill_summary.json` via `generate_plan`; same error-handling/tuple pattern as the court training-plan route.

All new routes use the `try/except → jsonify({'error':...}), 500` pattern standardized in Plan 2; missing files → structured 404. Output files served by the existing `/api/output/<video>/<path:subpath>` route (handles the `posture/` subpath already).

## Output files

```
outputs/<video>/posture/
├── detect_<video>.mp4      # annotated video (H.264, angle overlay)
├── drill_reps.jsonl         # one technique report per rep
├── drill_summary.json       # rep count, mean/best/worst, consistency, recurring weaknesses
├── training_plan.json        # generated on request (reuses Plan 2 generate_plan)
└── metadata.json            # video info, stroke_type, dominant_hand
```
The `posture/` subdir prevents collision with match-mode outputs for the same clip.

## Web UI

Top-level mode switch in `web_ui.html`: **🏟 Match Analysis | 🧍 Posture Drill** (default Match, existing flow untouched). A JS `setMode()` toggles panel visibility; existing match functions are unchanged, new `posture*` siblings added.

Posture panel: video picker → stroke-type dropdown + dominant-hand toggle → ▶ Analyze (no court step) → progress bar → results: annotated video, rep list (rep # + score badge, click → joint-angle breakdown + suggestions, reusing Plan 2 render helpers), drill summary (reps, mean/best/worst, consistency), training-plan section (On-Court/At-Home toggle + Regenerate). Self-contained, no new dependencies.

## Error handling

- 0 reps / no pose detected → empty `drill_reps.jsonl`, valid `drill_summary.json` with `rep_count: 0`; UI shows a guidance empty-state, not an error.
- New routes: JSON 500 on exceptions, structured 404 on missing files.
- Subprocess failure → job status `error` with message, surfaced in UI.

## Testing

- **Unit (pytest, TDD):** `segment_reps` (clean peaks, sub-threshold noise, min-gap, shuttle refinement, empty); `build_drill_summary` (consistency stddev, best/worst, recurring-weakness aggregation, empty); `PostureAnalysisSystem` post-loop orchestration via a fake frame buffer (no real video), mirroring the court `TechniqueAnalysisRunner` test style.
- **Integration:** new Flask routes via test client (`OUTPUTS` monkeypatched) — posture GET, posture-plan GET/POST, 404s, 500 on malformed.
- **Front-end:** manual + a served-HTML assertion that the mode switch and posture functions are present.

## Non-Goals (first release)

- Stroke classification (user picks the stroke).
- Non-side camera views (documented future work).
- Heatmaps / court-coordinate maps (no court).
- Multi-player drills.
- Reference-range re-tuning specifically for side-view geometry — uses the existing indicative ranges; side-view-specific tuning is future work.
- Refactoring the match pipeline into a shared base class (Approach C) — deferred.

## Future Extensions

- Side-view-specific reference ranges; front/other-view support (detect view first).
- Auto-detect court-vs-drill on upload.
- Rep-to-rep comparison view (overlay best vs worst rep).
- Promote shared video I/O into a base class if a third mode appears.
