# Sub-project B — Full-Match Stroke Recognition: Proposed Completion Bar (DRAFT)

**Date:** 2026-07-29
**Status:** DRAFT — awaiting owner approval, redirect, or amendment. Nothing in this
document has been implemented. This is the scope-planning artifact for the
`match-stroke-recognition-b` workstream (`.ai/workstreams/match-stroke-recognition-b.md`,
currently `planned`, no acceptance criteria).

---

## 0. Verification of the stated premises (read this first — one premise is wrong)

The briefing that produced this draft assumed the frame-budget guard
(`SHUTTLE_PRETRACK_MAX_FRAMES = 2000`) was the main reason full-match stroke recognition is
dormant. Checking the actual on-disk output of completed full-match runs shows that's not
the dominant blocker — there are two larger, previously-unrecorded ones.

**Confirmed as previously understood:**

- Sub-project A is complete (`.superpowers/sdd/progress.md:342`), and its decomposition names
  B as "a per-rally segment dense-tracking pre-pass"
  (`docs/superpowers/specs/2026-07-14-match-analysis-ux-design.md:20-21`, `:160`).
- BST shipped code-complete/EXPERIMENTAL and was found dormant: shuttle detected in ~35% of
  frames, 0 contacts at every threshold tried
  (`docs/superpowers/specs/2026-07-12-bst-stroke-recognition-design.md:206-235`).
- TrackNetV3 was vendored as "project 1 of 2" and validated functionally on a 750-frame clip:
  92% shuttle density, 8 contacts (default) / 14 (loose), `{net:4, clear:3, uncertain:1}`
  (`.superpowers/sdd/progress.md:325`). Project 2 = the ShuttleSet numeric benchmark
  (`docs/superpowers/specs/2026-07-12-tracknetv3-shuttle-tracking-design.md:25-38`).
- The frame-budget guard exists exactly as described: `badminton_analysis/system.py:13`
  `SHUTTLE_PRETRACK_MAX_FRAMES = 2000`, skip-and-fall-back at `system.py:586-589`. Root cause
  recorded at `.superpowers/sdd/progress.md:327-328` (a whole-video pre-pass over 64,085
  frames ≈ 7h; ~6s per 16-frame batch ≈ 0.37s/frame on a 4090).
- The hitter/opponent bug is real and unfixed: `system.py:491-497` picks `"lower"` then
  `"upper"` and captures **one** pose; `stroke_recog/hits.py:43` reads `player_side` as the
  hitter; `stroke_recog/inputs.py:13-24` documents person-1 as permanently zero-filled.
- Neither BST nor TrackNetV3 used a numeric accuracy bar in v1; both deferred measurement to
  project 2 and shipped honestly labeled (`bst-...-design.md:129-138`,
  `tracknetv3-...-design.md:147-164`).

