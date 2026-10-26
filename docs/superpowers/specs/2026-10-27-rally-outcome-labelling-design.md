# Rally outcome labelling - design

Status: approved by the owner in conversation on 2026-10-27, section by section (data contract, automatic
labeller and pipeline, Kestrel UI and API). The owner approved the rest of the flow up front: write the plan,
implement, test and merge the PR.

## 1. Request and why

A parallel session is building an LLM strategy report per match. Research says rally-ending shots and
outcomes (winner, forced error, unforced error) are the most important match indicators, but the pipeline
does not record who won each rally. This project adds that record: for every rally, who won the point
(`upper` / `lower` side; for doubles the team on that side) and, where known, how it ended.

## 2. Findings that shaped the design (read-only probes, 2026-10-27)

On the owner's DJI singles footage (the main use):

- Every DJI run has `rally_segments.json` `signal: swing`, which over-counts. Match 0009 + 0010 (one game,
  30-13, split over two videos) has 58 segments for 43 real points. **Segments are not points**, so a
  label must be able to say "this segment is not a point end", and points no segment covers must be addable.
- `hits.json` hitter attribution is skewed on this footage: 0009 has 195 `lower` hits vs 66 `upper`, and 132
  consecutive same-side hit pairs vs 92 alternations. "Last hitter wins/loses" and "next server = winner"
  both name the wrong overall winner on 0009 + 0010.
- `shuttle_trajectory.json` near rally ends is dominated by a static artefact and large jumps, so a landing
  point needs a cleaning step.
- Upper-player court positions are sometimes off court (x ~ 6.7 m, y ~ 5.3 m: near the net post), so
  serve-box parity from positions works on only some rallies.

Consequence (owner decision): the owner labels points in the Kestrel UI (these labels are also the accuracy
ground truth), and a cautious rule-based automatic labeller fills in what it can, claiming nothing it cannot
support. A score-sequence decoder (HMM) is a later project, once the labels show which evidence works.

## 3. Goal, done-means and non-goals

Goal: every analysed match gets `outputs/<stem>/rally_outcomes.json`, which says who won each point and,
when known, how it ended. The source is the owner's label where one exists, else the automatic labeller,
else unknown.

Done means:

1. The owner can label each segment (near won / far won / not a point end / unsure, plus an optional
   outcome type) and add a point end at the current video time, on the match details page.
2. `rally_outcomes.json` (section 5) is written at the end of every match analysis and rebuilt on every label
   save; a CLI backfills existing runs.
3. `insights` has an envelope reader for it.
4. Tests (pytest + node) cover the store, re-attachment, evidence, rules, reconciliation, reader, routes and
   the UI model.
5. Accuracy of the automatic labeller is measured against owner labels (at least 0009 + 0010) with
   `scripts/score_rally_outcomes.py` and reported honestly, even if coverage is near zero.

Non-goals: the score-sequence decoder (HMM); a learned outcome classifier; automatic forced / unforced
classification (auto `outcome` is always null in v1); fixing rally segmentation or hitter attribution
upstream; per-player mapping across end changes inside one video; doubles reconciliation.

## 4. Owner decisions

- Owner entry plus automatic, owner labels win.
- Entry in the Kestrel UI (not a labelling sheet), per segment, with "Add point end here".
- Per point: the winner, and optionally Winner / Forced error / Unforced error. The ending stroke comes from
  `strokes.json` automatically.
- Automatic labeller: a rule cascade with abstention and a reconciliation check now; the HMM later.
- Near = `lower`, far = `upper` (the convention of every labelling sheet). Doubles: the team on that side.
- Owner labels survive a re-analysis.
- Approved extra field: `ending` (`in` / `out` / `net`) from the landing evidence.

## 5. Data contract

### 5.1 Owner labels: `data/rally_labels/<stem>.json`

Outside `outputs/`, like `data/library_tags.json`, so a re-analysis or a `match` delete keeps them.

```json
{
  "schema": 1,
  "revision": 7,
  "labels": [
    {"end_sec": 33.7, "start_sec": 23.0, "verdict": "upper", "outcome": "winner"},
    {"end_sec": 40.1, "start_sec": 36.7, "verdict": "not_point", "outcome": null},
    {"end_sec": 52.4, "start_sec": null, "verdict": "lower", "outcome": null, "added": true}
  ]
}
```

