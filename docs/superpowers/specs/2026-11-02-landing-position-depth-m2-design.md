# M2 · Landing position and depth

Date: 2026-11-02. Roadmap: `docs/superpowers/research/2026-11-02-oss-landscape-roadmap.md` section M2.
Branch: `claude/m2-landing-position-depth-80c49d`.

## 1. Goal

Every shot gets a **landing point in court metres** with its source, and a **depth band** (front / mid /
rear of the receiving half). The strategy facts pack gains depth facts, and the match details page shows a
shot-landing map. This replaces the telemetry spec's "pull zones only" stand-in (2026-10-19 section 1,
"Shot-landing on the ground ... pull zones replace it").

Done means (this PR):
- Landing-band agreement with ShuttleSet `landing_x/y` is measured per video on the local ShuttleSet rallies
  and beats the measured baseline (today's `sent_to`, the receiver's feet); `scripts/score_landing.py` writes
  the per-video report.
- `strategy/shots.py` shot rows carry `landing`; `sent_to` is the landing's zone when there is one.
- Facts `<p>.depth.*` exist (estimates, `validated: false`), the report text and LLM prompt know them.
- The match details page has a Shot landing card.

Not in this PR (section 9): DJI ground truth, ShuttleSet `landing_area` codes, a learned landing model,
3D trajectory (M9).

## 2. What "landing" means

ShuttleSet annotates `landing_x/y` for every shot. For a non-terminal shot it equals the next shot's
`hit_x/y` (checked on the CSVs: row k's landing is row k+1's hit point), and it is a **ground** point: a long
serve's landing sits on the far baseline in the image, not up in the air where the shuttle is at contact.
So for a mid-rally shot "landing" = the ground point under the receiver's contact. For the rally's last
shot it is where the shuttle hit the floor.

## 3. Method (measured before choosing)

Prototype on the 5 accepted local ShuttleSet matches (`data/hit_dataset/*/*.npz`: our pipeline's own
TrackNetV3 shuttle and RTMPose keypoints per rally, ShuttleSet court corners), ground truth projected through
the ShuttleSet corners. 2,883 non-terminal shots with a usable receiver foot point:

| Estimate | Median error | Depth band agreement |
|---|---|---|
| Shuttle image point at the next hit, through the ground homography | 6.56 m | 0.398 |
| Receiver's feet (today's `sent_to`) | 1.31 m | 0.660 |
| Feet + net-ward reach offset (leave-one-match-out) | 1.14 m | 0.716 |
| **Depth from feet, width from the shuttle's image column, + reach offset** (LOMO) | **0.76 m** | **0.716** |

- The shuttle is mid-air at contact, so projecting it through the ground homography is useless (bias 6-7 m).
- The receiver's feet are a ground point but behind the contact: the median gap is 0.28 m on the near side
  and 1.02 m on the far side, always towards the net. A symmetric offset (mean of both sides, 0.62 m) is as
  good as per-side offsets in leave-one-match-out and is the physical reading (contact is in front of the
  body), so it is used: `REACH_M = 0.6`. Per-side offsets would mostly encode the broadcast camera. An
  in-sample sweep of 0.4-0.9 m moves depth agreement only from 0.706 to 0.735; the larger values win on
  the far-side gap, which is the part most likely tied to the broadcast camera, so 0.6 stays.
- Width: the court point at the feet's depth whose image x equals the shuttle's image x (assumes image
  columns are near vertical in the world, true for a camera behind the baseline). It cuts the lateral error
  (median 1.01 m vs 1.31 m before the reach offset).

So, for shot k with an intact link to shot k+1 (other side, gap <= 2.5 s, as `sent_to` today):
1. `foot` = receiver's court position at shot k+1's frame (`detections.jsonl`, within 8 frames, positions
   check passed). None: no landing.
2. `shuttle` = shuttle image point within 3 frames of that frame (`shuttle_trajectory.json`).
3. With a shuttle and a usable homography (`metadata.court.corners`): `x` from the shuttle column at
   `foot.y`, kept only when it falls within 1 m outside the court width, else `foot.x`. Source `shuttle_feet`.
   Otherwise `x = foot.x`, source `feet`.
4. `y = foot.y` moved `REACH_M` towards the net, never across it. A returned shot did not land out, so the
   point is clamped into the court on the receiver's half, and `side` is the receiver's (a point on the net
   line would otherwise read as the lower half).

