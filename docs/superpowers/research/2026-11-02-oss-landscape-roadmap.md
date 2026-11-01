# Open-source badminton AI: what Kestrel can borrow, and a roadmap to "complete"

Date: 2026-11-02. Method: the deep-research workflow (5 search angles, 21 sources fetched, 25 claims
put through a 3-vote adversarial check: 21 confirmed, 4 refuted). Each finding was then checked against
this repository. Accuracy numbers are the authors' own, on broadcast footage, with metrics that differ
from Kestrel's DJI numbers. Read them as upper bounds, not as targets.

## 1. Three corrections the research needed from the repo

The research only saw Kestrel's feature list, not its code or data. Three of its top suggestions are
already done here, or already have their inputs on disk:

1. **BST was already tried and replaced.** `third_party/bst/` and `weights/bst-shuttleset.pt` are still
   in the repo, but the unified stroke classifier (spec `2026-10-22-unified-stroke-classifier-design.md`)
   replaced BST because BST is trained on broadcast footage (risk R7). BST's CVPRW 2026 result
   (0.832 vs 0.824 on 25-class ShuttleSet) does not justify re-adopting it. **Drop.**
2. **InpaintNet and the median background are already on.** `weights/inpaintnet.pt` is installed and
   loads when present (`app.py:_inpaintnet_weights`, `shuttle_track/tracknet.py`). The median image is
   sampled within a memory budget. What is still missing is a **measurement** of TrackNetV3 recall on DJI
   footage. The quoted 99.33 % is on the authors' dataset.
3. **The labels for forehand/backhand, landing and score are already on disk.** Every ShuttleSet CSV in
   `data/shuttleset/labels/` (46 folders, 6 videos local) has these columns: `backhand`, `aroundhead`,
   `hit_height`, `landing_x/y`, `landing_area`, `landing_height`, `roundscore_A/B`, `getpoint_player`,
   `win_reason`, `lose_reason`, `player_location_x/y`, `opponent_location_x/y`. Kestrel's classifier uses
   only `type`. This is the cheapest gap-closer the research found.

## 2. Repos and papers surveyed

