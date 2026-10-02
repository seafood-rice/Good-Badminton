# Workstream: match-stroke-recognition-b

- **workstream_id:** `match-stroke-recognition-b`
- **Objective:** Sub-project B - full-match stroke recognition, extending the completed
  Sub-project A per-rally analysis, per the approved completion bar.
- **Scope paths (B11 claim `3388e5e9-cd73-46ad-b066-452e89c4dcf0`):**
  `.ai/workstreams/match-stroke-recognition-b.md`, `badminton_analysis/court/mapper.py`,
  `badminton_analysis/system.py`, `badminton_analysis/stroke/rallies.py`,
  `badminton_analysis/stroke/shot_boundary.py`, `app.py`, `static/kestrel.js`,
  `scripts/score_rally_labels.py`, `scripts/check_rally_gate.py`,
  `tests/test_court_annotation_headless.py`, `tests/test_court_view_gate.py`,
  `tests/test_rally_segmentation.py`, `tests/test_shot_boundary.py`, and the B11 plan, B11
  spec and B completion-bar spec.
- **State:** active
- **Branch:** `claude/match-stroke-recognition-b`, recreated 2026-09-29 from `origin/main` for
  B11. (The B1 branch of the same name was squash-merged as #2/#3 and deleted; the 2026-09-20
  defect fixes and the B11 plan landed from `claude/b11-rally-detection` as #7.)
- **Worktree:** primary checkout (no separate worktree)
- **Base commit:** `b4ee267bfac35fb75dd8ee81812a3a506b546d31` (`origin/main` after #10)
- **Head commit:** `b4ee267bfac35fb75dd8ee81812a3a506b546d31`
- **Last milestone:** 2026-09-29 - B11 execution started: branch recreated from `main`, scope
  claimed, plan to be executed task by task from Task 1. Previous milestone, 2026-07-29 -
  **B1 (both-player capture + hitter selection) complete.**
  All 7 tasks implemented via subagent-driven-development (fresh implementer + task review
  per task), plus a final whole-branch review that found and fixed 5 Important
  cross-task issues invisible at task scope: the racket YOLO model ran twice per frame,
  `_capture_side_pose` had no distance gate (letting one player's pose populate both sides
  when only one was detected), `detect_contacts_multi`'s `min_gap` was shared across
  players (silently dropping a genuine reply from the other side), a stale module
  docstring, and a missing end-to-end capture-to-recognition test. Re-review confirmed all
  5 fixed with no new breakage. Full committed suite: 433 passed.

## Acceptance criteria

See `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` section 2
("Done means") for the full list. Summary: both-player capture + proximity-based hitter
selection; a background-job deep-analysis path with live UI status; rally/play detection
fixed on both fixed-camera and broadcast footage so full-rally coverage is achievable;
per-rally BST invocation with coverage metadata; fps/resolution-normalized constants;
non-regression with no weights present; full committed suite green.

## Decisions and rationale

- Completion bar drafted by scope-planner, corrected against real on-disk full-match run
  artifacts (not just the existing design docs) — see the spec's section 0 for the premise
  correction (rally/play detection, not the frame-budget guard, is the dominant blocker).
- Owner resolved the three open questions on 2026-07-29 (spec section 6a): background job
  with UI-tracked status; both footage types in scope; strict full-rally coverage as the
  target now that time is no longer the limiting factor. This pulled two previously-deferred
  items (async job redesign, broadcast play detection) into scope.
- Owner approved landing the in-flight `player.py` net-line fix as part of this workstream.
- First implementation step, per owner direction: B1 (both-player capture + hitter-by-
  proximity selection), validated on the known-working 750-frame Axelsen clip segment where
  contacts already fire — not the full-match background-job/rally-detection work yet.
- **2026-08-23 — owner resolved B11's open questions (B11 spec §12a).** A = **no** (true
  multi-camera TV broadcast stays in scope, so B11 gains a shot-boundary component that no
  on-disk footage can validate); C = **no**, replaced same day with **uniform coarse windows
  over gate-passed court-view frames, marked degraded, with stroke recognition withheld on
  them** — chosen over labelling them unreliable because BST labels over non-rally windows are
  close to noise and a badge is weaker than the impression that strokes were detected;
  D = **yes** (completion-bar R10 amended, new R11 added). B and E were settled by measurement in the PR #4 shuttle investigation rather
  than by decision: there is no usable shuttle signal on the fixed-camera footage, so B11's
  swing signal is primary rather than a fallback.
- **B11 is the next milestone delivery**, chosen because it is the confirmed gate on all
  further real-footage validation (B1's own validation was blocked by exactly this) and it
  needs no new footage, unlike net-shot posture support, the two unvalidated posture
  thresholds, and §0.25 option D.

## Changed paths

- `badminton_analysis/tracking/player.py`, `tests/test_player_half_classification.py`
  (commit `fe31415`, net-line half-classification fix).
- `docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` (completion bar).
- `docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md` (B1 implementation plan).
- `badminton_analysis/detection/racket.py`, `badminton_analysis/stroke/events.py`,
  `badminton_analysis/stroke_recog/{hits,inputs,recognizer}.py`, `badminton_analysis/system.py`
  (the 7 B1 tasks + final-review fix pass), plus their test files.
- **B11** (`git diff --stat b4ee267..HEAD`, plus the final-review fix wave):
  - `badminton_analysis/stroke/rallies.py` (new: segmenter, shared wrists/caps helpers),
    `badminton_analysis/stroke/shot_boundary.py` (new, standalone, unwired),
    `badminton_analysis/system.py` (calibrated gate, `_rally_track`, post-loop segmentation,
    stroke withholding, pins), `badminton_analysis/court/mapper.py` (headless `annotate_court`),
    `app.py` (`_rally_summary`, `/api/stats`, job result), `static/kestrel.js` (rally copy,
    calibration stage).
  - `scripts/check_rally_gate.py` (new harness), `scripts/score_rally_labels.py`.
  - `tests/test_rally_segmentation.py`, `tests/test_court_view_gate.py`,
    `tests/test_court_annotation_headless.py`, `tests/test_app_rally_summary.py`,
    `tests/test_shot_boundary.py` (all new).
  - `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md` (sections 0.6a and
    15), `docs/superpowers/plans/2026-08-23-rally-play-detection-b11.md` (as-built pointer),
    and this file.

## Verification

- Per-task: 7 task-scoped reviews, all approved (task 7's fix-in-round for `min_gap`/pose-gate
  issues landed in the final-review pass below, not per-task).
- Final whole-branch review: 5 Important findings, 1 fix round, re-review clean, no new
  Critical/Important breakage.
- Full committed suite at `HEAD` (`1ac402b`): 433 passed (`--ignore=tests/test_ai_handoff.py`);
  also ran the full literal suite including that continuity file once during the task loop:
  713 passed, 0 failed.
- **Controller-run real-footage validation: run, result BLOCKED UPSTREAM** (2026-07-29). Full
  outcome recorded in the plan's own "Validation outcome" section
  (`docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md`). Summary: 0 BST strokes
  on the reconstructed clip, root-caused to `is_court_view`'s rally/court-detection gate
  limiting real analysis-track capture to one 87-frame window out of 752 — the same class of
  blocker the original BST T9 validation hit, and explicitly B11's scope, not B1's. B1's own
  correctness (done-means 1-3) already independently proven by a real end-to-end test added in
  the final-review fix pass; only item 4 (real-rally alternation) is unvalidated.
- **Critical data point, CORRECTED 2026-08-01:** this 25s clip took **17,443s (≈4.85h)**
  wall-clock, which this bullet originally attributed to a CPU-only machine
  (`torch==2.5.1+cpu`, no CUDA). **That attribution was false.** This machine has an NVIDIA
  RTX 4090 and the project venv's torch is `2.5.1+cu121` with CUDA available; the
  `requirements.txt` pin does not match what is actually installed (a real, separate
  reproducibility hazard worth tracking — a fresh install from `requirements.txt` would
  genuinely be CPU-only). The 17,443s was almost entirely a blocked interactive stdin prompt
  (`"Press Enter/Y to accept auto detection; press M/R/Esc for manual annotation."`, printed
  despite `--display false`), not compute: `auto_court_preview.png` was written at 02:43:06
  and the run did not proceed until `court_annotations.txt`/`metadata.json` appeared at
  07:26:56, 4h43m50s later. Actual analysis time was ~6m51s. Full investigation in
  `docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md`'s "Validation outcome"
  section and `docs/superpowers/specs/2026-07-30-rally-play-detection-b11-design.md` §0.10.
  The owner's Q1 decision (background job) is still correct, but for two different reasons:
  (1) the stdin-blocking defect just found can hang any unattended run indefinitely, and (2)
  TrackNetV3's dense pre-pass is genuinely slow at native 4K (2.33 s/frame measured, vs 0.385
  s/frame at 1080p) regardless of GPU, because the bottleneck is per-pixel preprocessing, not
  the network itself — not because the machine lacks a GPU.
- **B11 Task 6 fidelity gate (2026-09-30).** Command:
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B scripts/score_rally_labels.py [--detections <path>] [--out <path>]`
  (as-shipped constants swing_frac 0.25 / gap 1.0 / min_len 2.0; labels `outputs/b11-labelling/LABELS.md`).
  - Step 1, UNMODIFIED script, pre-#7 fixture (`outputs/Dji 20260718111111 0010 D/detections.jsonl`):
    F1 0.6439, precision 0.642, recall 0.646, 10/11 rallies found, start median +1.90 s,
    end median +0.59 s. Matched the pre-task backup; §0.16's F1 0.644 applies to this
    pre-#7 fixture only.
  - Step 3, script routed through `rallies.wrists_from_hands` / `baseline_caps` /
    `swing_activity` / `segments_from_activity` (caps 21.3 / 120.9 px from `metadata.json`),
    same pre-#7 fixture: F1 0.6439, precision 0.642, recall 0.646, 10/11, start +1.90 s,
    end +0.59 s. Byte-identical to Step 1 (including the grid search and `score_result.json`),
    so the +-0.02 F1 and 10/11 criteria both held with delta 0. Nothing tuned.
  - R10 re-measurement, a different input (post-PR-#7 run,
    `outputs/b11-post7-dji0010/detections.jsonl`, far-court hands in 14,256 records vs ~1,483):
    F1 0.6435, precision 0.646, recall 0.641, 10/11, start median +2.20 s, end median -0.26 s.
    A new measurement for new data, not a pass/fail against 0.644.
  - Tests: `tests/test_rally_segmentation.py` 46 passed; suite minus
    `tests/test_ai_handoff.py` 778 passed. The two script-equivalence tests now use a frozen
    copy of the d280bec script arithmetic as their oracle.
  - Tested commit: parent `d280bec` plus working tree (`scripts/score_rally_labels.py`,
    `tests/test_rally_segmentation.py`, this file); the commit that lands them is the one whose
    message is `test(rallies): score the shipped swing signal against the human labels`.
- **B11 Task 12 validation harness (2026-10-02).** `scripts/check_rally_gate.py` (new; prints
  only, writes nothing under `outputs/`). Reuses production code: `--gate` builds
  `BadmintonAnalysisSystem.__new__` (no weights) and calls `_load_template`,
  `_calibrate_court_view` (timed alone; a counting `cv2.VideoCapture` proxy reports frames
  grabbed/decoded) and `_court_view_downscaled` per frame; `--replay` uses
  `rallies.wrists_from_hands` and `rallies.segment_rallies(quad=corners)` with fps and corners
  from the run's `metadata.json` (shuttle key is `shuttlecock.image`). Command form:
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B scripts/check_rally_gate.py --gate <video> --template <png>`
  / `--replay <detections.jsonl>`.
  - **Contention:** another python process (PID 20108, ~2.4 GB, the long analysis job) was
    running throughout and was not stopped; all timings below are inflated by an unknown amount.
  - `--gate` DJI 0010 (3840x2160, 59.94 fps, 17,234 frames): calibration `median-4mad`,
    median 0.9428, MAD 0.01083, cut 0.8786, 575 samples. **Gate pass fraction 0.8754
    (15,086 / 17,234) - NOT the ~0.98 the plan expected to be preserved**: the old fixed 0.75
    gate recorded 16,889 / 17,234 = 0.980 frames for this clip. The calibrated cut sits well
    above 0.75, so this clip loses ~12% of frames to the gate. Recorded as a finding, not
    tuned; the speculated cause here (occlusion or camera shake, "not investigated") is
    **superseded by the gate-fix diagnosis below**: camera drift at the start of the video,
    fixed by shift tolerance plus the 0.75 cap (DJI 99.6% / Axelsen 97.8%). Calibration pre-scan 51.3 s (17,234 frames grabbed, 575 decoded); per-frame
    gate pass 475.5 s total (391.8 s decoding, 83.7 s grayscale + score).
  - `--gate` Axelsen broadcast (1080p, 60 fps, 64,085 frames): calibration `median-4mad`,
    median 0.6359, MAD 0.02349, cut 0.4966, 1,491 samples. **Gate pass fraction 0.9812
    (62,881 / 64,085)**, up from 0.0066 at the old 0.75 gate. Calibration pre-scan 30.6 s
    (64,085 grabbed, 1,491 decoded); per-frame gate pass 373.2 s total (199.3 s decoding,
    173.8 s grayscale + score).
  - `--replay` DJI 0010 pre-#7 (`outputs/Dji 20260718111111 0010 D/detections.jsonl`, 16,889
    records): `suppressed_static_shuttle` = 1 cluster at (473.0, 846.0), 3,941 points (R11
    fixture suppression confirmed on real data); signal `swing`, not degraded; 20 segments
    (median 5.37 s, shortest 2.05 s, longest 12.61 s); `shuttle_density` 0.00101;
    `caps_source` `quad`; `gated_outside_court` 236.
  - `--replay` post-#7 (`outputs/b11-post7-dji0010/detections.jsonl`, 16,889 records):
    identical suppression (1 cluster, (473.0, 846.0), 3,941 points), `swing`, not degraded;
    21 segments (median 4.20 s, shortest 2.34 s, longest 14.03 s); `shuttle_density` 0.00101;
    `caps_source` `quad`; `gated_outside_court` 236.
  - **not run:** Axelsen segmentation replay - its recorded `detections.jsonl` has ~426 records
    (recorded at the old 0.75 gate), so a replay is meaningless; it requires a full Axelsen
    re-run with the new gate (owner-run Step 4). Task 11 shot-boundary real-footage validation
    - no on-disk sample contains a cut (hermetic tests only).
  - Suite: `--ignore=tests/test_ai_handoff.py` 922 passed.
  - Tested commit: parent `f9aaf62` plus working tree (`scripts/check_rally_gate.py`, this
    file); the commit that lands them is `test(b11): staged gate and segmenter-replay
    validation harness`.