**Not as previously understood — new findings, from the completed full-match run under
`outputs\YTDown.com_YouTube_Nice-Angle-4K60FPS-Viktor-Axelsen-vs-Kod_Media_HhrFqbM-x4Q_001_1080p\`
and `outputs\Dji 20260718111111 0010 D\`:**

1. **Rally segmentation, not the frame budget, is the dominant blocker on broadcast
   footage.** The Axelsen run completed (`progress.json`: `stage=done`, `total_frames=64085`).
   `rally_segments.json` contains **7 rallies totalling 423 frames = 0.66% of the video**
   (longest 171 frames). `analyze.log:42` reports **279 records total** written to
   `detections.jsonl` — 0.4% of frames were court-view. `analyze.log:13` confirms the budget
   skip, `analyze.log:21` confirms **`Technique analysis: 0 strokes`**, and there is **no
   `strokes.json`** at all. Even a perfect per-rally dense pre-pass would have densely tracked
   423 frames of a 17.8-minute match. Cause: `system.py:828-832`'s `is_court_view` is a
   full-frame `cv2.matchTemplate` / `TM_CCOEFF_NORMED >= 0.75` against an auto-generated
   template — far too strict for broadcast cuts, score-bug overlays, zooms, and replays.
2. **On the owner's own fixed-camera footage, per-rally gating saves nothing at all.**
   `outputs\Dji 20260718111111 0010 D\`: 17,234 frames, 4K, 287s — `rally_segments.json`
   reports 5 "rallies" covering ~16,900 frames = **98% of the video** (one is 10,359 frames
   long). Court-view is nearly always true, so "rally segments" are not rallies; they are the
   whole video. `technique_summary.json` there is also **`stroke_count: 0`**. The "per-rally
   segment dense-tracking pre-pass" framing from the Sub-project A spec is not a viable
   cost-control mechanism on either footage class the project actually has — it under-covers
   broadcast (0.66%) and over-covers fixed-camera (98%).
3. **`track_video` cannot take a frame range, and the parameter that looks like one is not
   one.** `badminton_analysis/shuttle_track/tracknet.py:83` takes only a video path. `third_party
   /tracknet/infer.py:137`'s `video_range` is used **only for median-image generation**
   (`third_party/tracknet/dataset.py:748-760`); `Video_IterableDataset.__iter__`
   (`dataset.py:717-719`) always seeks to frame 0 and reads to EOF. Segment-scoped tracking is
   new code in `infer.py` + `tracknet.py` (+ a `CONTRACT.md` update), not a parameter
   pass-through. Mitigating detail: `Video_IterableDataset.__init__` accepts `median=`
   (`dataset.py:680`), so one median can be computed once and reused across segments.
4. **The dense pre-pass runs before rally segments exist.** `_run_shuttle_pretrack()` is
   called at `system.py:242`, before the frame loop; `rally_segments` is only appended during
   `_process_frame` (`system.py:371`) and written after the loop (`system.py:276`). Any
   segment-scoped design needs either a cheap pre-scan or a post-loop pre-pass. Post-loop is
   architecturally available because the dense track's only consumer is the analysis track
   (`system.py:552-557`) — the render path deliberately stays on yolo
   (`tracknetv3-...-design.md:41-45`).
5. **Contact detection is structurally single-player, so it misses the far player's hits
   entirely — a recall bug, not only a labelling bug.** `stroke/events.py:55` requires
   `dist(racket_head, shuttle) < contact_px`, and `racket_head` comes from the one tracked
   player (`system.py:545-549`). For upper-player hits the racket point belongs to the *other*
   player, so the proximity test can essentially never fire. The 8 contacts on the 750-frame
   clip are consistent with near-player-only detection. A full match cannot produce a
   plausible alternating stroke sequence until this is fixed.
6. **BST is invoked once over the whole match as if it were one rally**, and strokes carry no
   rally id: `system.py:664-667` calls `StrokeRecognizer.label_rally(self._analysis_track, ...)`
   on the entire track; `strokes.json` is `{strokes, distribution, shuttle_source}` only.
   Cross-rally discontinuities in the track can manufacture phantom direction-change contacts,
   and Sub-project C needs per-rally structure.
7. **The dense pre-pass writes no progress heartbeat, which directly breaks Sub-project A's
   done-bar.** `_write_progress("court_setup")` fires at `system.py:238`, then the pre-pass
   runs with no further write; `static/kestrel.js:477-481` renders "(updated Ns ago)". A
   multi-minute (let alone multi-hour) pre-pass makes A2's "always-advancing, never looks
   hung" false. `progressStages()` (`kestrel.js:436-438`) has no stage for it either.
8. **Frame-rate and resolution assumptions are unnormalized, and both real full-length
   videos are ~60 fps.** BST's window is `SEQ_LEN = 30` frames (`third_party/bst/CONTRACT.md:37`)
   and the validation clip was re-encoded to 30 fps, but the full Axelsen match is 60 fps
   (`metadata.json`) and the DJI clip is 59.94. At 60 fps the BST window spans half its
   intended duration. Same class of problem in `stroke/events.py:45`: `contact_px=80.0,
   lookahead=3, min_gap=15` are absolute pixels/frames, so they do not transfer between
   1080p/4K or 30/60 fps.
9. Minor, still unfixed from the BST T9 write-up: `--bst-model` alone still no-ops on the CLI
   (`main.py:66,69` set `analyze_technique` and `bst_weights` independently); the web route
   always sends `--analyze-technique`, so only bare CLI use is affected.
10. **In-flight uncommitted work is a prerequisite.** `badminton_analysis/tracking/player.py`
    is modified and `tests/test_player_half_classification.py` is untracked on branch
    `claude/motionbert-3d-lifting`; they fix upper/lower assignment to split at the true
    perspective net line instead of a fixed image row. Hitter-by-court-half is meaningless
    until that lands.

---

## 1. Goal (one sentence)

Make stroke recognition produce a trustworthy, per-rally stroke sequence for **both
players** on a **full-length match video**, within a bounded and visibly-progressing
analysis run, with the coverage it actually achieved stated honestly in the output and UI.

---

## 2. Done means

Functional/qualitative gates in the BST/TrackNetV3 tradition — no numeric accuracy target
(that stays project 2).

**Correctness (both players)**
1. `_capture_analysis_frame` records per-frame pose/position/racket-point for **both** court
   halves, and the existing single-player fields (`keypoints`, `nose`, `shoulder`, `hip`,
   `elbow_angle`, `player_side`) keep their current meaning so `TechniqueAnalysisRunner`
   output is unchanged when B's new path is off.
2. Contacts are detected for hits by **either** player: the contact test considers both
   players' racket points, so a full-match run yields hits attributed to both `lower` and
   `upper`.
3. `hitter` on each stroke is chosen by shuttle proximity at the contact frame, not by a
   hardcoded tracked side; BST's person-0 slot receives the **hitter's** pose and person-1
   the **opponent's** (no longer zero-filled).
4. On a real rally, the produced hitter sequence alternates in a physically plausible way
   (long runs of identical hitter are treated as a defect, not an artefact to be shipped).

**Full-match feasibility (bounded, no hang)**
5. `SHUTTLE_PRETRACK_MAX_FRAMES`'s all-or-nothing skip is replaced by an explicit,
   configurable **dense-tracking budget** that is *spent* on selected play segments instead
   of abandoning the pre-pass; a full-length match run never silently degrades to "no dense
   tracking at all" without saying so.
6. Dense tracking is scoped to explicit frame ranges: `track_video(..., frame_ranges=...)`
   (or equivalent) with one shared median image, verified to return the same trajectory for
   a range as the whole-video pass returns for those frames (± documented edge effects at
   range boundaries from TrackNet's 8-frame window).
7. A full-length match analysis completes end-to-end inside a stated wall-clock ceiling the
   owner approves (see Open Question A) on the reference machine, with no UI hang and a
   non-zero exit.

**Honesty and observability**
8. A new progress stage (e.g. `deep_analysis`) is wired through `progress.json` → `app.py` →
   `kestrel.js` stage list/labels, with a heartbeat updated at least every ~1s **during** the
   dense pre-pass, so A2's "never looks hung" contract holds for the longest stage in the
   pipeline.
9. `strokes.json` records coverage explicitly: which frame ranges were densely tracked, how
   many play frames existed, and how many rallies got labels. The results screen states
   coverage in plain language ("stroke labels cover 6 of 41 detected rallies") rather than
   implying whole-match coverage.
10. Strokes carry `rally_id`; BST is invoked **per rally segment**, not once across the whole
    match; the timeline UI groups by rally instead of rendering one unbounded arrow chain.

**Validation (the real done-check, controller-run, not in the committed suite)**
11. On a **full-length** match (primary target per Open Question B), the run produces: >0
    contacts in multiple distinct rallies, strokes attributed to both players, a non-empty
    coarse distribution, coverage metadata that matches the log, and a completed
    video/visualizations — versus today's ground truth of `stroke_count: 0` and no
    `strokes.json` on both full-length videos on disk.
12. Labels visibly track obvious strokes (serve/smash/clear) on at least one hand-checked
    rally; ambiguous cases land on `uncertain`. Reported qualitatively; if transfer is poor,
    B ships **experimental** and says so, exactly as BST and the quality model did.

**Non-regression**
13. With no TrackNet/BST weights present, behavior is unchanged from today; Fast mode still
    skips deep analytics; the pre-pass and recognition stay never-fatal (a failure leaves the
    match video and outputs intact).
14. Full committed suite green, including new hermetic tests for: both-player capture,
    hitter-by-proximity, per-rally invocation and `rally_id`, budget/segment selection,
    frame-range trajectory alignment, and coverage metadata.

---

## 3. In scope (MVP — the 20% that delivers the value)

1. **B1 — Both-player capture + hitter selection.** Additive per-frame records for
   `lower`/`upper`; hitter chosen by shuttle proximity at contact; both racket points
   considered by contact detection; BST person-0/person-1 filled correctly. This is the
   correctness prerequisite the BST spec itself demands before any meaningful validation, and
   it is independent of all throughput work.
2. **B2 — Frame-range-scoped dense tracking.** `frame_ranges` support in
   `shuttle_track/tracknet.py` + `third_party/tracknet/infer.py`, one shared median, cache key
   extended to include the ranges, `CONTRACT.md` updated with the new deviation-from-upstream.
3. **B3 — Budgeted segment selection replacing the hard skip.** Dense-track a selected subset
   of play frames up to an explicit budget (frames or seconds), plus an `eval_mode` choice for
   long videos (`nonoverlap` gives roughly 8× fewer forward windows than today's `weight`
   mode, since `sliding_step` goes 1 → 8). Selection heuristic kept deliberately dumb in v1
   (e.g. longest / most shuttle-active play segments first).
4. **B4 — Move the pre-pass so it can use real segment boundaries** (post-loop patch of
   `_analysis_track`/`_analysis_frames`, before technique analysis and BST) — the render path
   stays on yolo, per the TrackNetV3 spec.
5. **B5 — `deep_analysis` progress stage + heartbeat**, end to end through `app.py` and
   `kestrel.js`.
6. **B6 — Per-rally BST invocation + `rally_id` + coverage metadata in `strokes.json`**, and
   the minimum timeline-grouping change so the results screen stays usable at match scale.
7. **B7 — fps/resolution normalization** for the windows that now cross footage types: derive
   BST's 30-frame window and `detect_contacts`'s `lookahead`/`min_gap` from fps, and
   `contact_px` from frame or court scale. Small, but without it the constants tuned on a
   30 fps 1080p clip are wrong on both real 60 fps videos.
8. **B8 — Controller-run full-match validation harness** + an honest outcome addendum in a
   new B design spec, mirroring how BST and TrackNetV3 recorded theirs.
9. **B9 — Land the in-flight `player.py` net-line half-classification fix** (or explicitly
   re-do it inside B) before B1 depends on it.
10. **B10 — Background-job redesign for deep analysis** (per owner decision §6a-A): dense
    tracking + per-rally BST run as a detached job; `progress.json`/`app.py`/`kestrel.js`
    gain a real job-status surface (queued/running/done/failed + coverage-so-far) instead of
    a single synchronous request. Supersedes the plain progress-heartbeat framing in B5/done
    means item 8, which becomes a status field within this job rather than a blocking-call
    heartbeat.
11. **B11 — Rally/play detection fixes for both footage types** (per owner decision §6a-B),
    now load-bearing for "full coverage" rather than deferred:
    - Broadcast: replace or recalibrate `is_court_view`'s whole-frame `cv2.matchTemplate`
      gate (currently ~0.66% true-positive on the Axelsen match) with something that
      tolerates cuts, score-bug overlays, zooms, and replays.
    - Fixed-camera: detect rally start/stop *within* continuous court-view play (currently
      98% of the DJI clip is treated as one rally) so segments correspond to actual rallies,
      not the whole video.
    - **True multi-camera TV broadcast** (added 2026-08-23 by owner decision B11 §12-A =
      *no*): a third footage class, kept in scope rather than deferred. The single-angle
      Axelsen file is not a substitute for it, so this needs a shot-boundary component that
      B11 designs and tests hermetically while recording real-footage validation as **not
      run, blocked on a genuine broadcast sample**.
    This is itself a nontrivial, previously-unscoped computer-vision problem on both ends —
    treat it as its own design decision, not a small tuning pass (see the amended R10 below).

**Deliberately dumb in v1:** the segment-selection heuristic, the budget default, and the
coverage copy. All three are cheap to tune later and none of them need to be clever for B to
be useful.

---

## 4. Explicitly deferred (named, not rejected)

- **The ShuttleSet end-to-end numeric benchmark** — tracking radius metric, contact
  precision/recall, stroke-type accuracy + confusion matrix. This is *project 2* in the
  TrackNetV3 spec's own numbering (`tracknetv3-...-design.md:31-34`) and remains the owner's
  "measured accuracy" bar. B must not invent a numeric bar to substitute for it.
- **Sub-project C** — match-wide stroke-technique percentages and per-event
  tactical/strategy analysis. C depends on B (`match-analysis-ux-design.md:22-24`). B delivers
  the per-rally per-stroke data C needs and nothing more.
- ~~Broadcast shot-boundary / play detection~~ — **moved in scope as B11 per owner decision
  §6a-B.** No longer deferred.
- ~~Async / job-queue redesign of deep analysis~~ — **moved in scope as B10 per owner
  decision §6a-A.** No longer deferred.
- **Doubles.** BST is a singles model (`bst-...-design.md:23`).
- **Render-path unification** — the live overlay stays on `yolo11s-ball`
  (`tracknetv3-...-design.md:158-159`).
- **Per-hit stroke-label video overlay** — descoped in BST v1 because recognition runs after
  the video is written (`bst-...-design.md:185-187`); still true, still deferred.
- **BST retraining/fine-tuning, the 25/35-class fine taxonomy, drill-mode auto-detect**
  (`bst-...-design.md:21-28`).
- **Contact/stroke threshold *tuning* against ground truth** — project 2 measures first
  (`tracknetv3-...-design.md:161-162`). B only normalizes the constants for fps/resolution; it
  does not chase numbers.
- **`--bst-model` implying `--analyze-technique`** — a 2-line CLI ergonomics fix, unrelated to
  B's value. Fold in only if it costs nothing.
- **The pre-existing deleted `assets/*` files and other local dirt** — must never be swept
  into B's commits (`.ai/PROJECT_STATUS.md`, blockers section).

---

## 5. Risks and unknowns

- **R1 — Throughput may still not close, even segment-scoped.** Measured 0.37s/frame at
  1080p on a 4090. If play segmentation were fixed and a 17.8-minute broadcast match yielded
  ~35% play frames (~22k frames), that is ~2.3h in today's `weight` mode and roughly ~17min
  in `nonoverlap` — both estimates, not measurements, and the 4K DJI case is worse per frame.
  **The 8× `eval_mode` lever is doing most of the work in any plausible plan, and it costs
  tracking quality by discarding the temporal ensemble.** That trade has never been measured
  on this project's footage.
- **R2 — Segment selection could be self-defeating.** Budgeting to a subset of segments means
  most rallies get no labels; "full-match stroke recognition" then means "labels for the N
  rallies we could afford". That is honest and shippable, but the owner should confirm it is
  the product they want rather than discovering it at demo time.
- **R3 — 60 fps everywhere.** Both real full-length videos are ~60 fps and BST was trained on
  (presumably 30 fps) broadcast ShuttleSet — ShuttleSet's frame rate was not found documented
  in the vendored contract, so this needs confirming rather than assuming. If real, either
  resample to 30 fps for the analysis track or scale every window, and B7 becomes load-bearing
  rather than tidy-up.
- **R4 — Frame-range boundary effects.** TrackNet uses an 8-frame window and InpaintNet a
  non-causal 16-frame window (`tracknetv3-...-design.md:47-50`). Frames near a range edge will
  be worse than in a whole-video pass. Needs padded ranges and a documented, tested tolerance.
- **R5 — Memory.** The obvious `frame_arr` route (`third_party/tracknet/infer.py:199-201,
  224-226`) materializes a segment in RAM: ~1.8GB per 300 frames at 1080p, ~4× that at 4K,
  plus a median copy. Sub-chunking with overlap is likely mandatory. Separately,
  `_analysis_frames` (`system.py:108`) already carries a comment that its memory scales with
  video length; B doubles its per-frame payload.
- **R6 — Densifying the track changes an existing shipped feature.** `TechniqueAnalysisRunner`
  consumes the same `_analysis_track` (`system.py:901`). More contacts means more biomechanics
  reports and a different `technique_summary.json` on the same input. Intended, but it is a
  user-visible behavior change and needs explicit acknowledgement.
- **R7 — Domain shift is still unmeasured.** BST is broadcast-trained; the owner's footage is
  4K fixed-camera with a small far player. Even with everything above fixed, labels may be
  poor. The honest outcome (`experimental`) must be pre-agreed, not negotiated after the work.
- **R8 — Multi-file, cross-layer change surface.** `system.py`, `stroke/events.py`,
  `stroke_recog/{hits,inputs,recognizer}.py`, `shuttle_track/{tracknet,trajectory}.py`,
  `third_party/tracknet/infer.py` + `CONTRACT.md`, `app.py`, `static/kestrel.js`. `inputs.py`'s
  normalization is the highest-fidelity-risk unit and every existing test there stubs the
  person-1 zero-fill it currently asserts.
- **R9 — Sequencing/process.** B needs its own branch (`claude/` or
  `codex/match-stroke-recognition-b`), a scoped claim including
  `.ai/workstreams/match-stroke-recognition-b.md`, and no writes to `main`. The uncommitted
  `player.py` / `test_player_half_classification.py` work (confirmed 2026-07-29: complete,
  4/4 tests pass, owner-approved to land as part of B) is the first thing B commits. The
  continuity suite (`tests/test_ai_handoff.py`) is separately known to be flaky under long
  full-suite runs (unrelated to app code) — don't mistake it for a B regression.
- **R10 — Broadcast play detection (B11) is itself an unscoped, nontrivial CV problem, not
  a parameter tweak.** Reliably distinguishing "live play" from cuts/replays/score
  graphics/zooms across arbitrary broadcast footage is a real detection task (motion
  continuity, scene-cut detection, or a learned classifier are all plausible approaches, none
  yet evaluated on this project's footage). Budget it as its own design pass within B, not a
  quick threshold recalibration — recalibrating `TM_CCOEFF_NORMED`'s `0.75` cutoff alone is
  unlikely to close a 0.66%→acceptable gap given the failure modes observed (cuts, overlays,
  zooms all defeat whole-frame template matching structurally, not just by threshold).

  **Amended 2026-08-23 (owner-approved, B11 §12-D).** The paragraph above conflates two
  different problems, and its central prediction is measurably wrong for one of them. Split:

  1. **The court-view gate, on the footage actually on disk, is a self-calibration problem —
     not a structural one.** B11 §0.5 establishes that the file this project calls
     "broadcast" is a *single-angle continuous recording* with no cuts, no replays, and no
     score bug, and §0.6 shows a per-video automatic cut (`median − k·MAD`) does close the
     0.66%→large-majority gap on it. The prediction that threshold recalibration "is unlikely
     to close" that gap was therefore **false for this file**: the failure modes it blamed
     (cuts, overlays, zooms) are absent from it. What defeated the gate was a fixed global
     `0.75` cutoff applied to a similarity score whose absolute scale is video-dependent.
  2. **Rally segmentation *within* continuous play is the genuinely-unscoped part**, and it
     keeps R10's original weight. Nothing in the existing pipeline distinguishes rally
     boundaries inside an uninterrupted court view; today 98% of the fixed-camera clip is one
     rally. This is where B11's real design risk lives (see B11's own R1-R2, and R11 below).
  3. **R10's original failure modes are not retired — they are deferred to a footage class
     that is now in scope but absent from disk.** Owner decision B11 §12-A (2026-08-23) is
     **no**: true multi-camera TV broadcast, with cuts, replays and score-bug overlays,
     stays in B's scope rather than being split off as a future workstream. For that class
     the original paragraph stands as written, and a shot-boundary component is required.
     It cannot be validated against anything currently on disk — the Axelsen file has no
     cuts to detect — so B11 must design and hermetically test it while recording its
     real-footage validation as **not run, blocked on a genuine broadcast sample**.
- **R11 — The fixed-camera shuttle signal is measured, and it does not work (added
  2026-08-23).** B11's §0.7 blocker has since been investigated to a conclusion rather than
  routed around on suspicion: at 4K oblique, resolution was refuted as the cause (§0.20-0.21),
  the shuttle was located and physically verified (§0.22), and a purpose-built classical
  detector was costed, built, and **failed** — its coverage was anti-correlated with ground
  truth (§0.23-0.24). 86% of `yolo11s-ball`'s output on that footage is a single static
  fixture. Consequences: the swing signal is **load-bearing, not a fallback**, on the owner's
  own footage; B11's shuttle-density gate is what keeps a broken signal out of segmentation;
  and the remaining path to a working shuttle track is capture-time (§0.25, option D), which
  depends on the owner shooting a side-on or elevated clip and is not a code change.

---

## 6a. Owner decisions (resolved 2026-07-29)

**A (wall-clock ceiling): background job, tracked from the UI.** Deep analysis (dense
tracking + per-rally BST) runs as a detached background job; the results screen polls/shows
live status rather than blocking on a bounded synchronous run. This removes the wall-clock
pressure that was going to force `eval_mode=nonoverlap` (quality-losing) or aggressive
segment budgeting (coverage-losing) — see the consequence for Q5/E below.

**B (target footage): both** the owner's own 4K fixed-camera matches and broadcast footage.
Consequence: the broadcast rally/play detector (`is_court_view`'s `cv2.matchTemplate`
threshold, currently flagging only 0.66% of the Axelsen match as a rally) moves from
"deferred, separate workstream" **into scope** — see §3 items B10/B11 below. Fixed-camera
footage has the opposite problem (98% flagged as one giant rally) and needs a different fix:
detecting rally start/stop *within* continuous play, not detecting that play exists.

**C (partial vs. full coverage): strict full-rally coverage is the target**, now that A is a
background job — time is no longer the limiting factor, so there is no longer a budget reason
to leave a detected rally unlabeled. The remaining constraint is *detection*, not *budget*:
full coverage can only be as good as rally/play detection, which is why B10/B11 (fixing
detection on both footage types) become load-bearing rather than optional. Within a correctly
detected rally, individual strokes may still land on `uncertain` (domain shift, low BST
confidence) — that is the pre-existing, already-honest per-stroke degradation model, not a
coverage gap, and is unaffected by this decision.

**Scope changes from these three decisions:**
- The async/background-job redesign (previously deferred in §4) is now **in scope** — it was
  the single biggest unknown (R1) and is now resolved by design rather than measured/traded
  against.
- Broadcast rally/play detection (previously deferred in §4 as "a substantial separate
  workstream") is now **in scope**, added as B10/B11 in §3.
- `eval_mode=nonoverlap`'s quality-for-speed trade (Open Question E) is **no longer forced**
  by a ceiling; it becomes a tuning option only if the background job's total run time turns
  out to be impractically long in practice, not a default.
- Risk R1 (throughput) and R2 (segment selection self-defeating) are substantially
  de-risked by A; R7 (domain shift, unmeasured) and the new detection-accuracy risk (R10,
  broadcast play detection is itself an unmeasured, nontrivial computer-vision problem) are
  now the dominant remaining risks.

---

## 6. Open questions that genuinely need the owner's decision (historical — see §6a for resolutions)

**A. What is the acceptable wall-clock ceiling for a full-match deep analysis?** Today's full
match is 950s total (0.89× realtime) with dense tracking *off*. Every version of B makes that
longer. Is the bar "under ~5 minutes extra, synchronous", "up to ~30 minutes with a live
progress bar", or "hours is fine if it's a background job I can walk away from"? This single
answer decides whether B needs `eval_mode=nonoverlap` (quality cost), aggressive budgeting
(coverage cost), or the async redesign deferred above. **This is the question that changes
B's shape the most.**

**B. Which footage is B's acceptance target: the owner's own 4K fixed-camera match, or the
broadcast match?** They fail differently and need different work. Fixed-camera (DJI, 98%
court-view) needs budgeting, not rally gating, and is the app's real user scenario. Broadcast
(0.66% court-view) additionally needs a real play detector — a separate workstream.
**Proposed default: the owner's own full-length match is B's acceptance target; broadcast
gets an honest coverage report and no regression, and broadcast play detection becomes its
own named workstream.**

**C. Is partial coverage acceptable as "full-match stroke recognition"?** If B labels 6 of 41
rallies within budget and says so plainly, is that a shippable v1 (consistent with
BST/TrackNetV3 shipping honestly-labelled partial capability), or does "full-match" require
every detected rally to be labelled? If the latter, A's answer must be generous or B must
include the async redesign.

**D. Does the Sub-project A spec's "per-rally segment dense-tracking pre-pass" framing
stand, given the evidence?** The measured rally segmentation makes that mechanism unsound on
both footage classes. Proposal: amend the A spec's one-line description of B to "budgeted
dense-tracking pre-pass over selected play segments" so the specs stop contradicting the
data. That is a spec amendment for the owner to approve, not something to do silently.

**E. Is `eval_mode=nonoverlap` an acceptable quality trade for long videos?** It is the only
~8× lever available without new engineering, and it discards the temporal ensemble that
upstream treats as its best setting. Should B measure the density/accuracy delta on the
validated 750-frame clip first (cheap, ~5min of GPU) and bring the number back before
committing? **Recommended: yes, as B's first task.**

**F. When both players are captured, whose data drives the existing technique/biomechanics
report?** Safest is "unchanged — the currently-tracked player", with both-player data used
only by contacts/BST. Anything else silently changes a shipped feature's meaning.

**G. Does B own the ~2-line `--bst-model` → `--analyze-technique` CLI fix, or does it stay
deferred?**

---

## 7. Recommended shape if approved as drafted

Sequence, cheapest-and-most-informative first:

0. Measure `eval_mode` quality/throughput on the existing 750-frame clip (answers E, informs A).
1. Land the `player.py` net-line fix.
2. B1 both-player capture + hitter selection, validated on the 750-frame clip where contacts
   already fire.
3. B7 fps/resolution normalization.
4. B2 + B3 + B4 segment-scoped budgeted pre-pass.
5. B5 progress stage.
6. B6 per-rally + coverage + UI.
7. B8 full-match validation and honest outcome addendum.

Step 2 alone is independently valuable and independently verifiable: it turns the existing,
already-working short-clip path from "half the strokes are wrong or missing" into "both
players' strokes detected and attributed". If the owner's answers to A–C make the full-match
half of B expensive, **step 2 is the natural standalone first ship.**
