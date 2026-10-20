# Kestrel doubles support: design addendum

**Date:** 2026-10-20 · **Branch:** `claude/kestral-sports-ui-design-015bc4` · **Status:** approved by the owner
(per-identity athlete mapping chosen in session). Extends
`2026-10-19-kestrel-telemetry-ui-design.md` (referred to as "the telemetry spec") with doubles runs from
`2026-10-17-doubles-match-analysis-design.md` (referred to as "the doubles design").

## 1. Goal

Every telemetry-UI feature handles doubles matches (4 players, 2 teams) with real per-player data instead of the
current "not available" placeholder: per-identity athlete mapping, per-player and per-team insights, who-hit
shares, and doubles-aware Overview, Matches, peek, workspace, Players and Pipeline screens. Singles behaviour and
contracts are unchanged. Stroke types and technique scores stay withheld on doubles (doubles design section 7), and
every who-hit figure carries the "unvalidated" flag.

## 2. Inputs (doubles design 6.6, 6.5)

For a run with `metadata.json.match_format == "doubles"`:

- `players.json`: `{status: ok|degraded|degraded_teams|error, reason, players:[{id: "P1".."P4", team: 1|2|null, name,
  frames, coverage, distance_m, avg_speed_mps, max_speed_mps, hits, hits_named, heatmap, tracklets:[{id, start_frame,
  end_frame, confidence}]}], teams:[{id, players:[...]}], stats:{...}, validated: false, identity_video:
  {status: ok|failed|skipped, file, reason}}`. Any field may be missing; the file may be absent or malformed.
- `tracklets.jsonl`: one JSON object per line; the fields the insights read are `id`, `frames` (a list of frame
  numbers) and `court` (a list of `[x, y]` metres of the same length), plus `side`. Lines that do not match are skipped.
- `hits.json`: `hits[{frame, time_sec, hitter (side), hitter_id|null, hitter_team|null, hitter_confidence,
  hitter_evidence: shuttle|swing|single_candidate|none}]`, `hitter_attribution: {validated: false}`.
- `player_names.json` (optional): `{"P1": "Alice"}`; it is already copied into `players.json` `name`.
- `detect_<stem>_players.mp4` (identity video), only when `players.json.identity_video.status == "ok"`.

## 3. Athlete store (extends telemetry spec section 4)

A match record is singles (`upper`/`lower`, unchanged) **or** doubles:

```json
{ "date": "2026-10-11", "format": "doubles",
  "players": { "P1": {"athlete_id": "a_..."} | {"name": "S. Ito"} | null, "P2": ..., "P3": ..., "P4": ... },
  "result": { "winner_team": 1 | 2, "sets": [[21,18],[19,21],[21,15]] } | null }
```

- `format` is `"doubles"` or absent/`"singles"`. A doubles record has no `upper`/`lower`; a singles record has no
  `players`; a record with both, or with `format: "doubles"` and a singles `result.winner`, is invalid.
- `players` keys are a subset of `P1..P4`; each value follows the singles side rule (`athlete_id` XOR `name`, an
  `athlete_id` must exist; null or missing = unassigned). An athlete may be mapped to at most one identity per match
  (the same `athlete_id` on two identities is invalid).
- `sets` are team-1-first pairs of ints 0-99, 0-5 sets; a winner is required when sets are present.
- Deleting an athlete rewrites every doubles identity that referenced them to `{"name": <their name>}` (counted in
  `rewritten`). `GET /api/athletes` `matches` counts doubles records too (an athlete counted once per match).
- `side_athlete(data, record, side)` is unchanged for singles; new `player_athlete(data, record, pid)` returns
  `{"athlete_id", "name"}` for doubles; new `record_format(record)`.
- Routes (`/api/matches/<stem>` GET/PUT) accept the doubles shape with the same static bilingual error bodies; new
  error code `invalid_players` for a malformed `players` map.

## 4. Insights (extends telemetry spec section 5)

