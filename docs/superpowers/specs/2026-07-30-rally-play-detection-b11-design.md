# Rally / Play Detection (B11) — Design Spec

**Date:** 2026-07-30
**Status:** DRAFT — awaiting owner approval, redirect, or amendment. Nothing here is
implemented. This is the design pass for item **B11** of the Sub-project B completion bar
(`docs/superpowers/specs/2026-07-29-match-stroke-recognition-b-design.md` §3.11), which that
document explicitly deferred to its own design decision (see its R10).

---

## 0. Measured evidence (read this first — two premises in the completion bar are wrong, and there is a third blocker nobody has recorded)

Everything below was measured on this machine against the real files on disk. The
completion bar's §0 was itself a premise correction; this section corrects it again in two
places and adds a blocker it did not know about.

### 0.1 Method (reproducible)

`is_court_view` is *only* a `cv2.matchTemplate` call, so its behaviour can be measured over
thousands of real frames without running pose, racket, TrackNetV3, BST, or video writing.
Throwaway scripts (scratchpad, not committed) replicated `_load_template` +
`is_court_view` exactly:

```python
tpl  = cv2.resize(cv2.imread(template_path, 0), (frame_W, frame_H))   # system.py:845
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)                        # system.py:376
score = np.max(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))    # system.py:954
```

Frames were read sequentially with `cap.grab()` and decoded only on sampled indices.
Samples:

| Video | Res / fps / frames | Sampling | Samples |
|---|---|---|---|
| `videos/YTDown.com_..._Axelsen-vs-Kod_..._001_1080p.mp4` | 1920x1080, 60.00 fps, 64,085 | every 20th frame | 3,205 |
| `videos/Dji 20260718111111 0010 D.mp4` | 3840x2160, 59.94 fps, 17,234 | every 10th frame | 1,724 |
| `<scratchpad>/b1_validation_clip.mp4` (the B1 clip) | 1920x1080, 30 fps, 752 | every frame | 752 |

Templates: `templates/_auto_<video stem>.png` in each case (the same ones the real runs used).