| Source | What it is | Licence | Reported numbers | Verdict for Kestrel |
|---|---|---|---|---|
| [qaz812345/TrackNetV3](https://github.com/qaz812345/TrackNetV3) | Shuttle tracker: median background + InpaintNet rectification | MIT | 97.5 % acc, 99.33 % recall, F1 98.56 | Already vendored. Measure it on DJI. |
| [Va6lue/BST](https://github.com/Va6lue/BST-Badminton-Stroke-type-Transformer) ([paper](https://arxiv.org/html/2502.21085v3)) | Pose + shuttle + court-position stroke transformer, CVPRW 2026 | MIT | 0.832 acc / 0.810 macro-F1 (25 classes) | Already tried and replaced. Drop. |
| [jean50621/Badminton_Challenge](https://github.com/jean50621/Badminton_Challenge) ([paper](https://ar5iv.labs.arxiv.org/html/2308.12645)) | NTNU, CoachAI 2023 Golden Award: 11 stages (hit, hitter, backhand, round-head, height, landing, positions, ball type, winner) | CC0 | 0.78 aggregate only; no per-task numbers | Best blueprint for per-hit attributes. Borrow the recipes. |
| [ShuttleSet22](https://arxiv.org/html/2306.15664v3) | 58 BWF 2022 matches, 33,612 strokes, FH/BH, around-head, destination labels | Dataset licence not stated | — | Extra pro labels once ShuttleSet is exhausted. |
| [ShuttleSet / CoachAI-Projects](https://github.com/wywyWang/CoachAI-Projects) ([paper](https://arxiv.org/abs/2306.04948)) | 44 matches, 36,492 strokes; ShuttleNet stroke forecasting, DyMF movement forecasting, RallyNet | MIT repo; data cite-only | — | Already used. Its unused columns and forecasting models are the win. |
| [MonoTrack](https://arxiv.org/abs/2204.01899) | Monocular 3D shuttle trajectory; HitNet + trajectory optimisation | Code/licence unverified | 3D error 8.0 cm (synthetic); hits R 94.3 / P 94.9 | Reference for the 3D and hit-precision work. |
| [TrackNetV4](https://time.griffith.edu.au/paper-sites/tracknet-v4) | Motion-attention add-on to TrackNet | No code released | +0.4-0.8 F1 over V3 | Low priority. Reimplement only if tracking proves the bottleneck. |
| [arXiv 2308.12199](https://arxiv.org/pdf/2308.12199) | Broadcast analytics: ResNet-18 rally/non-rally filter, YOLOv3 players, distance covered | Paper | Rally filter F1 97.23; distance error ~4.5 m vs wearables (25 rallies) | Recipe for the broadcast gap; baseline for validating distance. |
| [Chien & Yu, arXiv 2307.16000](https://arxiv.org/abs/2307.16000) | 4-step hit pipeline with rally trimming | Paper | 99 % shot-angle recognition | Rally-trim idea for broadcast. |
| [tw-yuhsi/...hitting-event-detection](https://github.com/tw-yuhsi/a-new-perspective-for-shuttlecock-hitting-event-detection) | SwingNet (MobileNetV2 + BiLSTM) hit detector, AICUP 2023 rank 11 | None stated | No hit P/R reported | Idea only; code not cleared for reuse. |
| [nostang/AI-Motion-Platform](https://github.com/nostang/AI-Motion-Platform) * | Rule-based footwork scoring on 5 dimensions; every score carries evidence and rule IDs | MIT | Unvalidated | Borrow the dimension list and traceable-rule pattern. |
| [Chelsea0927/CourtPilot](https://github.com/Chelsea0927/CourtPilot) * | Footwork state machine WAITING → READY → MOVING → ARRIVED → RETURNING | MIT | Unvalidated | Borrow the state machine for recovery-time metrics. |
| [setuparimi/ai-badminton-analysis](https://github.com/setuparimi/ai-badminton-analysis) * | Doubles: scoreboard OCR, clothing-based identity, p-value stats | **AGPL-3.0** | Identity errors 1-10 %; 37/40 plays found | Ideas only; do not copy code. The p-value reporting is a good habit to copy. |
| [benny10ben/CourtSenseAI](https://github.com/benny10ben/CourtSenseAI) * | TrackNetV3 + YOLOv8-Pose, manual court corners, Gemini coaching | None | None | Behind Kestrel; nothing to take. |
| [yo-WASSUP/Good-Badminton](https://github.com/yo-WASSUP/Good-Badminton/wiki) * | RTMPose/RTMO/YOLO-Pose tiers, YOLO ball detector, line-model court detection | Apache-2.0 | Speed only | Looks like Kestrel's ancestor; Kestrel is ahead on every axis. |

\* Fetched and summarised but not put through the 3-vote check (dropped by the workflow's budget).
Treat these as leads.

**Not covered by any source.** Per-game score split, 7-technique scoring calibrated on pro footage,
doubles tactics (every surveyed system is singles only), and a cited-fact LLM report. Kestrel's
local cited-fact report is already ahead of the two LLM-coaching repos, which use cloud Gemini or
Claude with no fact grounding.

## 3. Roadmap

Dependency order: you can't measure without labels, and per-hit attributes need good hits. Landing feeds
depth, and depth feeds technique scores. Doubles and broadcast run as side tracks.

```
M0 Measurement base ──┬─> M1 Per-hit attributes ─┐
                      ├─> M2 Landing & depth ────┼─> M5 Footwork ─> M6 7-technique scores
                      └─> M3 Hit precision ──────┘        ^
                          M4 Game/score split ────────────┘
Side tracks: M7 Doubles tactics · M8 Broadcast footage      Stretch: M9 3D trajectory · M10 Tracker refresh
```

### M0 · Measurement base (blocks every "is it better?" claim)
Today the rally models can't be measured: there's 1 labelled match, and leave-one-match-out needs at
least 2. The stroke bar has no labels yet either.
- [ ] Owner: label points on a second match (0009 or 0010), and set `one_game_30` plus check the sides on 0003.
- [ ] Owner: label strokes on 0003/0004, with one match held out as test. Add 1-2 test videos so every group has ≥ 20 hits.
- [ ] Measure TrackNetV3 recall/precision on 2-3 owner-checked DJI rallies (frame-level). This tells us whether tracking or hit logic is the bottleneck.
- [ ] One `scripts/benchmark_dji.py` that runs the existing scorers (hits, strokes, rally outcomes) on the labelled set and writes one table, so every later milestone reports against the same numbers.

Done means: hit, stroke, outcome and tracking numbers on ≥ 2 labelled DJI matches, in one report.

### M1 · Per-hit attributes from ShuttleSet columns already on disk
- [ ] Extend the ShuttleSet loader (`scripts/train_stroke_classifier.py:load_shuttleset`) to carry `backhand`, `aroundhead`, `hit_height`.
- [ ] Add forehand/backhand, around-head and above/below-net heads to the stroke model. Inputs are pose + shuttle around the hit, as today. Note: backhand is 51-91 % of some types (push/rush, defensive, short serve), so a shot-type prior is not enough.
- [ ] If the pose-based head misses the bar on DJI, fall back to NTNU's recipe: an X3D-M clip classifier on the hitter crop, no pose (CC0). NTNU dropped pose because players were blurry.
- [ ] Add a FH/BH toggle to the owner stroke labeller and `owner_labels.py` overlay, and include it in the Retrain keep-if-better gate.
- [ ] Surface it in the UI and the strategy facts pack (e.g. "backhand rate under pressure", "around-head share in rear court").

Done means: FH/BH accuracy on owner DJI test shots ≥ an owner-set bar (suggested 0.85), and the Retrain gate rejects regressions.

### M2 · Landing position and depth
Today the telemetry spec defers ground landing because the track is image-space and mid-air, and
the outcome E2 evidence is noisy near rally ends.
- [ ] Mid-rally shots: take the shot's destination as the receiver's hit point (ShuttleSet `landing_x/y` for non-terminal shots is effectively this). Project the shuttle at the next hit frame through the court homography, falling back to the receiver's foot position.
- [ ] Terminal shots: reuse outcome E2 landing evidence, and project the last hit frame position (NTNU recipe d).
- [ ] Validate on the 6 local ShuttleSet videos against `landing_x/y` / `landing_area` (alignment already exists under `data/shuttleset/alignment`).
- [ ] Add depth bands (front / mid / rear, per side) to the facts pack and a shot-landing map on the match details page (this replaces the deferred "pull zones only" stand-in).

Done means: landing-area agreement with ShuttleSet ≥ a measured baseline, with a per-video report; depth facts cited in the strategy report.

### M3 · Hit detector precision 0.67 → ≥ 0.80 at recall ≥ 0.80
Runs after the dead-time-gaps retrain already in flight on `claude/learned-hit-detection`.
- [ ] Trajectory-consistency post-process (MonoTrack idea): fit piecewise ballistic segments between candidate hits, and drop candidates that don't start a new segment. MonoTrack reports that optimisation took recall from 78.1 % to 94.3 % at about 95 % precision on its data.
- [ ] Second opinion: a sliding-window clip classifier (A hits / B hits / none, then smoothing), NTNU recipe (a). Ensemble it with the current model under a keep-if-better gate.
- [ ] Feed owner "not a hit" and added shots into hit training (already an owner to-do, 2026-10-28).

Done means: P ≥ 0.80 and R ≥ 0.80 on the held-out Axelsen stretches and one DJI match.

### M4 · Per-game score split and score sequence (Kestrel-original)
- [ ] Split rallies into games from the match `scoring` format plus the decoded score sequence (end-of-game rules for `bwf_21x3` / `one_game_30`).
- [ ] Use ShuttleSet `roundscore_A/B` + `getpoint_player` as pro score sequences to test the planned HMM score decoder before DJI labels exist.
- [ ] Show the game split on the match details page and add per-game facts (e.g. "lost 6 straight from 15-15 in game 2").

Done means: game boundaries are correct on every labelled match, and the decoder is measured on ShuttleSet sequences.

### M5 · Footwork and movement efficiency
`insights/movement.py` already does heatmaps, recovery to base and pull zones.
- [ ] Distance covered, peak and mean speed, and shots per metre, per player and rally. Validate against a target better than the ~4.5 m per-rally error in arXiv 2308.12199.
- [ ] Per-shot recovery state machine (CourtPilot, MIT pattern): hit → move → arrive → return-to-base, giving reaction and recovery times.
- [ ] Footwork dimensions with evidence and rule IDs (AI-Motion-Platform pattern): completion, recovery speed, direction coverage, stability. These match the strategy report's cite-a-fact rule.
- [ ] Pro reference distributions from ShuttleSet `player_location_*` / `opponent_location_*`.

Done means: movement facts per player, with each number traceable to a rule and frames, and pro percentiles shown next to them.

### M6 · Per-player 7-technique scores (the stated project goal)
Needs M1, M2, M3 and M5.
- [ ] Pin the 7 techniques and the measurable signals behind each one (shot mix, FH/BH, landing depth, rally role, movement, posture).
- [ ] Calibrate ranges on pro data: ShuttleSet distributions first, then owner-chosen pro footage. Store calibration versions like the model store does.
- [ ] Each score shows its evidence (facts, frames) and abstains when coverage is thin, like the B11 "degraded" rule.
- [ ] Add a Players page view, with trends across matches via split-match linking and the athlete library.

Done means: all 7 scores on ≥ 2 owner matches, each with cited evidence; the owner agrees with the ranking on a blind check.

### M7 · Doubles tactics (side track; no external prior art)
- [ ] Formation detection from 4 court positions per frame: front-back (attack) vs side-by-side (defence), plus rotation timing.
- [ ] Doubles facts in the strategy report: serve/receive patterns, formation at point loss, middle-gap shots.
- [ ] Run one live doubles strategy report (currently untested) and a doubles stroke relabel (owner to-do).
- [ ] Report findings with significance (n, p) as setuparimi does. Borrow the habit, not the AGPL code.

### M8 · Broadcast footage (side track)
- [ ] Wire `stroke/shot_boundary.py` in. Add a ResNet-18 rally/non-rally frame filter (F1 97.23 in 2308.12199) to drop replays and close-ups, and split rallies on non-rally gaps ≥ 3 s.
- [ ] This lets ShuttleSet videos run end-to-end as a regression set for M1/M2/M4.

### M9 · 3D trajectory (stretch)
- [ ] First check whether MonoTrack code and weights are public, and under what licence (open question).
- [ ] Monocular 3D fit with court + physics priors, giving net clearance, true depth and smash speed. There is no real-world 3D ground truth anywhere; validate by reprojection error and against known court lines.

### M10 · Tracker refresh (only if M0 shows tracking is the bottleneck)
- [ ] Evaluate TrackNetV5 (not yet assessed) and a reimplemented TrackNetV4 motion prompt (+0.4-0.8 F1, no code) on the M0 DJI set.

## 4. Not adopting

- BST stroke model: tried, replaced (broadcast domain).
- YOLO ball detector (`yolo11s-ball.pt`, the upstream approach): TrackNetV3 is stronger.
- Cloud LLM coaching (Gemini/Claude with frames): the local, fact-cited report is better and keeps footage local.
- Manual 4-corner court calibration as the default: Kestrel detects the court automatically.
- Any AGPL code (setuparimi, Ultralytics-based pipelines) copied into the repo.

## 5. Open questions

1. MonoTrack code and weights licence (decides whether M9 is adoption or reimplementation).
2. ShuttleSet / ShuttleSet22 data licence for training models we ship. Both say "cite the paper" and state no licence.
3. What is TrackNetV3 recall on DJI footage? (M0)
4. Which 7 techniques exactly, and which pro footage the owner trusts for calibration (M6).