- `verdict`: `upper` | `lower` | `not_point` | `unsure`. `outcome`: `winner` | `forced_error` |
  `unforced_error` | null (only with an `upper` / `lower` verdict).
- Labels are keyed by **time**, not segment id. A segment label stores its segment's `start_sec` / `end_sec`
  when it was made. An added point (`added: true`) stores the video time of the point end in `end_sec`.
- `revision` increases on every save; a save based on an older revision is refused (409).
- An added point must name a winner (`upper` / `lower`); anything else is an invalid label.
- Re-attachment, when building: a segment label attaches to the segment whose `[start_sec - 1, end_sec + 1]`
  contains the label's `end_sec` (the one with the nearest end when several do). One label per segment: when
  two attach to the same segment the later list entry wins, and the displaced one survives as an added point
  only if it names a winner. A label that fits no segment becomes an added entry when it names a winner and
  is dropped otherwise (a `not_point` / `unsure` with no segment says nothing). Every added point stays an
  added entry at its `end_sec`.
- Written atomically (temp file + replace) under one lock in `app.py`; an unreadable store is never
  overwritten by a save (the route answers 409 with a static message), matching the tags store.

### 5.2 Derived file: `outputs/<stem>/rally_outcomes.json` (schema 1)

The only file consumers read. Stable: fields are only ever added; a breaking change bumps `schema`.

```json
{
  "schema": 1,
  "stem": "DJI_20261024092943_0009_D",
  "match_format": "singles",
  "generated_at": "2026-10-27T10:00:00Z",
  "rallies": [
    {"id": 1, "segment_id": 1, "start_sec": 22.96, "end_sec": 33.70,
     "winner_side": "upper", "outcome": "winner", "ending": "in",
     "ending_stroke": {"type": "smash", "group": "smash", "hitter": "upper", "frame": 2003, "confidence": 0.91},
     "confidence": 1.0, "source": "owner", "reviewed": true, "evidence": null}
  ],
  "summary": {
    "entries": 43, "labelled": 40, "by_source": {"owner": 38, "auto": 2},
    "coverage": 0.93, "points_won": {"upper": 28, "lower": 12},
    "segments": 31, "segments_rejected": 9, "added": 3,
    "confidence_calibrated": false,
    "reconciliation": {"status": "incomplete", "scope": "sides",
                       "expected": {"upper": 30, "lower": 13}, "claimed": {"upper": 28, "lower": 12},
                       "parts": ["DJI_20261024092943_0009_D", "DJI_20261024093729_0010_D"]}
  }
}
```

Per entry (`rallies`, in time order):

- `id`: 1..n in time order (not stable across rebuilds; `segment_id` is the link to `rally_segments.json`).
- `segment_id`: the segment's id, or null for an added point. An added point's `start_sec` is the previous
  entry's `end_sec` (or `end_sec - 10`, not below 0).
- `winner_side`: `upper` | `lower` | null (unknown). `outcome`: as in 5.1, or null.
- `ending`: `in` | `out` | `net` | null; only the automatic landing evidence sets it (also on owner entries,
  as extra information).
- `ending_stroke`: the last `strokes.json` stroke whose frame lies in the entry's window (rally pad 0.5 s);
  null when that stroke's type is `unknown` (an earlier stroke is never substituted), for doubles (strokes
  withheld), when `strokes.json` is absent or stale, or when the window has no stroke.
- `confidence`: 1.0 for an owner label; the rule's value for auto (5.3); null when unknown.
- `source`: `owner` | `auto` | null. `reviewed`: the owner gave any verdict (an `unsure` entry is
  reviewed, and keeps the automatic result if there is one).
- `evidence`: for auto entries, `{"landing": {...}, "last_hitter": ..., "next_server": ...}`; else null.
- Segments the owner marked `not_point` are left out of `rallies` and counted in `segments_rejected`.
  Unreviewed segments stay in with `reviewed: false`: a reader must know they may not be real points.

Summary:

- `entries` = len(`rallies`); `labelled` = entries with a `winner_side`; `coverage` = labelled / entries
  (null when there are no entries); `points_won` counts `winner_side`.
- `reconciliation` (5.4).

Top level `notes`: reader notes (an unreadable label file, malformed run files), for diagnosis only.

### 5.3 Automatic labeller (rule cascade)