**The method is validated against the real pipeline runs.** At the shipped `threshold=0.75`
it reproduces the recorded numbers exactly: 0.66% on the full Axelsen match (matches
`rally_segments.json`'s 7 rallies / 423 frames = 0.66%) and **87 of 752** frames on the B1
clip (matches the B1 validation outcome's "one rally window, frames 263-350, 87 frames"
to the frame). The distributions below are therefore the real gate's real behaviour, not a
re-implementation of it.

### 0.2 Because the template is resized to the full frame, the "court detector" is a whole-frame framing matcher

`_load_template` (`system.py:845`) resizes the template to the **frame size**, so
`matchTemplate` returns a **1x1** result: a single whole-frame normalised cross-correlation
between this frame and one reference frame of this video. It is not localising a court. It
scores "does this frame's overall composition match the reference frame's composition",
which in practice means **camera zoom and framing**, dominated by the ~50-70% of pixels
that are crowd, banners, and background rather than court.

This is directly visible in the data: on the Axelsen match the 7 detected "rallies"
(frames 9518-9689, 17672-17696, 28922-29078, 38663-38717, 60851-60899) coincide exactly
with the highest-scoring frame clusters (9520-9680, 17680, 28920-29060, 38620-38700,
60860-60880), and inspecting those frames shows they are all at one particular **wider zoom
level** — the zoom the auto-template happened to be extracted at. The gate is reporting
"the camera is framed like the template", not "a rally is happening".

### 0.3 Score distributions

**Broadcast (Axelsen), n=3205:** min 0.3697, max 0.9654, mean 0.6032, std 0.0482.

```
 p1 0.4606   p5 0.5219   p10 0.5542   p25 0.5803   p50 0.6061
 p75 0.6257  p90 0.6485  p95 0.6741   p99 0.7374

 >=0.90   0.06%      >=0.65    9.27%
 >=0.85   0.12%      >=0.64   14.38%
 >=0.80   0.22%      >=0.63   21.15%
 >=0.75   0.66%  <-- shipped   >=0.62   30.95%
 >=0.70   3.00%      >=0.61   44.96%
 >=0.68   4.52%      >=0.60   57.57%
 >=0.66   6.80%      >=0.55   90.95%
                     >=0.50   96.88%
                     >=0.45   99.13%
```

Histogram: a single unimodal blob. 1,950 of 3,205 samples fall in `[0.58, 0.64)`; there is
**no valley** anywhere in the distribution. Otsu's method, applied to the observed
histogram, cuts at 0.5893 — i.e. straight through the middle of the single mode (68.2%
pass), which is what Otsu does when there is nothing to separate.

**Fixed-camera (DJI 0010), n=1724:** min 0.6209, max 0.9999, mean 0.8889, std 0.0535.

```
 p1 0.7434   p5 0.7666   p25 0.8759   p50 0.8998   p75 0.9284   p95 0.9496

 >=0.90  49.83%   >=0.80  88.63%   >=0.75  97.97%  <-- shipped   >=0.70  99.71%
```

**The two distributions barely overlap.** The DJI clip's *worst* frame (0.6209) sits at the
Axelsen match's *median* (0.6061). A single absolute threshold cannot serve both files;
0.75 is roughly correct for DJI and catastrophically wrong for the broadcast file.

**B1 clip, n=752:** min 0.5834, max 0.9904, p50 0.6205; 11.57% >= 0.75, 92.15% >= 0.60.

### 0.4 What the low-scoring and high-scoring frames actually are (frame inspection)

Representative frames were extracted and inspected at percentile positions and at the
extreme tails.

**Broadcast, mid-range scorers — all unmistakable live play, all rejected by the gate:**

| Frame | t | Score | What it is |
|---|---|---|---|
| 23460 | 391.0s | 0.6061 (p50) | Mid-rally overhead smash, full court in view |
| 44860 | 747.7s | 0.6257 (p75) | Mid-rally net shot, full court in view |
| 51860 | 864.3s | 0.5541 (p10) | Mid-rally jump smash |
| 1020 | 17.0s | 0.4606 (p1) | Between-points, both players on court, ready |

**Broadcast, lowest 24 scorers (0.3697-0.4470) — these ARE the non-play moments,** and they
cluster tightly in time: frames 32620-32860 (~544-548s: court being mopped, a player down
after a fall, medical break), 63940-64080 (~1066-1068s: post-match handshake at the net,
camera pushed in), 400-500 (~7-8s: pre-match introduction), 55500 / 63120 (celebration
close-ups).

**So the signal does carry real play/non-play information — but the shipped threshold cuts
at roughly p99.4, above almost all of the live play.**

**Fixed-camera, lowest 18 scorers (0.6209-0.7433):** frames where a person walks close past
the camera and occludes it, plus a few between-point moments. The camera never moves.

### 0.5 Premise correction 1 — the "broadcast" file is not multi-camera broadcast footage

A montage of **36 uniformly-spaced frames across the whole 17.8-minute video** shows the
**same single fixed camera angle, full court, every time**. There are no cuts to other
cameras, no replays, no score-bug graphics, no player close-ups, no dugout/crowd cutaways.
The file is a "Nice Angle 4K60FPS" single-continuous-angle YouTube recording. The camera
does vary its zoom modestly (a handful of pushed-in frames at 0.50-0.55), and the only
non-court content is the ~5-8 seconds of mopping/handshake/intro identified in §0.4.

The completion bar's §0.1 attributes the 0.66% to "broadcast cuts, score-bug overlays,
zooms, and replays", and R10 lists those same failure modes as structural defeats of
template matching. **None of them are present in this file.** Structurally, the project's
"broadcast" video is the *same footage class* as the owner's DJI footage — a continuous
single-angle recording — differing in camera position, resolution, and background.

### 0.6 Premise correction 2 — recalibration DOES fix the gate (contra R10), and that is why the gate is not the interesting problem

R10 predicted that "recalibrating `TM_CCOEFF_NORMED`'s 0.75 cutoff alone is unlikely to
close a 0.66%->acceptable gap". On this footage that prediction is wrong: a cut around
0.45-0.48 keeps ~98-99% of the broadcast video (all of the live play in §0.4) and rejects
the mopping/handshake/intro tail, which is exactly the correct answer for this file.

A fixed absolute constant still cannot transfer (§0.3), but a **robust lower-tail cut**
does, on all three videos:

| Rule | Broadcast | DJI | B1 clip |
|---|---|---|---|
| shipped `>= 0.75` | 0.66% | 97.97% | 11.57% |
| Otsu on observed histogram | 0.5893 -> 68.2% | 0.8453 -> 83.9% | 0.7239 -> 15.8% |
| `0.75 x observed max` | 0.7240 -> 1.6% | 0.7500 -> 98.0% | 0.7428 -> 12.5% |
| fixed `p2` | 0.4785 -> 98.0% | 0.7505 -> 98.0% | 0.5912 -> 97.9% |
| **`median - 4 x 1.4826 x MAD`** | **0.4730 -> 98.3%** | **0.7402 -> 99.7%** | **-> 100.0%** |
| `median - 3 x 1.4826 x MAD` | 0.5063 -> 96.5% | 0.7801 -> 92.8% | -> 100.0% |

Otsu and scale-relative-to-max both fail. A fixed percentile "works" only by forcing a
fixed rejection rate, which would be wrong on a video that genuinely is 50% non-play.
`median - 4 x robust-SD` is the only family tested that lands in the right place on all
three without assuming a rejection rate: it rejects only what is genuinely far below the
video's own modal court-view level.

**But here is why this does not close B11.** A correctly calibrated gate passes ~98-99% of
*both* videos. That converts the broadcast case into the DJI case: one enormous
"rally" spanning the whole video, which is exactly the failure the completion bar records
for the DJI clip (5 "rallies" covering 98%, one of them 10,359 frames). Fixing the gate
unblocks *analysis capture* — which is what B1's validation was actually blocked on — but it
does **not** produce rallies. Those are two different problems and the completion bar's
B11 text conflates them.

### 0.7 New blocker (not in the completion bar) — on the owner's own footage the shuttle signal is 86% a single static false positive

Analysing all 16,889 records of `outputs/Dji 20260718111111 0010 D/detections.jsonl`:

- Shuttle detected in **4,194 / 16,889 frames (24.8%)**.
- **3,617 of those 4,194 (86.2%)** sit within 25 px of the single fixed point
  **(473, 846)**. A frame crop at that location shows a small white/yellow shuttle-shaped
  fixture hanging on a pole against the dark blue hall banner. `yolo11s-ball` locks onto it.
- Gating detections to the annotated court polygon
  (`[[1724,1122],[2372,1135],[3827,2095],[145,2005]]`): **1 of 4,194**. Gating to a raised
  "play volume" (top edge lifted by 0.9x court depth so airborne shuttles over the far
  court still count): **17 of 4,194**.
- Removing the static cluster leaves 577 detections (3.42% of frames), of which only those
  17 are over the court. The remainder are adjacent courts — the DJI frame contains
  **several simultaneously-active neighbouring courts** (full-resolution frame inspection
  confirms: near court has both players plus an umpire chair; two more courts with other
  players are in shot), and `roi_corners` for this run is the **whole frame**
  (`[[0,0],[3839,2159]]`).

So: **on the fixed-camera footage the real on-court shuttle is found in roughly 0.1% of
frames.** Any rally segmenter driven by shuttle presence has no usable input there today.
An earlier pass of this investigation produced an encouraging-looking result on this clip
(shuttle-gap segmentation with 1.0s gap closing and a 2s minimum length gave 21 segments,
37% coverage, median 3.5s — very rally-plausible); **that result is invalid** and is
recorded here only so nobody re-derives it. It was segmenting on when a static background
artifact happened to be detected.

By contrast, the dense **TrackNetV3** trajectory from the B1 validation run
(`outputs/b1-validation/shuttle_trajectory.json`, broadcast-class 1080p footage) is good:
668/752 frames present (88.8%), 7 miss-runs, longest 22 frames (0.73s at 30 fps), **zero**
miss-runs >= 30 frames. On continuous play a shuttle-gap segmenter would correctly emit one
rally for that clip. Its polarity is right; what it lacks is a between-rally gap in that
25-second sample to test positive detection.

### 0.8 A swing-based signal is available and looks plausible on the fixed-camera footage

`detections.jsonl` already records per-frame wrist positions
(`players.<side>.hands.{left,right}`). Wrist presence on the DJI clip: **lower 95.6%**,
upper 16.0%. Applying the existing `rep_segmenter` idiom (per-frame wrist displacement,
teleport rejection at `max(100 px, 10 x median)`, 0.5s smoothing) to `max(lower, upper)`
wrist speed and segmenting at `0.25 x p99.5` with 1.0s gap closing and a 2s minimum:

**23 segments, 41.2% coverage, median 3.95s, longest 14.25s** over 287s of footage.

That is a rally-plausible structure for a singles match segment, from a signal whose input
is measured present in 95.6% of frames. It is **not validated against ground truth** — see
§9 and §12.

### 0.9 Cost measurements — NCC costs (GPU/CPU framing corrected in §0.10)

**Correction (2026-08-01):** the original version of this subsection, and §6 below, claimed
this machine was CPU-only and that the B1 validation run's 17,443s was compute time spent
processing a 25-second clip. Both claims were false — see §0.10 for the full correction and
the real numbers. The `matchTemplate` costs measured below are unaffected: they are real
OpenCV CPU-operation costs (this codebase has no GPU path for `cv2.matchTemplate`), measured
on this machine, and remain accurate regardless of the CPU/GPU question. Measured
`matchTemplate` cost, plus three candidate replacements, over the real videos:

| Signal | 1080p (ms/frame) | 4K (ms/frame) | Discriminates? |
|---|---|---|---|
| **A** whole-frame NCC (shipped) | 23.8 - 35.0 | 120.6 - 138.5 | yes (see §0.4) |
| **B** court-bbox-crop NCC | 11.3 | 62.5 | **no** — broadcast p50 collapses to 0.087 |
| **C** court-polygon-masked NCC @480w | 0.40 | 0.37 | **no** — broadcast p50 0.051 |
| **D** whole-frame NCC @480w downscale | **1.42** | **1.74** | **yes — same distribution as A** |

Signal **D** reproduces A's distribution closely (broadcast p50 0.6157 vs 0.6060,
pass@0.75 1.0% vs 0.7%; DJI p50 0.9086 vs 0.9002, pass@0.75 99.3% vs 97.9%) at **17x**
less cost at 1080p and **69x** less at 4K.