Terminal shots (last of the rally): outcome E2's floor landing: the cleaned track after the hit, its first
still run, projected (`outcomes/evidence.floor_point`, now shared with `_Landing.verdict`). Source `floor`.
On broadcast footage this finds nothing (0 of 393: the clip ends 1.5 s after the hit and replays cut in), so
it is not validated. Two filters drop what is not a landing: a point more than 1.5 m outside the court
(`FLOOR_SLACK_M`), and a point that recurs within 0.2 m in 3 or more rallies of one run (a still object the
track snaps to). On the DJI runs nearly every raw floor point was such an object, one fixed spot per video
7-17 m behind the near baseline (found in review), so terminal landings are rare there (0-3 per run).
The same object fed outcome E2's "outside" verdict, so E2 now applies both filters too (section 3.1).

### 3.1 Outcome E2 uses the same filters

`outcomes/evidence.py` holds `floor_plausible` and `repeated` (used by `shots.py` and `outcomes/build.py`).
`build._auto` first takes every segment's floor point, drops the implausible and repeated ones, then judges.
Measured with `python scripts/score_rally_outcomes.py outputs/DJI_20261024090907_0003_D
outputs/DJI_20261024091506_0004_D` (owner labels, 49 points):

| | E2 named a winner | E2 right | Auto claims | Claims right | Coverage |
|---|---|---|---|---|---|
| Before | 21 | 10 (0.476) | 4 | 3 (0.75) | 0.082 |
| After | 1 | 0 | 0 | - | 0.000 |

Before, E2 voted "lower won" every time it fired (0003: 12 of 13, 0004: 20 of 20), always from one spot
about 10 m behind the near baseline (0003 (3.5, 23.2), 0004 (4.3, 23.7)). The 3 right claims were that
constant vote meeting the lower side's 14-of-20 win rate on 0003, not evidence. Precision after the change
cannot be measured (no claims), so this does not strictly meet "precision does not drop"; it is kept because
the claims it removes rested on no real landing. Existing `rally_outcomes.json` files change only when
rebuilt (`python -m badminton_analysis.outcomes.build outputs/<stem>`).

## 4. Data contract

Each `build_shots` row gains:
```
"landing": {"x": 3.1, "y": 2.4, "side": "upper", "depth": "rear", "source": "shuttle_feet"} | null
```
`side` is the half it landed in, `depth` from `zone_of`. For `floor` landings outside the court, `depth` is
null and `out: true`. `sent_to` = `zone_of(landing.side, x, y)` when there is an in-court landing on the
receiver's side, else as before (feet zone). Doubles: the receiver is the next hit's actor (player id) as
today; without a known actor there is no foot point and no landing.

Depth bands are `zone_of`'s (front <= 1.98 m from the net = short service line, rear > 4.7 m), the same
bands the shot table already uses, so zones and landings agree.

## 5. Facts

`shots.depth_stats(table, side)`: the side's non-serve shots whose landing is in court on the other half.
Facts per player (`team.<p>` in doubles), all `validated: false` (validated on broadcast, not DJI), with
`insufficient` / `few_attempts` below `MIN_DEPTH = 20` landings (same rule as `hand.*`):

| Fact | Value |
|---|---|
| `<p>.depth.n` | landings counted |
| `<p>.depth.front_share` / `mid_share` / `rear_share` | share of those landings per band |
| `<p>.depth.lift_clear_rear_share` | lifts + clears that reached the rear band (n = lifts + clears) |
| `<p>.depth.drop_net_front_share` | drops + net-group shots that stayed in the front band |

The share facts with their own denominator use it for the `insufficient` rule. `prompts.py` describes
them as estimates; `report.py` has en/zh sentences and adds `you.depth.lift_clear_rear_share` to shot
selection and `opponent.depth.rear_share` to scouting candidates.

## 6. API and UI

`GET /api/insights/video/<stem>/landings` -> `{"stem", "match_format", "landings": [{"rally", "time",
"hitter", "stroke", "group", "x", "y", "side", "depth", "source", "out"}], "bands": {side: {"n", "front",
"mid", "rear"}}}`; 404 for an unknown stem. Computed on request from `build_shots` (no new file, so older
runs need no backfill).

Match details page, under Movement (single video or part: its own landings; Whole match: every analysed
part pooled into the primary's frame, a swapped part turned 180 degrees with its sides exchanged, like the
pooled point tally): a **Shot landing** card. A full court drawn as SVG (far half on top),
one dot per landing coloured by hitter side, a hitter filter (both / each player), a hollow dot
for `feet` landings and a square for `floor` ones, and each player's front / mid / rear shares underneath.
An "estimate" badge says it is not validated on this footage. No landings: the card says why.