Inputs read back from the run directory (never in-memory state): `rally_segments.json`, `hits.json`
(learned source only), `shuttle_trajectory.json`, `metadata.json` (court corners, frame size, fps).

Evidence per segment:

- **E1 last hitter**: the side of the last learned hit in `[start - 0.5, end + 0.5]`.
- **E2 landing**: the cleaned shuttle track after the last hit, up to `end_sec + 2 s`; no landing at all when the
  segment has no learned hit (a shuttle held before the serve would pass for one):
  - drop points within 1.5 % of the frame height of a `detection.suppressed_static_shuttle.cells` point,
    and points that jump more than 8 % of the frame height from the previous kept point (after 5 such rejects
    in a row the next point is kept: the previous kept point was the outlier);
  - the landing is the first run of >= 0.25 s where the kept points stay within 0.6 % of the frame height and
    cover at least half of the run's frames;
  - its mean point is projected to court metres through the court corners (valid for a landed shuttle only).
  - Verdict: inside the court on side X (`upper` when y < 6.7) means X lost; outside the court beyond side X's
    half means X won (the last hitter, from the other side, hit it out). The court is x in [0.46, 5.64] for
    singles (the singles sidelines) and [0, 6.1] for doubles, y in [0, 13.4] for both (the back boundary in
    play is the baseline). Within 0.3 m (inclusive) of a boundary line or of the net line: no verdict.
  - `ending`: outside -> `out`; inside on X with E1 = X -> `net`; inside on X with E1 = the other side -> `in`;
    inside without E1 -> null.
- **E3 next server**: the first learned hit of the next segment, when there are at least 3 s between this
  segment's last hit and it; its side is the predicted winner.

Rules (first match wins):

| E2 | E1 | E3 | Result | confidence |
|---|---|---|---|---|
| none | any | any | unknown | null |
| winner W, out | W (inconsistent) | any | unknown (conflict) | null |
| winner W | any | the other side | unknown (conflict) | null |
| winner W | any | W | W | 0.8 |
| winner W, out | the other side of W (consistent) | none | W | 0.6 |
| winner W, in or net | any | none | unknown (one signal) | null |

The confidences are fixed placeholders: `summary.confidence_calibrated` stays false until they are measured.
Auto `outcome` is always null in v1.

### 5.4 Reconciliation

Against the match record (`data/athletes.json`, via `library.athletes`): the primary record and its parts.

- The parts set is the primary plus `parts_of(primary)`. The part being built uses its fresh document; every
  other part uses its own `rally_outcomes.json` (a missing or unreadable one counts as a part with unknown
  entries, so the status can be at best `incomplete`). Each part's `points_won` is mapped into the primary's
  sides (exchanged when `sides_swapped`).
- `scope`: `sides` when the result has exactly one set (no end change), comparing per side; `total` when it
  has more sets, comparing only the point total.
- `status`:
  - `no_result`: no record, no `result.sets`, or a doubles record (v1);
  - `over`: claimed points exceed the expected on some side (or in total): certainly wrong;
  - `consistent`: every entry of every part is labelled and claimed == expected;
  - `short`: every entry is labelled but claimed < expected (the videos may not cover the whole match);
  - `incomplete`: some entries are unknown and claimed <= expected.
- Every part's file carries the same reconciliation (each build recomputes it for the whole match; a label
  save rebuilds all parts' files of that match).

### 5.5 Insight reader: `insights/outcomes.py`

`rally_outcomes_insight(run)` with the envelope:

- `unavailable("no_outcomes")` when the file is missing, unreadable or not schema 1;
- `partial(value, "outcomes_incomplete")` while coverage < 1 or reconciliation is not `consistent`;
- `ok(value)` otherwise. `value` is the summary plus `rallies`. `loaders.Run` gains `rally_outcomes`.
- The per-video insights sidecar (`insights.json`, `/api/insights/video/<stem>`) carries it as `outcomes`
  (insights version 8; `rally_outcomes.json` is a sidecar input, so a rebuild invalidates the cache).

## 6. Code layout

New package `badminton_analysis/outcomes/`:

