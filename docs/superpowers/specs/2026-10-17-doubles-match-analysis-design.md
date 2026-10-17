# Doubles match analysis - design

- **Date:** 2026-10-17
- **Workstream:** `doubles-match-analysis` (branch `claude/doubles-match-analysis`, from `origin/main` `71cee7b`)
- **Status:** approved by the owner in conversation (sections 1-3, 2026-10-17); this document records it.

## 1. Goal

Let a match analysis run handle a **doubles** game (4 players, 2 per side) and deliver, per
player: a match-level identity (P1-P4, teams {P1,P2} and {P3,P4}), movement statistics,
court heatmaps, and **who hit each shot** with a confidence. Singles runs stay exactly as
they are today.

## 2. Owner decisions (2026-10-17)

| # | Question | Decision |
|---|---|---|
| D1 | Deliverable for v1 | Per-player movement **plus who hit** (individual partner attribution). |
| D2 | Validation footage | Owner's fixed-camera Dji doubles clips, **no labels**. Validation is visual review of the annotated output plus reported run statistics; no accuracy claims. |
| D3 | Identity persistence | **Whole match**: the same person keeps the same ID across rallies and end changes. |
| D4 | Approach | **A - offline tracklets + constrained global 4-identity assignment** (not an online re-ID tracker, not rule slots). |
| D5 | Stroke types | Withheld on doubles runs (BST and the hit TCN are singles-trained, ShuttleSet). |
| D6 | Continuity claim | The helper could not narrow the expired `learned-hit-detection` claim (the primary checkout's `.ai` is a cloud-sync reparse point). Owner chose: proceed without a helper claim and log the deviation in the workstream file. |

Consequence of D2 + B11 decision B (no usable shuttle signal on the owner's fixed-camera
footage): within-side hitter attribution on Dji footage uses **swing evidence**; shuttle
proximity is used only when a shuttle track exists.

## 3. Scope

**In:** format switch end to end; multi-person in-loop collection; tracklets; appearance
signatures; constrained identity assignment; within-side hitter attribution; per-player and
team statistics and heatmaps; identity-labelled annotated video; Kestrel UI panel; honest
degraded / error statuses; tests.

**Out (deferred):** doubles stroke-type recognition (needs labelled data + retraining);
who-hit accuracy numbers (needs labels); broadcast doubles with camera cuts; shipping or
downloading re-ID embedding weights (the hook exists, weights are optional and local only);
an identity correction UI beyond optional display names; rally segmentation changes for
doubles (see 9, risk R3); doubles technique (biomechanics) scoring.

## 4. Format switch

- `match_format: "singles" | "doubles"`; default `"singles"`; any other value is rejected
  (CLI `choices`, API 400).
- CLI: `main.py --match-format {singles,doubles}`.
- API: `/api/analyze` payload key `match_format`, forwarded to `main.py`.
- Kestrel: a "Match format" select in the analyze config. Pre-filled to `doubles` when the
  video carries the library tag `doubles` (tags are already loaded per video); the user can
  override.
- `BadmintonAnalysisSystem(match_format=...)`; recorded in `metadata.json` as top-level
  `"match_format"`. A missing key means singles (older runs).

## 5. Architecture

Singles: the code path and every output are unchanged. Doubles adds a **parallel
collector** next to the existing `PlayerTracker`, and post-loop stages after it. The legacy
tracker keeps running in doubles (it still provides one representative per side to the
existing side-level consumers: rally swing signal, contact rule, hit signals), so no
existing consumer changes behaviour.

```
in-loop (doubles only)                       post-loop (doubles only)
detect_players + far pass                    tracklets.jsonl ──► identity.assign()
   │  all on-court people, keypoints             │                     │
   ▼                                              ▼                     ▼
DoublesCollector.observe(frame) ──► MultiTracker ─► tracklets ──► players.json (P1-P4)
   (dedupe near/far passes,          (Kalman + Hungarian,                │
    appearance sample every k)        court metres)                       ▼
                                                        hits.json (side) ─► hitter.attribute()
                                                                             │
                                                    per-player stats/heatmaps ◄┘
                                                    detect_<stem>_players.mp4 (re-render)
```

### 5.1 New modules

| Module | Purpose | Depends on |
|---|---|---|
| `badminton_analysis/tracking/multi.py` | `MultiTracker`: frame-to-frame association of N people in court coordinates into tracklets. Pure (no I/O, no cv2). | numpy, scipy |
| `badminton_analysis/tracking/appearance.py` | Appearance signature from a frame + keypoints: HSV histograms of torso and legs, standing height in metres; optional embedding hook. | cv2, numpy |
| `badminton_analysis/tracking/identity.py` | `assign_identities(tracklets)`: constrained global assignment to P1-P4 with teams and confidences. Pure. | numpy, scipy |
| `badminton_analysis/tracking/doubles.py` | `DoublesCollector`: the in-loop adapter (dedupe, feed tracker, sample appearance), and post-loop orchestration (identity, hitter, stats, outputs). Never fatal. | the three above |
| `badminton_analysis/stroke/hitter.py` | `attribute_hitters(hits, players, frames, fps, shuttle)`: within-side partner choice with confidence. Pure. | numpy |
| `badminton_analysis/visualization/doubles_render.py` | Per-player heatmaps (PNG) and the identity-labelled re-render of the video. | cv2, matplotlib |

### 5.2 Changes to existing files

- `main.py`: `--match-format` flag passed to the system.
- `app.py`: accept and validate `match_format`, forward it.
- `static/kestrel.js`: format select (tag default), doubles results panel.
- `badminton_analysis/system.py`: constructor arg; metadata field; in-loop call to the
  collector; doubles enables per-frame capture (racket detector + `_capture_analysis_frame`)
  even when technique analysis is off, so a side-level hit list exists; skips technique
  scoring and stroke recognition on doubles runs with a recorded reason; calls the
  post-loop doubles stage after hit detection.
- `badminton_analysis/visualization/player_pose.py`: no behaviour change; the collector
  reads `current_pose_data`, whose keypoint order matches the merged centroid order.

## 6. Components in detail

### 6.1 In-loop collection (`DoublesCollector.observe`)

Input per analysed court frame: merged `centroids` (whole-frame pass + far pass), the merged
`current_pose_data` keypoints (same order), the frame image, the frame index.

- **Dedupe:** the whole-frame and far passes can both see a far player. Two detections whose
  foot points are within 0.4 m in court coordinates are merged, keeping the one with more
  valid keypoints.
- **Court position:** foot point mapped by the existing `CourtMapper`; detections whose
  mapping fails are dropped.
- **Hands are stored with the person**, not in a dict keyed by image Y (fixes the existing
  same-Y collision for this path; the singles path is left alone).
- **Appearance sample:** every `APPEARANCE_EVERY = 5` analysed frames, for each detection
  with valid shoulders, hips and knees, compute the signature (6.3) from the frame. The
  frame image is not retained.
- Feeds `MultiTracker.step(frame, detections)`.

### 6.2 Tracklets (`MultiTracker`)

- State per active track: constant-velocity Kalman filter on court (x, y).
- Association: Hungarian (`scipy.optimize.linear_sum_assignment`) on court distance between
  predictions and detections; gate `GATE_M = 1.0` metre per analysed-frame step, scaled by
  `sqrt(gap)` for gaps.
- A track ends after `MAX_MISS_SEC = 0.5` without a match, or when the assignment is
  ambiguous: two active tracks within `CROSS_M = 0.6` m of each other where either is
  unmatched. Ending early is deliberate: a short tracklet costs nothing, a swap poisons
  identity.
- A track is also split when its court-y crosses the net line (6.7 m) and stays across for
  3 consecutive analysed frames (players only cross at end changes), so **every tracklet
  has exactly one side**, which the constraints in 6.4 rely on.
- Bystander rejection: tracklets shorter than `MIN_TRACKLET_SEC = 1.0` or whose median
  court position is outside the court (x not in [0, 6.1] or y not in [0, 13.4]) beyond a
  0.3 m margin are discarded before identity assignment.
- Output record per tracklet: `id`, `frames`, per-frame `court` (x, y), `image` foot point,
  `keypoints` (17x2), its single `side` (`upper` if median court-y < 6.7 else `lower`), and
  the list of appearance samples.
- Written to `tracklets.jsonl` (one tracklet per line) for debugging and re-runs.

### 6.3 Appearance signature (`appearance.py`)

- Torso region: polygon from shoulders and hips; legs region: hips to knees. Each gives an
  HSV histogram (H 16 bins x S 4 bins), L1-normalised; regions under 50 pixels are skipped.
- Standing height in metres: image distance from the ankle midpoint to the shoulder
  midpoint, converted with the court's px/m at the player's court-y (from the quad, as
  `rallies.baseline_caps` does).
- Optional embedding: if `weights/reid-resnet18.pt` exists, a 512-d torchvision ResNet18
  feature of the person box; otherwise absent. Nothing is downloaded.
- A tracklet's signature is the median of its samples (histograms re-normalised).
- Distance between signatures: `0.6 * chi2(torso) + 0.25 * chi2(legs) + 0.15 * |dh| / 0.3`,
  plus the embedding cosine distance (weight 0.5, re-normalised) when both have one.

### 6.4 Identity assignment (`identity.py`)

Input: surviving tracklets. Output: `{tracklet_id: identity}`, per-identity team, per
tracklet confidence, and a status.

1. **Coexistence graph.** Two tracklets *coexist* if they share any frame.
2. **Teams.** Two tracklets that coexist on opposite sides are on opposite teams; that is a
   cannot-link. Two tracklets that coexist on the same side are on the same team (partners
   are always on the same side) - a must-link. Solve as a 2-colouring with must-links
   merged first (union-find), then cannot-links between components. Components not
   connected by any constraint are placed by appearance: assigned to the team whose
   signature (median of member tracklets) is closer. A conflict (odd cycle) is resolved by
   dropping the shortest offending tracklet; each drop is counted.
3. **Partners within a team.** Same-team tracklets that coexist are different partners: a
   cannot-link 2-colouring per team. Unconstrained components are placed by appearance +
   height distance to each partner's current signature, longest component first.
4. **Confidence** per tracklet: 1.0 when its placement is forced by a constraint chain;
   otherwise `d_other / (d_own + d_other)` mapped to [0, 1] as `2 * x - 1`, clipped.
5. **Labels:** team containing the identity seen first on the lower side is team 1 =
   {P1, P2}; within a team the lower-numbered identity is the one with more total
   frames.
6. **Status:**
   - `ok` - 4 identities each covering at least 20% of the analysed frames.
   - `degraded` - fewer than 4 identities meet that bar (e.g. the far back-court is not
     visible, or bystanders dominate). Identities are still reported with their coverage.
   - `degraded_teams` - median team-placement confidence among unforced components below
     0.3: teams cannot be told apart by appearance; identities are reported per side only
     (team field null). The median is weighted by each component's frame count
     (amended 2026-10-18): unweighted, a few short orphan tracklets placed with
     confidence 0 could outvote long, confidently placed components and flip a whole run.
   - `error` - the stage raised; the message is recorded and every earlier output stands.

### 6.5 Who hit (`hitter.py`)

Input: the run's side-level hit list (learned or contact rule, as written to `hits.json`
today), the identity tracks, fps, and the shuttle track if any: the dense TrackNet track when it ran, otherwise the per-frame
YOLO shuttle points (amended 2026-10-18).