### 4.1 Sidecar: the `doubles` insight

`compute_video` adds a top-level `doubles` insight (for singles runs: `unavailable` / `not_doubles`). New reason
codes: `not_doubles`, `no_players` (no readable players.json), `doubles_degraded` (params `{status}`; used for
`degraded` and `degraded_teams`), `doubles_error` (players.json status `error`; params `{reason}` text is dropped,
never echoed). Value:

```json
{ "status": "ok|degraded|degraded_teams", "validated": false,
  "players": [ { "id": "P1", "team": 1, "name": "Alice"|null, "coverage": 0.0-1.0, "frames": 0,
                 "distance_m": 0.0, "avg_speed_mps": 0.0, "max_speed_mps": 0.0,
                 "hits": 0, "hits_named": 0, "samples": 0, "grid": [[...]]|null } ],
  "teams": [ { "id": 1, "players": ["P1","P2"], "hits": 0, "hits_named": 0 } ],
  "hits": { "total": 0, "named": 0, "named_share": 0.0|null, "single_candidate": 0 },
  "identity_video": { "status": "ok", "file": "detect_<stem>_players.mp4" } | null }
```

- `grid` is an 11x10 court-occupancy grid (shares sum to 1, 4 decimals, row 0 at the net, col = floor(x / 0.61), like
  `movement.py`), built from the player's tracklets (identity -> tracklet ids from `players.json`): per frame the
  half is chosen by the point's own side (y < 6.7 upper, else lower) and the same orientation/clamp/drop rules as
  movement apply; `samples` = points used; null when under 30 samples.
- A hit is *named* when `hitter_id` is set and `hitter_evidence` is `shuttle` or `swing` (same rule as
  `tracking/doubles.py _is_named`); `single_candidate` hits count in `single_candidate`, never in `named`.
  `named_share = named / total` (null when total 0). Per-player/team hit counts use `hitter_id`/`hitter_team` from
  `hits.json` (a hit with `hitter_id` null counts only in `hits.total`).
- Envelope: `ok` when players.json status is `ok`; `partial` + `doubles_degraded` for `degraded`/`degraded_teams`
  (teams may be null); `unavailable` + `doubles_error` for `error`; `unavailable` + `no_players` when the file is
  missing/unreadable or has no players.
- Doubles runs keep the singles per-player insights `technique`, `strokes`, `movement`, `recovery`, `pull`
  `unavailable` with `doubles_run`. `INSIGHTS_VERSION` becomes 6.

### 4.2 Overview aggregation

- A doubles unit is (stem, identity) for each identity mapped to an athlete in the record; unmapped identities
  contribute only to the doubles runs list. Athlete scope selects doubles stems where the athlete is mapped;
  squad scope selects stems where any identity is a squad member's; no scope selects every doubles stem.
- New top-level response key `doubles`: an Insight with value
  `{ "matches": <doubles stems in scope>, "athlete": Summary|null, "runs": [Run] }` where
  `Summary = {distance_m, avg_speed_mps, max_speed_mps, frames, hits, hits_named, named_share, matches, grid,
  samples}` aggregates the selected athlete's identity across their doubles matches (distance and hits summed, speeds
  frame-weighted, grid sample-weighted and renormalised; `null` when no athlete is in scope or mapped) and
  `Run = {stem, date, date_source, status, validated: false, teams:[...], players:[player summaries with an
  `athlete` name field resolved from the record, else the identity name, else null]}`. `unavailable` + `no_data`
  when no doubles stems are in scope. A `doubles` insight status is `partial` when any run is degraded.
- `movement.position.value.grid` additionally includes the selected athlete's doubles grids (sample-weighted, same
  merge as singles) so the court map shows the athlete's real position; `pull`, `recovery`, technique and strokes
  KPIs remain singles-only.