- `labels.py`: label-store read / validate / atomic write; `attach(labels, segments)` re-attachment.
- `evidence.py`: E1, E2 (trajectory cleaning, landing, projection), E3. Pure functions on loaded data.
- `rules.py`: the cascade (5.3).
- `reconcile.py`: 5.4.
- `build.py`: `build(out_dir, labels_dir, athletes_path)` merges labels and auto evidence into the document,
  adds reconciliation, writes atomically; never raises for bad input. `python -m
  badminton_analysis.outcomes.build <out_dir>` backfills a run.

Other changes: `system.py` calls the build at the very end of match analysis (after the doubles stage),
wrapped so a failure only logs; `insights/outcomes.py` + `loaders.py`; `web/rally_labels_routes.py` (blueprint,
`app.py` registers it and adds the `all`-scope cleanup); `scripts/score_rally_outcomes.py`; UI in
`static/kestrel` (section 7).

## 7. Kestrel UI and API

API (blueprint, same style as `athletes_routes.py`, static bilingual error bodies):

- `GET /api/rally-labels/<stem>` -> `{schema, revision, labels}` (an empty doc when none).
- `PUT /api/rally-labels/<stem>` with `{revision, labels}`: 400 for an invalid body, 404 for an unknown video,
  409 when `revision` is stale (`reason: "stale"`, with the current `revision`) or the store is unreadable
  (`reason: "unreadable"`). Saves, then rebuilds `rally_outcomes.json` for every part of the match; the rebuild
  is skipped while that video's match-analysis job is queued, running or paused (the job's own final build
  picks the labels up; a posture job does not hold it back). Answers `{revision, outcomes_rebuilt}` (plus
  `rebuild_skipped: "job_active"`). Known gap: when that match job then fails or is cancelled, the labels reach
  `rally_outcomes.json` only at the next save or a CLI build.
- `rally_outcomes.json` is read through the existing output-file route.
- Delete scope `all` removes `data/rally_labels/<stem>.json` (never fatal, like the tag cleanup); scope `match`
  removes `rally_outcomes.json` only.

Match details page (one video or one part):

- The rally summary card gets a Point block for the selected segment: `Near won` / `Far won` / `Not a point
  end` / `Unsure` (player names from the match record when set), then optional `Winner` / `Forced error` /
  `Unforced error`. A click saves and advances to the next segment. Keys: N, F, X, U; 1, 2, 3 for the outcome.
- With no owner label, the auto result shows greyed with an "auto" tag; one click confirms or overrides.
- "Add point end here" next to the previous / next rally buttons, at the player's current time. Added points
  are listed under the tally (time, winner; click to seek) with a remove control.
- Timeline segments are tinted near-won / far-won / unknown / rejected; a tally line ("Near 12 - Far 7 ·
  19 of 31 labelled") with a reconciliation badge.
- Whole match view: the pooled tally and the match reconciliation.
- Doubles: the same controls, labelled near team / far team.
- EN + ZH strings in `core/i18n.js`. A save conflict (409) reloads the labels and says so.

## 8. Measurement

`scripts/score_rally_outcomes.py <out_dir>...` compares auto results with owner labels (owner `upper` /
`lower` verdicts only; `unsure` and `not_point` excluded): precision = correct auto claims / auto claims on
owner-labelled entries, coverage = auto claims / owner-labelled entries, and per-evidence accuracy for E1, E2
and E3 on their own (input for the later HMM). The owner labels 0009 + 0010 (43 points) and, if possible,
one more match; the numbers are reported as they are.

## 9. Testing

- pytest: label validation, revision conflicts, the unreadable-store refusal, atomic write; re-attachment after
  re-segmentation (moved, split, merged segments); trajectory cleaning, landing and projection on synthetic
  tracks; the rule table row by row; reconciliation cases (single video, split with swapped sides, multi-set
  total, doubles, over / short / incomplete); the reader's envelope on missing / broken files; the routes
  (GET, PUT, 400 / 404 / 409, rebuild skipped during a job); the system hook never fatal.
- node: the UI model (tally, tinting, keyboard mapping, auto-vs-owner display, conflict handling).
- Live: the UI on a seeded run in the worktree preview server.

## 10. Risks

- On current DJI runs the auto labeller may claim almost nothing; the report must read `coverage`.
- Labelling 43 points takes the owner some minutes per match; keyboard shortcuts and auto-advance keep it short.
- The landing projection assumes the corners in `metadata.json` are image-space court corners in the
  trajectory's pixel space; tests pin that.