- **B11 gate fix (2026-10-02):** Task 12 found the calibrated gate passing 87.6% of DJI 0010
  (old 0.75: 98%). Fixed in `0de0ad1` (`system.py`, `tests/test_court_view_gate.py`):
  shift-tolerant unpinned score (`COURT_VIEW_SHIFT_PX = 4`, max NCC over the slid cropped
  template, shared by calibration and live gate) plus a 0.75 cap on calibrated cuts
  (`court.court_view.capped` / `calibrated_cut`). Spec correction: §0.6a.
  - Tests: RED 9 failed / 46 passed (new tests vs old code), GREEN 55 passed in
    `tests/test_court_view_gate.py`; suite `--ignore=tests/test_ai_handoff.py` 936 passed.
  - Per-frame score cost (synthetic gray, 300 iterations, mean): 4K 1.89 ms new vs 1.93 ms
    old; 1080p 1.18 ms new vs 1.27 ms old.
  - `check_rally_gate.py --gate`, DJI 0010: cut 0.75 (capped from 0.8813, median 0.9432, MAD
    0.0104, 575 samples), pass 17,167 / 17,234 = **99.6%**. Axelsen: cut 0.5189 (not capped,
    median 0.6633, MAD 0.0244, 1,491 samples), pass 62,690 / 64,085 = **97.8%**.
  - Tested commit: `0de0ad1` (the harness ran on the tree with those code changes
    uncommitted; the committed tree is identical).