- Match rows for doubles stems (in addition to the existing keys): `doubles: true`, `format: "doubles"`,
  `athlete_player: "P3"|null`, `team: 1|2|null` (the athlete's team), `partner`: name or null, `opponent`: the
  opposing team's names joined with " / " (null when none known), `result` W/L when the record has a winner team and
  the athlete's team is known, `sets` oriented to the athlete's team (`[own, opp]`; team-1-first when no athlete),
  `tech_index: null`, `needs_review`: true when the identity status is not `ok`.
- `scope.units` counts singles units plus mapped doubles identity units (document the choice in code).
- Findings: new codes `doubles_identity_degraded{status}` (watch), `doubles_hits_unvalidated{named_share}` (info,
  only when an athlete summary exists).
- As built (Task 3 and its fix round 1; the full contract is in telemetry spec 5.4):
  - `matches` counts **analysed** doubles stems only; a `new`/`processing` doubles stem is a row, not a run.
  - `Summary` gains `hits_named_of` (the hits of the rows that report `hits_named`);
    `named_share = hits_named / hits_named_of`, and the UI's "N of M" uses it. `hits` still sums every row.
  - Every doubles row gains `teams: ["A / B", "C / D"] | null` (team 1 first; names resolved like
    `runs[].players[].athlete`, else the identity id; null without team info). A no-scope row is titled from it.
  - `movement.position.distance_m` sums known distances only, and `mean_speed_mps` is computed from units whose
    distance and speed are both known; either is null when nothing usable exists. This also applies to singles.
  - `doubles_hits_unvalidated` needs a numeric `named_share`; `doubles_identity_degraded` shows the localized
    identity status.

### 4.3 Jobs

`/api/jobs` is unchanged; the front end maps the doubles stage names (`doubles_identity`, `doubles_heatmaps`,
`doubles_video`; see `app.py` `STAGE_LABELS`) to localized text.

## 5. Front end (extends telemetry spec section 6)

- **Overview:** a "Doubles players" widget (shown when `doubles.value.matches > 0`): per run a table of players
  (name or identity, team chip, distance, average/max speed, hits, named share) with the `unvalidated` badge and the
  identity status/reason; the selected athlete's summary row when present. Tactics-lens tiles for the athlete use the
  doubles summary where the singles value is unavailable: a "Distance covered" tile and a "Hits named" tile.
- **Matches:** doubles rows get a `.k-chip` "Doubles", the row title lists teams ("A / B vs. C / D" or identity
  labels), and the record editor shows four identity pickers (labelled `P1 · Team 1 · coverage 83%`), a winner-team
  select and a sets field (team 1 first); it switches layout on the run's format (from `row.doubles`).
- **Peek:** teams, identity status, named-hit share with the unvalidated badge, no scores.
- **Workspace:** plays `detect_<stem>_players.mp4` when the identity video is `ok` (else the annotated video); a
  Players panel; a who-hit timeline strip coloured per identity (four fixed team colours from the doubles design:
  muted when `hitter_id` is null or the evidence is `single_candidate`); a hits table (time, player, team,
  confidence, evidence); rally timeline unchanged; stroke types replaced by a "withheld for doubles" note.
- **Players view:** match counts include doubles. **Pipeline view:** localized doubles stage names. **Palette:** no
  change. All text through textContent/escHTML; zh and en for every new string.

## 6. Testing

Synthetic doubles runs (`tests/insights_fixtures.py` gets `make_doubles_run`), hostile `players.json` /
`tracklets.jsonl` / `hits.json` (malformed, NaN, huge, wrong types), store round trips and validation, aggregate
scope matrices (athlete/squad/none x singles/doubles/mixed), strict-JSON checks, node tests for every pure
front-end model, browser pass with a temporary synthetic doubles run (removed afterwards).

## 7. Out of scope

Doubles stroke types and technique scoring, doubles recovery/pull insights, identity correction UI beyond the
record's identity->athlete mapping, validation of who-hit accuracy (owner-run, no labels), and cross-match identity
linking (P1 in one match is unrelated to P1 in another unless the owner maps both to the same athlete).