Two useful consequences. First, the shipped gate is not cheap: at 4K it costs 120.6 ms
every `COURT_VIEW_CHECK_INTERVAL = 3` frames, ~40 ms/frame amortised, ~693s (11.5 min) of
pure template matching for the 17,234-frame DJI clip. Second, B and C are dead ends worth
recording: the court surface is a near-uniform low-texture region, so after mean
subtraction an NCC over it is mostly noise — "just crop to the court" is the obvious
proposal and the data kills it.

### 0.10 CORRECTION (2026-08-01): this machine is not CPU-only, and the B1 run's 17,443s was mostly a blocked prompt, not compute

Two premises stated in the original §0.9 and in §6 were false. This document already has a
precedent for correcting its own prior premises (§0's introduction; the R10 amendment in
§12.D); this subsection follows that precedent rather than silently rewriting the earlier
text.

**The false claim:** "This machine is CPU-only (`requirements.txt` pins `torch==2.5.1+cpu`,
no CUDA GPU); the B1 validation run took 17,443s (~4.85h) for a 25-second clip," which §6
then elevated to "The CPU-only constraint is the primary design driver: it rules out any
per-frame learned classifier."

**What is actually true:**

1. **A GPU is present and in use.** `nvidia-smi` reports an NVIDIA GeForce RTX 4090. The
   project venv's installed torch is `2.5.1+cu121` with `torch.cuda.is_available() == True`
   (both verified by running them). The false inference came from `requirements.txt` pinning
   `torch==2.5.1+cpu` — that pin does not match what is actually installed in this venv. This
   is still worth recording as a real, separate hazard: a fresh install from
   `requirements.txt` would get a CPU build and would genuinely be this slow. It does **not**
   mean this machine, or the runs measured in this document, were CPU-bound.

2. **The 17,443s was almost entirely a blocked interactive prompt, not compute.** File
   mtimes in `outputs/b1-validation/`:
   - `02:43:06` — `auto_court_preview.png` written; the pipeline then printed `"Press
     Enter/Y to accept auto detection; press M/R/Esc for manual annotation."` and blocked on
     stdin. The run was headless/background with no stdin attached, despite `--display
     false` having been passed.
   - `07:26:56` — `court_annotations.txt` and `metadata.json` written: the prompt finally
     resolved, **4h43m50s later**.
   - `07:33:47` — all remaining outputs written.
   - **Actual analysis time was ~6m51s**, of which the TrackNetV3 dense pre-pass was ~4m49s
     (the run log records 47 iterations over 752 frames in 4:49 ⇒ **0.385 s/frame at
     1080p**).

3. **The run's own log recorded the real per-frame main-loop cost:** `"Frame 300: pose
   0.02s, shuttlecock 0.02s, shuttle draw 0.00s, players draw 0.02s, court draw 0.00s"` ≈
   **0.06 s/frame** for the streaming loop at 1080p — GPU-accelerated and fast, nothing like
   the multi-second-per-frame figure the false claim implied.

4. **A new GPU measurement, added by this correction:** TrackNetV3 on native 4K
   (3840x2160) DJI footage costs **2.33 s/frame** (900 frames in 2,099.5s), versus 0.385
   s/frame at 1080p. ~6x cost for 4x the pixels means the bottleneck is **per-pixel work (4K
   decode, resize, median-image generation) — not the neural network**, which sees a fixed
   288x512 input regardless of source resolution. This is actionable: downscaling or
   cropping before TrackNetV3 should cut its cost substantially, and it is independent of the
   CPU/GPU correction.

**A genuine defect this uncovered — not just a measurement artifact.** The pipeline blocks
on an interactive stdin prompt (`"Press Enter/Y to accept auto detection; press M/R/Esc for
manual annotation."`) even when invoked with `--display false`. It silently consumed 4h43m
in this run. This is directly relevant to B10 (the planned background-job redesign): any
unattended/background invocation can hang indefinitely at this prompt. Recorded here as a
finding; not fixed as part of this correction or this document.

**What survives unchanged.** Every `cv2.matchTemplate` / NCC measurement in §0.9 above, and
the §0.6 threshold-recalibration verdict, are OpenCV CPU-operation measurements made on this
machine and are unaffected by the GPU correction. The signal-D 480px-downscale finding
(17-69x cheaper, same distribution) also stands. §1, §3, §6, §11, and §13 below are updated
to remove the false CPU-only framing while keeping the cost numbers that were always real.

---

### 0.11 TrackNetV3 measured on native 4K DJI footage: does NOT supply a usable shuttle input