- **B11 end-to-end run (commit `548c536`):** a real pipeline run on DJI 0010
  (`outputs/b11-e2e-dji0010`): signal `swing`, 19 segments, full provenance block, gate
  coverage 0.875, empty error log. Replaying that run's own `detections.jsonl` through
  `check_rally_gate.py --replay` reproduces all 19 segments. Scored against the human labels:
  F1 0.653, precision 0.671, recall 0.637, 10/11 rallies found, start median +2.22 s, end
  median -0.29 s.
- **B11 final-review fix wave (2026-10-02):** F1 (an internal segmenter failure is
  `signal: "error"`, not degraded), F4 (shuttle signal hedged as not yet validated), M1-M6
  (pre-merge minors) and the F2/F3 docs. Commits `f5603f7`, `c4e4d75`, plus the docs commit
  that carries this section.
  - Tests, each touched file alone: `tests/test_rally_segmentation.py` 154 passed,
    `tests/test_court_view_gate.py` 60, `tests/test_court_annotation_headless.py` 5,
    `tests/test_app_rally_summary.py` 24, `tests/test_shot_boundary.py` 21;
    `node --check static/kestrel.js` clean.
  - Suite `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
    --ignore=tests/test_ai_handoff.py`: **953 passed** in 33 s. Tested commit: `c4e4d75` (all
    code and tests; the only uncommitted changes at that moment were the B11 spec/plan docs).
  - `check_rally_gate.py --replay "outputs/Dji 20260718111111 0010 D/detections.jsonl"` after
    the fix: `swing`, 20 segments, not degraded (unchanged).
  - `tests/test_ai_handoff.py` was not run (not touched; continuity helper unchanged).