## 7. Validation script

`scripts/score_landing.py [--data data] [--out <dir>]` runs the shipped `landing.estimate` on every
`data/hit_dataset/<match>/*.npz` rally whose hit count matches the label CSV rally, against ShuttleSet
`landing_x/y`. Per video and overall: n, median / p80 error (m), depth band agreement and side-third
agreement for `feet` (baseline) and the shipped method, plus a leave-one-match-out check of `REACH_M`.
Writes `landing_report.json` and `landing_report.md`. Feet in the npz come from the ankle midpoint (the
pipeline's `detections.jsonl` court point is a bbox foot point; close, not identical).

### 7.1 Result (shipped code, `python scripts/score_landing.py`, 2026-11-02)

| Video | n | Feet median m | Feet depth agree | Shipped median m | Shipped depth agree | Shipped third agree |
|---|---|---|---|---|---|---|
| An Se Young v Ratchanok (Thailand Open 2021 QF) | 413 | 1.136 | 0.738 | 0.676 | 0.760 | 0.879 |
| Antonsen v Axelsen (WTF 2020 F) | 732 | 1.271 | 0.680 | 0.707 | 0.719 | 0.892 |
| Marin v An Se Young (Thailand Open 2021 SF) | 607 | 1.363 | 0.647 | 0.761 | 0.725 | 0.914 |
| Sindhu v Chochuwong (WTF 2020 QF) | 473 | 1.415 | 0.594 | 0.904 | 0.666 | 0.905 |
| Axelsen v Ng Ka Long (Thailand Open 2021 F) | 657 | 1.364 | 0.647 | 0.815 | 0.720 | 0.895 |
| **Overall** | 2882 | 1.314 | 0.660 | 0.768 | 0.718 | 0.898 |

The shipped estimate beats the baseline on every video. The sixth local video (Momota v Axelsen) is 25 fps
and was never accepted into the hit dataset, so it is not scored.

On the 13 local runs (2026-11-02, `build_shots`, after the review fixes): 0.1-1 s per match; mid-rally
landings on 0-51 shots per run (0 on 0004, where both sides fail the position check), terminal floor landings
on 0-3 rallies per run. Most missing mid-rally landings come from the far side failing the existing position
check (0003, 0004, 0010, 0014), a known loose end.

## 8. Files

- `badminton_analysis/insights/landing.py` (new): `REACH_M`, `column_x`, `estimate`, `depth_of`.
- `badminton_analysis/outcomes/evidence.py`: `load_trajectory`, `floor_point`, `floor_plausible`, `repeated`;
  `outcomes/build.py` uses them (section 3.1).
- `badminton_analysis/strategy/shots.py`: `landing` on rows, `sent_to` from it, `depth_stats`.
- `badminton_analysis/strategy/facts.py`, `prompts.py`, `report.py`: depth facts.
- `badminton_analysis/web/insights_routes.py`: the landings route.
- `static/kestrel/widgets/landing.js` (new), `static/kestrel/views/landing_model.js` (new),
  `static/kestrel/views/workspace.js`, `static/kestrel/core/api.js`, `static/kestrel/core/i18n.js`,
  `static/kestrel/widgets.css`.
- `scripts/score_landing.py` (new); `scripts/shuttleset_labels.py` keeps parsing unchanged.
- Tests: `tests/test_landing.py`, `tests/test_score_landing.py`, additions to `test_strategy_shots.py`,
  `test_strategy_facts.py`, `test_strategy_report.py`, `test_app_insights.py` (route); `floor_point` and
  `load_trajectory` are tested in `tests/test_landing.py`;
  `tests/js/landing_model.test.mjs`.

## 9. Deferred

- Why the DJI track snaps to a still object behind the baseline (likely a shuttle or marker lying there):
  suppressing it at tracking time would give E2 real floor landings back.

- DJI ground truth: needs owner-marked landings; until then depth facts stay `validated: false`.
- ShuttleSet `landing_area` codes: the area grid is not documented in the local data; the bands are derived
  from `landing_x/y` instead.
- Terminal landings on broadcast footage (clips end too early; M8 broadcast work would allow it).
- Reach offset per stroke group or a learned landing model (more data first).