The owner authorised the "highest-value cheap experiment" from §0's open question B: run
TrackNetV3 (not `yolo11s-ball`) on the DJI 4K footage and see whether it supplies the shuttle
track that C2's rally segmentation depends on. Two runs, both on a 15 s / 900-frame native-4K
segment cut at `-ss 25 -t 15` from `Dji 20260718111111 0010 D.mp4` (chosen because
`detections.jsonl`'s non-artifact shuttle hits cluster densest at 25-40 s). GPU (RTX 4090),
`device=None` which `third_party/tracknet/infer.py:158` resolves to cuda when available.

| Metric | `yolo11s-ball` (recorded run) | TrackNetV3, full frame, 900 fr | TrackNetV3, frames 0-300 |
|---|---|---|---|
| Frames with a shuttle | ~25 % | **95.3 %** (858/900) | **94.3 %** (283/300) |
| Single dominant static position | 87 % @ (473,846) | **42.5 % @ (1829,536)** | 7.4 % @ (1792,585) |
| Zero-motion consecutive pairs | - | ~25 % | 6.4 % |
| Median consecutive displacement | - | 7.0 px | 11.3 px |
| **Inside the court polygon** | 1 of 4,194 | **0 of 858** | **0 of 283** |

**The 95 % detection rate is misleading, and visual inspection is what establishes that.**
Cropping the frame around individual detections and looking at them:

- The 42.5 % cluster at (1829,536), held unbroken for frames 314-614 (5.0 s), is a **ceiling
  light fixture** in the hall roof. Not the shuttle.
- A sampled *moving* detection (frame 134, (1605,1019)) is a **racket being swung on a
  different court in the background**. Not the analysed court's shuttle.

**Root cause: footage framing, not the tracker.** This DJI camera captures a multi-court
training hall. The analysed court quad spans x 145..3827, y 1122..2095 — 96 % of frame width
but only the bottom ~45 % of frame height. The upper ~52 % of every frame is hall ceiling,
spectators, and *other courts in simultaneous play*. TrackNetV3 was trained on broadcast
footage where one court dominates the frame; here it has several candidate shuttles plus
static roof clutter and picks the wrong ones.

**Why "inside the court polygon" is itself the wrong test** (and why §0's earlier "1 of 4,194
inside, 17 in a raised play volume" reads the way it does): the polygon is the court *floor*.
A shuttle in flight is above the floor, which in image space means *above* the quad. So a
0 %-inside result is expected even for a perfect tracker and is not by itself evidence of
failure. The evidence of failure is the visual inspection plus the vertical distribution:
**100 % of the cropped run's 283 detections sit above y=1122 with median y=831**, i.e. in the
band where the *background* courts appear (further from camera ⇒ higher in frame), not on a
trajectory over the analysed near court.

**A crop cannot cleanly fix this, and the experiment that looked like it tested that did
not.** The second run was intended as a court-crop test, but the computed crop came out at
**96.4 % of the 4K area** (3840x2082) — because the court already spans 96 % of frame width,
and the headroom the shuttle legitimately needs above the court pushed the top edge to y=51.
So the two runs above differ by *segment length* (900 vs the first 300 frames, which exclude
the 314-614 static-lock stretch), **not** by cropping. The apparent improvement in the static
-lock and zero-motion columns is that confound, not a crop effect. Recorded explicitly so no
one reads the third column as "cropping helps."

The deeper point stands independently of that failed test: the shuttle over the *analysed*
court flies from y≈2095 (near baseline) up through y≈500-800 on a high clear, and the
*background* courts occupy y≈600-1100. **Those bands overlap**, so no axis-aligned spatial
crop can separate the analysed court's shuttle from the background courts' shuttles.
Disambiguation needs a non-spatial signal — e.g. requiring the shuttle to be near the tracked
players, or temporal-trajectory continuity anchored on the near court.

**Cost:** 2.33 s/frame at native 4K (2,099.5 s for 900 frames) and 1.63 s/frame on the
96.4 %-area re-encode, both on the 4090 — consistent with §0.10.4's finding that per-pixel
preprocessing, not the network, dominates.

**Consequence for this design.** C2's shuttle-based rally segmentation (signal S1) is
**unavailable on the owner's own footage class** with either shuttle detector currently in the
repo. This is not fixable by swapping detectors, which was the hypothesis worth testing and is
now tested. Either C2 relies on the non-shuttle signals for this footage class (the
wrist/swing signal, §0's 23-segment result — plausible but still unvalidated), or the shuttle
input needs a disambiguation stage that does not exist yet. This is a **new, measured blocker
on B11's fixed-camera half**, and it strengthens rather than weakens the §0 recommendation
that the owner decide whether multi-court hall footage is a supported input at all.

---

## 1. Goal

Make the pipeline (a) capture analysis data on essentially all frames where the court is
actually in view, on both of the project's footage classes, and (b) emit `rally_segments`
that correspond to actual rallies rather than to camera framing or to the whole video — at a
per-frame cost no greater than today's (§0.10 corrects an earlier false CPU-only framing of
this constraint; the cost discipline itself still applies), with the achieved quality
reported honestly rather than assumed.

---

## 2. Background — why this is B11 and not a tuning pass

`is_court_view` (`system.py:952-956`) is a single gate with two jobs it cannot both do:

1. **Transitively gates all analysis.** A non-court frame returns at `system.py:406-413`,
   *before* `_capture_analysis_frame` is called, so this one comparison decides whether any
   pose, racket, both-player capture, contact, or BST data exists for a frame. On the B1
   validation clip it admitted 87 of 752 frames, which is why B1's real-footage validation
   produced 0 strokes (`docs/superpowers/plans/2026-07-29-match-stroke-recognition-b1.md`,
   "Validation outcome").
2. **Drives `rally_segments`** via `is_court_view_count` / `consecutive_non_court_frames`
   with 5-frame hysteresis both ways (`system.py:390-401`, thresholds at `:230-231`).

Job 1 wants a permissive gate. Job 2 wants a discriminative one. Conflating them is why the
same code under-covers one video by 150x and over-covers the other. §0.6 establishes that
job 1 is a calibration problem and job 2 is a different-signal problem; they should not
share a mechanism.

`COURT_VIEW_CHECK_INTERVAL = 3` (`system.py:18`) additionally makes the gate's answer up to
2 frames stale, a cost workaround that §0.9 shows is no longer needed.

---

## 3. Verdict on the central question

> Is broadcast play detection a recalibration, a different-signal problem, or a
> genuinely-learned-classifier problem?

**It is a recalibration — and it is the wrong question.** Three findings, in order of
importance:

1. **Not a learned-classifier problem.** The premise that motivated that framing (cuts,
   replays, score bugs, zooms) is absent from this project's footage (§0.5). Both files are
   continuous single-angle recordings. There is no shot-boundary problem to detect and no
   evidence in hand that this project needs a learned play/non-play classifier. That
   conclusion rests on the footage having nothing for such a classifier to classify, not on
   hardware — an RTX 4090 is available on this machine (§0.10 corrects an earlier false
   CPU-only claim), so a learned classifier is not foreclosed by lack of compute; it is
   simply unjustified by the evidence in hand.
2. **The court-view gate is a recalibration, specifically a self-calibration.** The signal
   already ranks real non-play below real play (§0.4); the shipped constant simply sits at
   ~p99.4 of the broadcast distribution. A robust lower-tail cut derived per video lands
   correctly on all three videos measured (§0.6). R10's prediction is wrong on this footage
   and should be amended.
3. **Rally segmentation is a genuinely different-signal problem, and it is the real B11.**
   No threshold on a whole-frame framing-similarity score can find rally boundaries inside
   continuous single-angle play, because the framing does not change between rallies. This
   needs a play-activity signal (shuttle motion, or racket-swing motion), which is not what
   the current gate measures at all.

**And a fourth finding that reorders the work:** on the owner's own footage the obvious
activity signal is unusable today — the shuttle detector finds the real shuttle in ~0.1% of
frames and 86% of its output is one static background artifact (§0.7). So fixed-camera rally
segmentation is not blocked on a smarter algorithm; it is blocked on a working input. That
is a **sequencing dependency on B2/B3 (TrackNetV3 on this footage) that the completion bar
does not record**, and it is the single most important thing in this document for planning.

---

## 4. Architecture — one shared gate, one shared segmenter, honest per-class outcomes

Split B11's two jobs into two mechanisms at different points in the pipeline.

```
                     per frame, streaming, must be cheap
video --> [C1 court-view gate: NCC @480w vs self-calibrated cut] --> capture or skip
              ^                                                          |
              |  C0 calibration pre-scan (strided, downscaled)           v
              +--------------------------------------------      _analysis_track_both
                                                                 _analysis_frames
                                                                          |
                     post-loop, offline, cost ~free                       v
        rally_segments.json <-- [C2 rally segmenter over the recorded track]
                                                                          |
                                                  per-rally BST (B6) <----+
```

**Why the segmenter moves post-loop.** `rally_segments` has no in-loop consumer that needs
it live: it is written after the loop (`system.py:303-312`), and the only other in-loop
effects of the rally state are `player_tracker.start_new_rally()` and
`shuttlecock_tracker.clear_trajectory()`. Everything B needs rallies *for* (per-rally BST
invocation, `rally_id`, coverage metadata — B6) happens after the loop anyway. Segmenting
offline over the already-recorded per-frame track costs effectively nothing, can use
non-causal windows freely (a rally's start is best judged knowing what follows), and can be
re-run and re-tuned without re-processing the video. This is the same architectural move the
TrackNetV3 spec made for the dense pre-pass and for the same reason.

**Does one mechanism serve both footage classes?** For C1, **yes** — one shared gate with a
per-video self-calibrated cut, validated on both classes (§0.6). For C2, **the same code,
but with materially different input quality per class, and therefore different honest
outcomes**: broadcast-class footage has a good dense shuttle track (88.8%, §0.7) and can use
the shuttle signal; the owner's fixed-camera footage has no usable shuttle signal today and
must fall back to the swing signal (§0.8), whose quality is unvalidated. C2 must therefore
declare which signal it used, per video, in its output — not silently degrade.

---

## 5. Components & interfaces

### C0 — `_calibrate_court_view()` (new, `badminton_analysis/system.py`)

Runs once, before the frame loop, after `_load_template`. Strided pre-scan of the video at
the downscaled resolution:

- Sample every `ceil(fps/2)`-th frame (~2 samples/second) to a cap of ~1,500 samples.
- Score each with the same downscaled NCC that C1 uses (signal D, §0.9).
- Cut = `median - COURT_VIEW_MAD_K x 1.4826 x MAD`, default `COURT_VIEW_MAD_K = 4.0`
  (§0.6), clamped to `[0.0, 0.95]`.
- **Template-sanity check:** if the calibration median is below `COURT_VIEW_MIN_MEDIAN`
  (proposed 0.30), the template almost certainly does not correspond to this video. Log a
  clear warning, set the cut to `-inf` (pass everything), and record
  `court_view.calibration = "template_mismatch_pass_all"` in `metadata.json`. Losing the
  gate is strictly better than losing the whole analysis, and the failure is stated rather
  than silent.
- Record `{median, mad, cut, samples, method, k}` under `metadata.json`'s `court_view` key
  so a run's gate behaviour is auditable after the fact.
- Cost: 1,500 x ~1.5 ms plus decode of ~1,500 frames — order 10-20 s on either video, once.

An explicit `--court-view-threshold` override bypasses C0 entirely (records
`calibration = "manual"`), preserving today's behaviour for anyone who needs it and giving
the validation harness a way to pin the gate.

### C1 — `is_court_view` (rewrite, `system.py:952-967`)

```python
COURT_VIEW_SCORE_WIDTH = 480      # downscale width for the gate's NCC

def is_court_view(self, gray_frame, template_small, threshold=None):
    """Whether the court is in view, by whole-frame NCC at COURT_VIEW_SCORE_WIDTH.

    `threshold` defaults to the per-video cut from _calibrate_court_view().
    """
```

- Template is downscaled **once** at load time (`_load_template` gains a
  `template_small` return) rather than per frame.
- `COURT_VIEW_CHECK_INTERVAL` drops to 1 (evaluate every frame) — at 1.4-1.7 ms/frame it
  is cheaper than today even without the interval, and the 2-frame staleness goes away.
  Keep the constant so it can be raised again if a future footage class is slower.
- The keyword `threshold` parameter stays for the existing hermetic tests and for the CLI
  override.

### C2 — `badminton_analysis/stroke/rallies.py` (new module)

```python
def segment_rallies(track, fps, *, signal="auto", gap_sec=1.0, min_len_sec=2.0,
                    swing_frac=0.25) -> tuple[list[RallySegment], dict]:
    """Segment a post-loop analysis track into rallies.

    `track` is `_analysis_track_both`: per-frame
    {frame, racket_lower, racket_upper, shuttle}.
    Returns (segments, provenance) where provenance records which signal was
    chosen, why, and the parameters used.
    """
```

Signal selection (`signal="auto"`):

1. **`shuttle`** — used when the shuttle track is dense enough to trust: `shuttle`
   non-`None` in >= `SHUTTLE_DENSITY_MIN` (proposed 0.50) of gate-passed frames **after**
   static-artifact suppression (below) and after gating to the raised court play volume.
   Activity mask = shuttle present; close gaps shorter than `gap_sec`; drop runs shorter
   than `min_len_sec`.
2. **`swing`** — fallback. Activity = 0.5s-smoothed `max` over both players' racket-point
   displacement per frame, with the `rep_segmenter` teleport rejection
   (`max(TELEPORT_MIN_PX, 10 x median)`) reused verbatim; threshold at
   `swing_frac x p99.5` of that video's own activity (self-relative, exactly the
   `PEAK_FLOOR_FRAC` idiom at `rep_segmenter.py:88`); same gap-close and minimum-length
   post-processing.
3. **`none`** — neither signal qualifies: emit **zero** rallies and say so. Emitting one
   whole-video "rally" is the current bug and must not be the fallback.

`gap_sec`, `min_len_sec`, and the smoothing window are **seconds**, resolved to frames via
`fps` at call time — B7's fps-normalisation requirement applies here from the start rather
than being retrofitted.

**Static-artifact suppression** (needed because of §0.7, and cheap): over the recorded
trajectory, bucket shuttle points into a coarse grid; any bucket holding more than
`STATIC_FRAC_MAX` (proposed 0.25) of all detections whose points span less than
`STATIC_SPREAD_PX` (proposed 25 px) is a fixture, not a shuttle — drop those points and
record the suppressed location and count in the provenance. A real shuttle never occupies a
25 px box for minutes. O(n) over a few thousand points.

**Court-volume gating** of the shuttle before segmentation, using the annotated corners with
the top edge raised so airborne shuttles count. On the DJI clip this reduces 4,194
detections to 17 — which is the *correct* answer (that footage's shuttle detection does not
work) and is precisely why the density check must gate signal selection rather than the
segmenter blindly using whatever it is handed.

### `system.py` wiring

- The in-loop rally state machine (`system.py:382-404`) loses its `rally_segments`
  responsibility. It keeps `player_tracker.start_new_rally()` /
  `shuttlecock_tracker.clear_trajectory()` on court-view transitions, which are about
  tracker hygiene across genuine view breaks, not about rallies.
- After the loop and before technique analysis / BST, call `segment_rallies` and write
  `rally_segments.json` from its result.
- `rally_segments.json` gains provenance without breaking its existing shape:

```json
{
  "fps": 59.94,
  "rallies": [{"id": 1, "start_frame": 477, "end_frame": 848,
               "start_sec": 7.96, "end_sec": 14.15}],
  "detection": {
    "signal": "swing",
    "reason": "shuttle density 0.001 below 0.50 after artifact suppression",
    "params": {"gap_sec": 1.0, "min_len_sec": 2.0, "swing_frac": 0.25},
    "court_view": {"cut": 0.7402, "method": "median-4mad", "pass_frac": 0.997},
    "suppressed_static_shuttle": {"point": [473, 846], "count": 3617}
  }
}
```

The existing `rallies` array keeps its exact schema, so `player_positions.py` and the UI
are unaffected by the addition.

### Not touched

`badminton_analysis/visualization/player_positions.py:330-351` has its own independent
frame-gap rally detector (gap > 100 frames, minimum 150 frames) used only for position
visualisations. It is a separate concern operating on a different input and is deliberately
left alone; unifying it is out of scope (§10).

---

## 6. Cost budget (corrected 2026-08-01 — see §0.10)

An earlier version of this section claimed "the CPU-only constraint is the primary design
driver: it rules out any per-frame learned classifier." That claim was false: this machine
has an RTX 4090, and the project venv's torch is `2.5.1+cu121` with CUDA available (§0.10).
The reason C2 stays offline rather than streaming is not a CPU limitation — it is that
`rally_segments` only needs to exist once per completed video, so there is nothing to gain
from streaming it.

The `matchTemplate` costs below were always real CPU-operation measurements (this codebase's
NCC path has no GPU variant) and are unaffected by the correction:

| Stage | Cost | Notes |
|---|---|---|
| C0 calibration pre-scan | ~10-20 s per video, once | ~1,500 samples x (decode + 1.5 ms NCC) |
| C1 gate, 1080p | **1.4 ms/frame** every frame | vs 23.8-35.0 ms every 3rd frame today (~8-12 ms/frame amortised) |
| C1 gate, 4K | **1.7 ms/frame** every frame | vs 120.6-138.5 ms every 3rd frame today (~40-46 ms/frame amortised) |
| C2 segmenter | **O(frames), one pass, no decode** | operates on the recorded track; milliseconds total |

**Net: B11 makes the C0/C1/C2 per-frame budget cheaper, not more expensive** — roughly 6-8x
cheaper at 1080p and ~25x at 4K, saving ~11 minutes of wall clock on the DJI clip alone —
while removing the gate's 2-frame staleness. All four numbers above are measured on this
machine (§0.9), not estimated.

**What the real (GPU-corrected) budget is.** The genuinely expensive stage is not the gate —
it is TrackNetV3's dense pre-pass, measured post-correction at 0.385 s/frame at 1080p and
2.33 s/frame at native 4K (§0.10.4). The ~6x cost for 4x the pixels shows the bottleneck is
per-pixel preprocessing (decode/resize/median-image), not the fixed 288x512 network input —
so downscaling before TrackNetV3, not more GPU, is the lever. A full 64,085-frame match at
the 4K rate is **~41 hours** for that one pre-pass stage alone, even on a 4090; at the 1080p
rate it is **~6.9 hours**. The streaming main loop (pose + shuttlecock + draws) is cheap by
comparison at ~0.06 s/frame (§0.10.3) and is already GPU-accelerated.

**What this does and does not rule out.** A per-frame learned play/non-play classifier is no
longer foreclosed by hardware — a 4090 is available, so it is a genuinely open option, not
something this design can dismiss on cost grounds alone. This design still does not adopt
one, but for the reason given in §3 finding 1: the footage has no shot-boundary problem for
such a classifier to solve, which is a footage-content argument, independent of hardware.
Cost discipline still matters, for a narrower and more honest reason: TrackNetV3's dense
pre-pass is genuinely slow on 4K source video regardless of GPU, which is why the
per-frame budget above is still worth minimising and why B10's background-job work remains
warranted (§0.10's stdin-blocking finding is a second, independent reason for the same
conclusion).

The cost that B11 *does* add is downstream and indirect: opening the gate from 0.66% to ~98%
on the broadcast video means pose/racket/shuttle detection now run on ~98% of frames instead
of 0.66%. That is the intended effect (it is what makes any stroke recognition possible),
but it is a large real increase in total run time and it lands squarely on the background-job
redesign (B10) rather than on B11. Anyone planning B11 in isolation should expect the
*first* full-match run after it lands to be dramatically slower than the recorded 950s.

---

## 7. Error handling & fallback

- **Template does not match the video** -> C0's sanity check logs and passes all frames
  (§5 C0). Never fatal, never silent.
- **Calibration pre-scan fails** (unreadable video, zero samples) -> fall back to the
  shipped constant `0.75`, log it, record `calibration = "fallback_constant"`.
- **No usable rally signal** -> zero rallies plus a stated reason. Downstream per-rally BST
  (B6) then labels nothing and reports zero coverage, which is honest. It must not fall back
  to one whole-video rally.
- **Segmenter exception** -> caught, logged, `rally_segments.json` written with an empty
  `rallies` array and `detection.signal = "error"`. The match video and all other outputs
  survive, per the never-fatal convention the TrackNetV3 and BST specs both established.
- **Zero rallies is a legitimate, reportable outcome**, not an error state.

---

## 8. Testing strategy

**Committed hermetic tests** (no video, no weights, no network):

- C0: synthetic score arrays -> `median - 4 x robust-SD` cut; the template-mismatch branch;
  the manual-override branch; the empty-sample fallback.
- C1: a synthetic frame/template pair at both resolutions -> identical decisions from the
  downscaled path and the full-resolution path on clearly-matching and clearly-non-matching
  input; the `threshold=` override still honoured (protects the existing tests).
- C2 signal selection: dense synthetic shuttle track -> `shuttle`; sparse track ->
  `swing`; neither -> `none` with zero rallies.
- C2 segmentation: synthetic activity with two known bursts separated by a 3s gap -> exactly
  two segments; a 0.5s gap with `gap_sec=1.0` -> one segment; a 1s burst with
  `min_len_sec=2.0` -> zero segments.
- C2 fps normalisation: the same *temporal* pattern at 30 fps and 60 fps -> the same
  segment boundaries in seconds.
- Static-artifact suppression: a synthetic track that is 90% one fixed point -> that point
  suppressed, its location and count in the provenance, and the remaining points intact.
- Non-regression: with `--court-view-threshold 0.75` and the segmenter's signal forced,
  behaviour matches today's on a synthetic track.

**Controller-run real-footage validation** (not in the committed suite, mirroring the BST T9
and TrackNetV3 harnesses). Deliberately staged so the cheap checks come first, because a
full run costs hours on this hardware:

1. **Gate-only re-measurement** (minutes, no weights): re-run the §0 measurement scripts as
   a committed-adjacent harness against the new C1/C0 path on both videos; confirm the
   pass fractions land near 98-99% on both and that the rejected frames on the broadcast
   video are still the mopping/handshake/intro frames.
2. **Segmenter-only replay** (seconds): run `segment_rallies` over the *already recorded*
   `detections.jsonl` from the two completed runs. This needs no video processing at all and
   directly answers "does this produce rally-plausible segments" on real data.
3. **One full run** on the owner's fixed-camera footage, then a hand-check of a sample of
   emitted boundaries against the video.

---

## 9. Success criteria (done-means)

Functional and qualitative, in the BST / TrackNetV3 / quality-model tradition. **No numeric
accuracy bar** — there is no labelled rally ground truth for this footage, and inventing one
here would pre-empt the ShuttleSet benchmark that remains the owner's measured-accuracy bar
(§10).

**Court-view gate**

1. On both project videos, the gate admits the large majority of frames a human would call
   "court in view", with the per-video cut derived automatically and recorded in
   `metadata.json`. Against today's measured ground truth: 0.66% -> a large majority on the
   broadcast video; ~98% preserved (not regressed) on the fixed-camera video.
2. The frames the gate still rejects on the broadcast video are, on inspection, genuinely
   non-play (the mopping / handshake / introduction periods of §0.4) rather than live play.
3. A template that does not correspond to the video produces a logged warning and a
   pass-all gate, not a silently empty analysis.
4. Measured per-frame gate cost does not exceed today's amortised cost on either video.

**Rally segmentation**

5. `rally_segments.json` on the fixed-camera clip no longer reports a handful of
   multi-thousand-frame segments covering ~98% of the video. Segment lengths are
   rally-plausible (seconds, not minutes) and their count is in the same order as the
   rallies a human counts in that footage.
6. Every `rally_segments.json` states which signal produced it, why that signal was chosen,
   and the parameters used.
7. When no usable signal exists, the output is zero rallies with a stated reason — never one
   whole-video rally.
8. Segment boundaries are expressed in seconds internally and are invariant to fps: the
   same footage at 30 and 60 fps yields the same boundaries in seconds.
9. On at least one hand-checked stretch of real footage, emitted boundaries visibly
   correspond to rally starts and stops. **Reported qualitatively, and reported honestly:
   if the swing-based fallback turns out to be poor on the owner's footage, B11 ships that
   half labelled experimental and says so** — exactly as BST and the quality model did.

**Non-regression**

10. With `--court-view-threshold` set to `0.75` and the signal pinned, behaviour matches
    today's.
11. The segmenter is never fatal: any failure leaves the match video, `detections.jsonl`,
    and all other outputs intact.
12. Full committed suite green, including the new hermetic tests in §8.

**Explicitly not claimed as done by B11:** that stroke recognition now fires end-to-end.
B11 removes the *detection* blocker; contacts and BST labels additionally depend on B2-B7
and on the domain-shift risk (R7) that remains unmeasured.

---

## 10. Out of scope (deferred, named not rejected)

- **B2-B10** — frame-range-scoped dense tracking, budgeted segment selection, moving the
  dense pre-pass post-loop, the `deep_analysis` progress stage, per-rally BST invocation and
  `rally_id`, fps/resolution normalisation of `contact_px` / `lookahead` / `min_gap` and
  BST's `SEQ_LEN`, the full-match validation harness, and the background-job redesign. B11
  produces rally boundaries; consuming them is B6's job. (B11 does normalise its *own*
  time constants to seconds — see done-means 8 — because retrofitting that later would be
  strictly worse.)
- **The ShuttleSet end-to-end numeric benchmark** — tracking radius metric, contact
  precision/recall, stroke-type accuracy and confusion matrix. Project 2 in the TrackNetV3
  spec's numbering and the owner's actual measured-accuracy bar. **B11 must not invent a
  numeric rally-detection bar to substitute for it.**
- **Fixing `yolo11s-ball` on 4K oblique fixed-camera footage.** §0.7 shows it is effectively
  non-functional there. Diagnosing or retraining the shuttle detector is its own project;
  B11 only detects the condition and routes around it.
- **Restricting the analysis ROI to exclude adjacent courts.** The DJI run's
  `roi_corners` is the whole frame, so neighbouring courts' players and shuttles are inside
  the analysis region. Real, worth fixing, and a different change (court/ROI setup) from
  rally detection.
- **True multi-camera broadcast footage** — shot-boundary detection, replay rejection,
  score-bug handling. The project has no such sample (§0.5). If the owner later supplies
  one, this design's C1 is untested on it and a shot-boundary component would be genuinely
  new work. See Open Question A.
- **Unifying `player_positions.py`'s independent frame-gap rally detector** (§5).
- **Doubles.** BST is a singles model.
- **Score-recognition / point-boundary detection.** A rally is not a point; deriving score
  or point structure is not attempted.
- **Per-rally video clip export or timeline UI changes** beyond what B6 needs.
- **The pre-existing deleted `assets/*` files and other local dirt** — must never be swept
  into B11's commits (`.ai/PROJECT_STATUS.md`, blockers).

---

## 11. Risks and unknowns

- **R1 — The swing signal is unvalidated.** §0.8's 23 segments / median 3.95s on the DJI
  clip is plausible, not verified. An attempt to verify boundaries by eye from thumbnail
  montages was **inconclusive** at that scale; confirming them needs frame-by-frame review
  of real footage, which the owner is better placed to do than a montage. If the signal is
  poor, the fixed-camera half of B11 ships experimental (done-means 9).
- **R2 — Fixed-camera rally segmentation is really blocked on the shuttle track (§0.7), and
  that is a sequencing dependency on B2/B3.** TrackNetV3's performance on 4K oblique
  fixed-camera footage has **never been measured** — the only dense trajectory on disk is
  from broadcast-class 1080p footage. If TrackNetV3 also struggles at 4K oblique, the
  fixed-camera class has no shuttle signal at all and the swing fallback becomes
  load-bearing rather than a fallback. Measuring TrackNetV3 on a short DJI segment is cheap
  relative to a full run and should precede committing to this design's signal-selection
  logic.
- **R3 — Opening the gate multiplies run time.** Going from 0.66% to ~98% court frames on
  the broadcast video means ~150x more frames reach pose/racket/shuttle detection. Even with
  the RTX 4090 available (§0.10), the first post-B11 full-match run will be far slower than
  the recorded 950s: those stages' cost is dominated by per-pixel preprocessing at full
  source resolution, not raw GPU throughput (§0.10.4, §6). Intended, but it makes B10
  (background job) a hard prerequisite for any full-match validation, not a parallel nicety.
- **R4 — More court frames changes an existing shipped feature.** `TechniqueAnalysisRunner`
  consumes the same `_analysis_track`; admitting ~150x more frames on broadcast footage will
  produce a substantially different `technique_summary.json` on the same input. Intended, but
  user-visible and worth stating (this is R6 of the completion bar, now with a magnitude).
- **R5 — `_analysis_frames` memory.** `system.py:138` already notes its memory scales with
  video length and B1 doubled its per-frame payload. B11 raises the *number* of entries by
  ~150x on broadcast footage. A 64,085-frame match at ~98% coverage is a genuinely different
  memory profile from 279 records. Needs a measurement before a full-match run, and possibly
  spilling `_analysis_frames` to disk — which would be new work in B2-B4's territory.
- **R6 — `median - 4 x MAD` is calibrated on three videos, two of them the same file.** It
  lands correctly on all three, but three samples of two footage classes is thin evidence for
  a constant. `k` must be a named, overridable constant, and its value re-checked whenever a
  new footage class appears.
- **R7 — The gate's low tail is not purely non-play.** On the broadcast video the tail
  contains both genuine non-play *and* occasional pushed-in live-play frames (§0.4, p10 at
  0.554 is a live jump smash). A cut at ~0.47 keeps those, but a footage class with more
  aggressive zooming would force a trade between admitting non-play and rejecting play. This
  design accepts that trade in favour of admitting play, on the grounds that a false-positive
  court frame costs compute while a false-negative costs the analysis entirely.
- **R8 — Two rally definitions will coexist.** `player_positions.py`'s frame-gap detector
  stays as-is (§10), so `rally_1_heatmap.png` and `rally_segments.json` may disagree about
  what rally 1 is. Pre-existing, but B11 makes the discrepancy more visible.
- **R9 — Sequencing/process.** B11 belongs on `claude/match-stroke-recognition-b` with a
  scoped claim including `.ai/workstreams/match-stroke-recognition-b.md` and no writes to
  `main`. `tests/test_ai_handoff.py` is separately known to be flaky under long full-suite
  runs (unrelated to app code) — do not mistake it for a B11 regression.

---

## 12. Open questions that genuinely need the owner's decision

**A. Does the project actually need true multi-camera broadcast support?** §0.5 establishes
that the file this project calls "broadcast" is a single-angle continuous recording with no
cuts, replays, or score bugs. The owner's §6a-B decision ("both fixed-camera and broadcast
footage") was made on the assumption that the Axelsen file *was* conventional broadcast. If
what the owner wants is "this Axelsen-style YouTube footage plus my DJI footage", this design
covers both and no shot-boundary work is needed. If the owner genuinely intends TV-broadcast
footage with cuts and replays, that is untested here and needs a real sample plus a
shot-boundary component. **Proposed default: scope B11 to the two footage classes actually on
disk; treat true multi-camera broadcast as a named future workstream triggered by a real
sample.**

**B. Should TrackNetV3 be measured on a short 4K fixed-camera segment before B11 is
implemented?** R2 makes this the highest-information cheap experiment available: it decides
whether the shuttle signal is viable at all on the owner's own footage, and therefore whether
the swing fallback is a fallback or the primary mechanism. **Recommended: yes** — a short
(~10-20s) DJI segment through the dense pre-pass only, before committing to §5's
signal-selection logic.

**C. When no usable rally signal exists, is "zero rallies, stated reason" acceptable?**
This design refuses to emit one whole-video rally as a fallback (done-means 7), which means a
run on footage with no usable signal produces no rallies and therefore no per-rally stroke
labels. That is honest and consistent with how BST and TrackNetV3 shipped, but it is a
visible "nothing here" in the UI and the owner should confirm it rather than meet it at demo
time. (This is the §6a-C "strict full-rally coverage" decision meeting the detection limit
that decision itself identified as the remaining constraint.)

**D. Should R10 of the completion bar be amended?** Its prediction that threshold
recalibration is "unlikely to close a 0.66%->acceptable gap" is contradicted by §0.6 on this
footage, and its stated failure modes (cuts, overlays, zooms, replays) are absent from the
file (§0.5). Proposal: amend R10 to record that the gate is a self-calibration problem while
rally segmentation is the genuinely-unscoped part, and add the §0.7 shuttle-detector blocker
as a new risk. That is a spec amendment for the owner to approve, not something to change
silently.

**E. Does B11 own diagnosing `yolo11s-ball` on 4K footage, or does that become its own
item?** §0.7 shows it is effectively non-functional on the owner's footage and that 86% of
its output is a static artifact. B11's proposed scope only *detects and routes around* this.
Fixing the detector is arguably higher-value than anything else in Sub-project B for the
owner's own footage. **Proposed default: out of scope for B11, raised as its own named item.**

---

## 13. Global constraints

- **Cost-aware, not CPU-only** (corrected 2026-08-01, §0.10). This machine has an RTX 4090
  and a working CUDA torch install; an earlier version of this constraint falsely claimed
  otherwise. No per-frame learned classifier is adopted in this design, but on the
  footage-content grounds in §3 finding 1, not a hardware limit. Every proposed per-frame
  cost is still measured, not estimated (§0.9, §6, §0.10).
- **Never-fatal.** Neither the gate's calibration nor the segmenter may crash the pipeline;
  every failure path leaves the match video and outputs intact (§7).
- **Zero regression when pinned.** With `--court-view-threshold 0.75` and the signal forced,
  behaviour matches today's.
- **Honest reporting.** Coverage, chosen signal, calibration, and suppressed artifacts are
  recorded in `metadata.json` / `rally_segments.json`, not inferred by the reader.
- **Never commit** model weights, datasets, or the pre-existing local dirt.
- **Windows env**: `.venv/Scripts/python.exe` with `PYTHONUTF8=1`; kill background processes
  by PID.
- **No writes to `main`**; work proceeds on `claude/match-stroke-recognition-b` under a
  scoped claim.