For each hit with side `s` at frame `f`, candidates = identities on side `s` at `f`
(within +/- 2 frames).

- 0 candidates (or the hit has no side): `hitter_id` null, evidence `none`.
- 1 candidate: that identity, confidence 0.5 (the partner may simply be undetected).
- 2 or more candidates (more than 2 only when identity is degraded): score every candidate;
  the best wins and the confidence is the margin between the best two.
  - **Shuttle available** within +/- 2 frames of `f`: score = `-min(distance(shuttle, wrist))`
    over both wrists, in metres (pixels / px-per-m at the player's row).
  - **Otherwise swing evidence** in `[f - 0.3 s, f + 0.3 s]`: peak wrist speed in m/s / 10
    (capped at 2), plus 0.5 if a wrist rises above its shoulder within the window.
  - Confidence = `softmax` margin `|p1 - p2|` over the best two scores (temperature 1),
    i.e. `tanh(|s1 - s2| / 2)`.
  - *As built (2026-10-17):* the racket-box terms of the original design are dropped in v1:
    the pipeline stores racket points per side, not per person, so they cannot be scored per
    partner. The "faster wrist" qualifier became "a wrist", which is less sensitive to pose
    jitter.
- For two-candidate scoring, confidence below `HITTER_MIN_CONF = 0.6` gives `hitter_id`
  null; the side is kept. The single-candidate case is named with confidence 0.5 and
  evidence `single_candidate`, so a consumer can tell it apart and discount it.

`hits.json` keeps every existing field (`hitter` stays the side) and gains, on doubles runs
only, per hit: `hitter_id` (e.g. `"P3"` or null), `hitter_team` (1, 2 or null),
`hitter_confidence`, `hitter_evidence` (`"shuttle"` | `"swing"` | `"single_candidate"` |
`"none"`), plus top-level `"hitter_attribution": {"validated": false}`.

### 6.6 Outputs

`players.json` (doubles only):

```json
{
  "schema_version": "1.0",
  "match_format": "doubles",
  "status": "ok",
  "reason": null,
  "players": [
    {"id": "P1", "team": 1, "name": null, "frames": 0, "coverage": 0.0,
     "distance_m": 0.0, "avg_speed_mps": 0.0, "max_speed_mps": 0.0,
     "hits": 0, "hits_named": 0,
     "heatmap": "heatmap_P1.png",
     "tracklets": [{"id": 3, "start_frame": 0, "end_frame": 0, "confidence": 1.0}]}
  ],
  "teams": [{"id": 1, "players": ["P1", "P2"], "coverage_heatmap": "heatmap_team1.png"}],
  "stats": {"tracklets_total": 0, "tracklets_used": 0, "tracklets_dropped": 0,
            "median_confidence": 0.0, "hits_total": 0, "hits_named_share": 0.0},
  "validated": false
}
```

- Speeds use the same 8 m/s cap and 0.05 m jitter floor as `PlayerTracker`.
- Per-player heatmaps and team heatmaps as PNGs in the run directory.
- `detect_<stem>_players.mp4`: a post-loop re-render of the source video drawing each
  identity's foot point, label and team colour (4 fixed colours), using `tracklets.jsonl`
  and the assignment. No inference; decode + draw + encode. Skipped with a recorded reason
  if it fails.
- Optional names: `outputs/<stem>/player_names.json` (`{"P1": "Alice"}`) is read when
  present and copied into `players.json`; Kestrel can write it via the existing output
  file route.

## 7. Stroke labels, technique scoring, rallies on doubles

- `_run_stroke_recognition` and `_run_technique_analysis` are skipped on doubles runs. The
  console and `players.json` record `"stroke_labels_withheld": "doubles: BST is a singles
  model"`; any previous run's `strokes.json` / `strokes.jsonl` / `technique_summary.json`
  are removed (same stale-output rule as today).
- Hit detection: the learned model (if configured) and the contact rule still run, side
  level only; their output feeds 6.5.
- Rally segmentation: unchanged (it uses the legacy tracker's per-side representative).

## 8. Kestrel UI

- Analyze config: "Match format" select (Singles / Doubles); defaults from the `doubles`
  tag.
- Results, when `players.json` exists: a "Players" panel with a row per player (name or
  ID, team colour, distance, average / max speed, hits, named-hits share), team and
  per-player heatmaps, an **"Who hit: unvalidated"** badge, and the identity status with
  its reason when not `ok`. A "Stroke types withheld for doubles" note replaces the stroke
  distribution.
- Text goes through `escHTML`. Optional inline name editing writes `player_names.json`.

## 9. Error handling and risks

- Every doubles stage is wrapped: an exception records `status: "error"` and a reason in
  `players.json` (or, if that cannot be written, the console), and never aborts the run.
- **R1 Matching kits.** Partners in identical kits separate only by height, constraint
  chains and the optional embedding. Expect lower confidence; reported, not hidden.
- **R2 Far back-court visibility.** If the camera cannot see the far back-court partner,
  identity degrades to fewer than 4 and says so.
- **R3 Rally segmentation in doubles** uses one representative per side; the per-side
  representative alternates between partners, and swaps are zeroed as teleports. Rally
  recall on doubles is unmeasured; observed on the owner's clip and reported.
- **R4 Swing-based who-hit** cannot be measured without labels; the UI badge and
  `"validated": false` say so.
- **R5 Cost.** The re-render adds one decode + encode pass; appearance sampling every 5th
  frame. Recorded in the verification notes.

## 10. Testing

TDD, hermetic, synthetic:

- `tests/test_doubles_multi_tracker.py` - crossing partners, occlusion gap, a 5th static
  person, end change; no ID swap inside any tracklet; tracklets cut at ambiguous
  crossings; bystander rejection.
- `tests/test_doubles_appearance.py` - histogram regions from synthetic images and
  keypoints; height in metres; distance properties.
- `tests/test_doubles_identity.py` - distinct kits; matching kits separable only by height
  and constraints; end change; forced `degraded` and `degraded_teams`; exact P1-P4
  recovery up to the defined labelling rule.
- `tests/test_doubles_hitter.py` - shuttle case, swing case, single candidate, none, low
  confidence gives null id with side kept.
- `tests/test_doubles_collector.py` - dedupe of near/far detections, per-person hands,
  never-fatal behaviour, `players.json` / `hits.json` fields.
- `tests/test_doubles_format_switch.py` - CLI and API validation and forwarding; metadata
  field; singles run writes no doubles outputs and its `hits.json` has no doubles fields.
- Full existing suite stays green (singles regression guard).

## 11. Done means

1. Doubles selectable via CLI, API and Kestrel; defaulted from the `doubles` tag; recorded
   in `metadata.json`.
2. A doubles run writes `tracklets.jsonl`, `players.json` (4 identities or an honest
   `degraded` / `degraded_teams` / `error` status), per-player and team heatmaps,
   `detect_<stem>_players.mp4`, and `hits.json` with per-hit `hitter_id` /
   `hitter_confidence`.
3. Stroke types and technique scoring withheld on doubles, stated in the UI.
4. Singles behaviour and outputs unchanged; the full test suite passes.
5. Owner-run check on one Dji doubles clip: visual review of the identity video; the run's
   reported statistics (status, tracklet counts, confidence distribution, named-hit share)
   recorded in the workstream file, with no accuracy claims.