## Blockers

- Further real-footage validation of B1 is coupled to B11 (rally/court-view detection fixes on
  both footage types) — chasing a better clip segment without fixing detection first risks
  repeated runs with the same null result. Recommend addressing B11 (or at least a
  quick recalibration of `is_court_view`'s threshold) before another validation attempt.
- **RESOLVED (B11 Task 1, commit `577cff1`): the `annotate_court` hang.** The defect found
  during the 2026-07-29 validation run (the pipeline blocked on an interactive stdin prompt
  even with `--display false`, silently consuming 4h43m of that run) is fixed: `annotate_court`
  now takes `interactive`, the pipeline passes `bool(self.show_display)`, and a non-interactive
  run with no usable auto detection raises with a message instead of waiting on a keypress. Both
  the refusal and the call site are covered by `tests/test_court_annotation_headless.py`. The
  history of the root-cause investigation is in B11 spec section 0.10 and the B1 plan's
  "Validation outcome".
- **B11 known gaps at merge** (also in B11 spec section 15):
  - The shuttle signal path is unvalidated: no shuttle-based segmentation has been measured on
    any footage (section 0.16 measured the swing path only). Kestrel hedges it as "not yet
    validated"; `/api/stats` still sums shuttle runs.
  - Axelsen segmentation needs an owner-run full re-run with the new gate. Warning: the gate now
    admits about 98% of its 64k frames (it admitted 0.66% before), which multiplies run time and
    `_analysis_frames` memory; the DJI run peaked at about 2.4 GB.
  - Shot-boundary detection (`shot_boundary.py`) is standalone, unwired and unvalidated: no
    on-disk footage contains a cut.
  - Pins are constructor-only (`court_view_threshold`, `rally_signal`): there are no CLI flags
    because `main.py` is outside the claim, so the web UI cannot pin.
  - The legacy `web_ui.html` (`/` route) disables clips for swing runs.
  - Clip padding is 1.5 s, below section 0.16's >= 2 s start padding.

## Interruption: 2026-09-20 match-analysis defect fixes (branch `claude/b11-rally-detection`)

Owner reported three symptoms on the new `Dji 20260919111119 0007 D` run: no playable
analysed video, an empty upper-player heatmap, and no technique data. Diagnosed to three
distinct root causes and fixed on this branch ahead of the B11 tasks.

- **Unplayable video** (`ee2b5fa`). OpenCV could not initialise H.264 (openh264 DLL version
  mismatch) and silently fell back to MPEG-4 Part 2, which no browser decodes. The ffmpeg
  rescue transcode was then killed by a fixed 300 s timeout; measured, that video needs
  ~425 s (20 s encodes in 12.5 s). It left a truncated 848 MB file with no moov atom and
  the job still reported success. Budget now scales with duration, partial output is
  deleted, and the failure is reported in the job, the result payload and the UI.
- **Far player never detected** (`aafdc6e`, `bd25b9d`). `process_frame` passed no `imgsz`,
  so Ultralytics letterboxed 3840x2160 to its 640 default and the far player's 100-155 px
  body became 17-26 px. Fixed with a second pose pass over a far-court crop at
  `imgsz=1280`: measured 8/8 sampled frames against 0/8 for the shipped path and 5/8 for a
  whole-frame 2560 pass at twice the cost. Upper-half candidates only, plus a static
  filter for courtside bystanders. Real-footage check on a 15 s cut: upper present in
  **97.9%** of records (was **0 of 36,732**), lower unchanged at 61.9% (was 65.5%).
- **Perspective contact gate wired into the technique path** (`664df2a`). It existed since
  B11 but `TechniqueAnalysisRunner` still used the fixed 80 px, which is 0.13 m near and
  0.65 m far on this footage.

**Not fixed, and why.** The empty technique output is *not* a contact-gate problem:
measured over the run's 36,732 records the shuttle appears in 4.9% of frames, comes within
80 px of a hand in 6 frames and within the perspective radius in 3, and in neither case
does any such frame also carry the shuttle at `i-1`, `i` and `i+3` that the
direction-change test needs. Contacts are 0 under both radii. The binding constraint is
shuttle detection — B11 §0.7/§0.11/§0.17 reproduced on new footage — whose remaining path
is capture-side (§0.25 option D). Rally segmentation is also still wrong on this run (13
segments, one of them 546.8 s covering 80% of the video); that is what the B11 plan's
Tasks 4-9 address and was deliberately not attempted ad hoc.

The owner's existing run was repaired in place: its video was transcoded to H.264
(verified 40,697 frames and 678.96 s, matching the original) so it plays without
re-analysis. Its *data* still has no upper player — that needs a re-run.

## Next action

Execute the B11 plan (`docs/superpowers/plans/2026-08-23-rally-play-detection-b11.md`) task by
task from Task 1 (headless-safe `annotate_court`, still unfixed on `main`), with a task review
after each and a final whole-branch review. Tasks 6 and 12 run against the owner's footage and
`outputs/b11-labelling/LABELS.md`, both on disk. Task 11's real-footage validation stays
blocked on a genuine broadcast sample. B2-B10 remain separate, unplanned pieces of Sub-project B.

(History: the "write B11's plan" next action of 2026-08-23 is done — the plan merged with #7.)

<!-- ai-continuity:milestones:start -->
<!-- ai-continuity:milestones:end -->
