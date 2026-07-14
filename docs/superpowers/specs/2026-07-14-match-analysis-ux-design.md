# Match Analysis UX — Design Spec (Sub-project A)

**Date:** 2026-07-14
**Status:** Design approved; awaiting spec review before writing the implementation plan.

## Goal

Make the match-analysis experience whole and responsive: the output video keeps the
**full original duration** with overlays, the analysis shows **real, always-advancing
progress** (never looks hung), it runs **faster** (lossless wins + an optional Fast mode),
and its **visualizations are localized** to the selected language.

## Where this fits (decomposition)

The user's match-analysis overhaul is three sub-projects with a hard dependency:

- **A — Analysis UX (this spec):** full-duration video, real progress, speedup, localized
  visualizations. Independent; fixes the currently-broken experience.
- **B — Full-match stroke recognition (later):** a per-rally segment dense-tracking pre-pass
  so BST fires on full matches (yesterday's fix limited dense tracking to short clips).
- **C — Match analytics + tactics (later):** match-wide stroke-technique distribution with
  percentages, and per-event tactical/strategy analysis (Men's Singles first). Depends on B
  for full-match per-stroke data.

A is built first and stands alone. B and C get their own spec → plan → cycle.

## Components

### A1 — Full-duration output video

**Problem:** `badminton_analysis/system.py::_process_frame` returns early on non-court frames
(`if not is_court: return frame, detect_frame_count`) *before* `out.write(frame)`, so the
output video contains only the court-view frames stitched together — shorter than the input
and perceived as "chopped." It also desyncs the merged audio (audio is full-length, video is
not).

**Design:** On a non-court frame, write the **raw frame** (passthrough — no overlays, no
marker) to the output writer, then return. Court frames are unchanged (overlays drawn, then
written). Every frame read from the input is written exactly once, so the output duration
equals the input duration and one continuous video is produced. The existing audio-merge path
(`media/video_audio.process_video_with_audio`) then aligns correctly with no further change.
`rally_segments.json` remains as-is (rally boundaries are still useful metadata); the video is
no longer chopped to those boundaries.

**Done:** output video frame count == frames decoded from the input; a clip containing both
non-court and court stretches produces a full-length video with overlays only on court frames.

### A2 — Real progress, never looks hung

**Problem:** the web app (`app.py`) infers progress from `detections.jsonl` line count, which
only grows on court frames — so during a long non-court intro (the sample match had ~9,500
non-court frames first) the bar sits at 0% and looks hung. Separately, the match subprocess is
spawned with `stdout=PIPE, stderr=STDOUT` and its pipe is **never drained**, risking a
buffer-fill deadlock, and the output (including tracebacks) is discarded.

**Design:**
- **`system.py` writes a `progress.json` heartbeat** into the output dir. Fields:
  `{"stage", "current_frame", "total_frames", "pct", "updated"}` (`updated` = epoch seconds).
  Written at stage transitions and every ~1 second of frames (`max(1, int(fps))` frame
  interval) during the loop. Stages: `initializing` → `court_setup` → `analyzing` →
  `visualizing` → `encoding` → `done`. `pct` is derived from `current_frame/total_frames`
  during `analyzing`, and pinned per-stage otherwise. All progress writes are wrapped
  never-fatal (a failed write must never abort analysis).
- **`app.py` redirects the subprocess output to `<save_dir>/analyze.log`** (open a file handle
  as `stdout`, `stderr=STDOUT`) instead of an undrained PIPE — this removes the deadlock risk
  and persists logs. `track_progress` polls `progress.json` for stage + pct (falling back to
  the old detections-based estimate only if `progress.json` is absent). On non-zero exit, the
  job surfaces the **tail of `analyze.log`** as the error message (so failures like yesterday's
  are visible, not silent).
- **Frontend (`static/kestrel.js`)** progress screen shows the localized **stage label + pct +
  an elapsed/"still working" indicator** driven by `progress.json.updated`, so the user always
  sees it is alive. On error, it shows the surfaced log tail.

**Done:** progress advances during non-court stretches (frame-based, not detection-based); the
stage label and elapsed indicator render; the subprocess cannot deadlock on a full pipe; a
failed run shows a real error message.

### A3 — Speedup: measure → lossless + Fast/Accurate toggle

**Approach (hybrid, user-selected):** measure first, apply lossless wins, and expose a
Fast/Accurate quality mode.

- **Measure (implementation step 1):** profile per-stage timings using the existing
  `--performance-stats` output (pose, shuttle, court-view check, drawing) on a representative
  court-heavy segment, and record the dominant cost. No optimization is chosen before this
  measurement identifies the bottleneck.
- **Lossless wins (applied to the measured bottleneck, low-risk first):**
  - **Court-view check cadence:** the per-frame template match (`is_court_view`) drives rally
    segmentation, whose thresholds already span several frames; running it every N frames
    (holding state between) is effectively lossless for rally boundaries and removes per-frame
    template-match cost. Prime candidate if the check dominates.
  - **GPU batch inference** (pose/ball) is a *higher-risk* lossless option (it restructures the
    streaming loop). It is **gated on measurement** — implement only if detection dominates and
    the win justifies the restructure; otherwise defer.
- **Fast/Accurate toggle** (`main.py --analysis-quality {accurate,fast}`, default **accurate**):
  - **Accurate (default):** every frame analyzed, full pipeline — behavior-identical to today
    plus the lossless wins. Keeps B/C analytics viable.
  - **Fast:** apply a frame stride to the heavy per-frame analysis (process every Kth frame for
    pose/ball, hold the last result for skipped frames) for a quick overview. The output video
    still renders every frame (A1). Fast **degrades dense analytics** (contact detection, and
    later stroke recognition), so in Fast mode `analyze_technique`/BST are skipped and the UI
    labels the run as a quick look. The stride K is a named constant chosen from profiling.
  - **Wiring:** `app.py` passes `--analysis-quality` from a new Fast/Accurate toggle in the
    match config step; `static/kestrel.js` adds the toggle (default Accurate) with help text on
    the tradeoff.

**Done:** profiling recorded; Accurate mode is behavior-identical to current analysis (analytics
unchanged); Fast mode measurably reduces wall-clock with documented analytics degradation; the
toggle is wired end-to-end and defaults to Accurate.

### A4 — Localized visualizations

**Problem:** `badminton_analysis/visualization/player_positions_zh.py` and `_en.py` are two
~600-line near-duplicate modules differing mainly in hardcoded label strings; the divergence is
why localization drifts (the heatmap can render in the wrong language for the selected UI
language).

**Design:** unify the two into **one language-parameterized module** (`player_positions.py`)
with a strings table (i18n dict for chart titles and axis labels) and a `language` parameter;
the Chinese-font loader is used for `zh`, the default font for `en`. `main.py` imports the
single module and passes the selected language. **Both the heatmap and the scatter plot** render
their text in the selected language. Output **filenames stay stable** (`match_heatmap.png`,
`match_scatter.png`) so the frontend result URLs work unchanged — the language affects the
rendered text inside the image, not the filename. The unify is a mechanical, string-only
parameterization of two near-identical files and must preserve the current chart output
(same plots, localized text).

**Done:** an `en` run produces English chart labels, a `zh` run produces Chinese labels; the
frontend displays the correct-language images; only one visualization module remains.

## Global constraints

- **Windows env:** run Python as `.venv/Scripts/python.exe` with `PYTHONUTF8=1`; kill background
  processes by PID.
- **Never commit** model weights or datasets.
- **Never-fatal instrumentation:** progress-heartbeat writes must not be able to abort analysis
  (wrap in try/except).
- **Accurate is the default and is behavior-preserving** for the analysis outputs that B/C
  depend on (every frame analyzed); the full-duration video (A1) is the one intended change to
  the output video.
- **Stable output filenames** for visualizations (`match_heatmap.png`, `match_scatter.png`) and
  the output video path, so existing frontend URLs keep working.
- **No deadlock:** the web subprocess output must be drained/redirected, never left on an
  undrained pipe.

## Success criteria (done-means)

1. Output video duration equals input duration; one continuous video with overlays on court
   frames and raw passthrough elsewhere; merged audio stays in sync.
2. The web UI shows an always-advancing, frame-based progress with a stage label and a
   clearly-alive heartbeat; the subprocess cannot deadlock; failures surface a real error.
3. Profiling is recorded; Accurate mode matches current analytics behavior; Fast mode reduces
   wall-clock time with documented analytics degradation; the toggle defaults to Accurate.
4. Heatmap and scatter render in the selected language; the frontend shows the correct images;
   one unified visualization module remains.
5. The full test suite stays green; new behavior is covered by tests.

## Out of scope (deferred)

- **B — full-match stroke recognition** (per-rally segment dense-tracking pre-pass).
- **C — match-wide stroke-technique distribution + percentages, and tactical/strategy analysis**
  (Men's Singles). These depend on B.
- A "chopped rally clips" export mode (the video is now always full-duration).
- Any accuracy/perf work beyond the measured lossless wins + the Fast stride (e.g., model
  retraining, TensorRT) is a separate performance project.

## Profiling results (A3a)

**Method:** `scripts/profile_pipeline.py` times the three per-frame calls `_process_frame`
makes (`badminton_analysis/system.py`) directly against frames of a representative
court-view clip: `is_court_view`'s `cv2.matchTemplate` (grayscale, full-frame template),
`player_pose_visualizer.detect_players` (YOLO pose), and `shuttlecock_tracker.detect_ball`
(YOLO ball). No court annotation is available headlessly, so the pose/ball ROI is
approximated as the full frame rather than the true (smaller) annotated crop — a
conservative approximation that, if anything, makes production pose/ball costs *lower*
than measured here, not higher. 250 frames measured after a 10-frame CUDA warmup, on
`rally_seg.mp4` (750 court-view frames, 1080p).

**Machine:** RTX 4090 (`torch.cuda.get_device_name(0)` confirmed), models run on
`device=0`.

**Measured (mean ms/frame, two runs for stability):**

| stage              | run 1 (ms) | run 2 (ms) |
|--------------------|-----------:|-----------:|
| court_view_check   |      28.82 |      27.17 |
| pose               |      16.88 |      16.02 |
| ball               |      13.74 |      13.07 |
| total              |      59.44 |      56.25 |

**Bottleneck:** `is_court_view` (CPU-bound `cv2.matchTemplate` against a full 1080p
grayscale template) is the single largest per-frame cost — larger than the GPU-accelerated
pose and ball inference *combined*. Pose and ball, running on the RTX 4090, are already
inexpensive relative to this CPU-bound check.

**Recommendation:** the court-view-check cadence optimization (A3, "lossless wins") is
**strongly worthwhile** — it targets the actual bottleneck (~48% of per-frame time) with a
low-risk change (holding state between checks, since rally-boundary thresholds already
span several frames). GPU batching of pose/ball is **not warranted as a near-term
follow-up**: those stages are already fast on this hardware and are not the dominant
cost; revisit only if a future profile on different hardware (e.g., no/weaker GPU) shows
otherwise.
