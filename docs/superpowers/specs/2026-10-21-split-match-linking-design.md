# Split-match linking (one match, several videos) - design

Status: approved by the owner in conversation on 2026-10-21 (design sections 1 and 2 reviewed;
section 3 and the test plan written here; owner waived separate spec/plan review and asked to
implement, test and deploy).

## 1. Problem

One match is sometimes recorded as two (or more) videos: the camera follows the tracked player, and
the players change ends part-way through the game (11 points in a 21-point game, 15 in a 30-point
game), so the recording is cut at the side change. Today a "match" is one video: the match record
(date, players, result, sets) is stored per video stem, the overview emits one row per video, and
every match-counting KPI counts each video separately. The same match therefore appears twice, its
record must be typed twice, and a win is counted twice.

## 2. Goal and non-goals

Goal: the Matches table shows one row per match. The record is entered once. Technique, rally and
movement statistics pool across the parts. Match-counting numbers (win rate, trend, number of
matches) count the match once.

Out of scope: doubles videos (no fixed sides to swap); auto-detecting that two videos belong
together (a later add-on: same date, same names, consecutive DJI timestamps); stitching the video
files or merging analysis outputs; per-part editing of the result.

## 3. Decisions (owner)

- Link manually from the Matches page (not auto-detect, not at upload).
- Per link, a "sides swapped" checkbox; default off = the tracked player is on the same picture
  side (upper/lower) in the part as in part 1. On = the players swapped picture sides.
- A match may have 2 or more parts (every part points at the same part 1).
- The Matches row is one expandable row; each part is a sub-row with its own status and tech score
  and a link to its own workspace.

## 4. Data model

`matches[<part stem>] = {"part_of": "<primary stem>", "sides_swapped": false}`.

A linked part stores nothing else: no date, players, result, sets. `schema_version` stays 1 (an
older reader would treat the entry as a blank singles record, which is harmless).

Rules enforced by `validate_match` (new code `invalid_link`, plus the checks below):

- `part_of` is a non-empty string, is not the part's own stem, and names a stem whose record exists,
  is singles (not doubles) and is itself not a part (one level only: no chains, no cycles).
- `sides_swapped` is a bool (absent = false).
- A link record carries no other key (`date`, `upper`, `lower`, `result`, `players`, `format` are
  rejected as `invalid_record`).
- Linking over a video that already holds its own non-blank record is rejected with
  `part_has_record`; the UI tells the user to clear that record first. (Replacing an existing link
  with another link is allowed.)
- `part_of` may not name a stem that is already a part (`invalid_link`).

Deleting a primary's video, or clearing/replacing the primary's record so that it is no longer a
valid singles primary, un-links its parts (their entries are removed): handled in
`_forget_match_record` (video delete) and `set_match`/`PUT` (record replaced by a doubles record
while parts point at it is rejected with `has_parts` instead of silently orphaning them).

## 5. Resolver

`resolve_record(data, stem)` (pure, in `badminton_analysis/library/athletes.py`) returns the
effective record:

- no link: the stored record (or `{}`).
- linked part: the primary's record with `part_of` and `sides_swapped` kept as extra keys. When
  `sides_swapped` is true, `upper`/`lower` are exchanged and `result.winner` is exchanged
  (`upper`<->`lower`); `result.sets` and `date` are returned as stored (set scores are not used per
  video). Dangling `part_of` (primary missing or itself a part, in a hand-edited store): the part
  resolves to `{}`, never an error.
- `parts_of(data, primary)` returns the primary's parts in stem order: `[{stem, sides_swapped}]`.
- `record_athlete_ids`/`/api/athletes` match counts count a split match once (a link record
  references no athlete, so nothing changes).

## 6. Aggregation (`insights/aggregate.py`)

`_make_entries` resolves each stem's record with `resolve_record` and records `group` (the primary
stem, or the stem itself) and `part_swapped`. Every downstream use of `entry["record"]` and
`entry["ident"]` therefore sees the part's effective record, in the part's own picture frame.

Units keep `(stem, side)` and gain `group` and `frame_side` (the side expressed in the primary's
frame: `side` when not swapped, the other side when swapped).

Pool across parts (every unit counts): radar / technique index, recovery, strokes and net share,
movement position, pull, avg rally (rallies are real and distinct per video).

Count once per match: `scope.stems` (number of distinct groups), `match_win_rate` (one outcome per
group and athlete, taken from the primary frame), trend (one point per group: date from the
primary's record, index pooled over the group's units for the selected athlete(s)), and the squad
benchmark (one index per group and athlete).

Rows: one row per `(group, frame_side)` unit group, in the primary's frame, carrying
`parts: [{stem, side, status, tech_index, needs_review}]` (primary first, then stem order). `stem`
is the primary stem, `side` is `frame_side`, `tech_index` pools the group's analysed parts for that
athlete, `status` is the least finished part (`processing` > `new` > `analysed`), `needs_review` is
true if any part needs review. A part never gets its own top-level row. A row of an unlinked video
has `parts: []`, so every existing consumer is unchanged.

Date range: a group's date is the primary's effective date; all parts are in or out together.

A part whose primary is not in the selected scope (for example scoped to an athlete who only
appears in part 2 by name mapping) cannot happen because the effective record is the primary's:
all parts resolve to the same athletes.

## 7. API

- `PUT /api/matches/<part>` with `{"part_of": "<primary>", "sides_swapped": bool}` creates or
  replaces the link. Errors (400, bilingual static strings like the existing codes): `invalid_link`,
  `part_has_record`, `has_parts` (writing a doubles record over a primary that still has parts).
- `GET /api/matches/<stem>`: for a part returns the resolved record plus `part_of` and
  `sides_swapped` (so the peek, workspace and old editor code keep working unchanged); for a primary
  with parts it also returns `parts: [{stem, sides_swapped}]`. A normal record has neither key.
- `PUT /api/matches/<part>` with a normal record, or `{}`, replaces/clears the link (that is how a
  part is unlinked).
- `POST /api/delete/<primary>` (video delete) un-links the parts via `_forget_match_record`.

## 8. UI (`static/kestrel/views/matches.js`, `core/i18n.js`)

- `matchRows` passes `parts` through (validated: array of `{stem, side, status, tech_index,
  needs_review}`; anything else is dropped to `[]`). A row with 2+ parts shows a "N parts" chip.
- Expandable row: a chevron button (`aria-expanded`) on a row with parts toggles sub-rows, one per
  part: part label ("Part 1", "Part 2"), video name, status chip, tech index, an "Open" button to
  that part's workspace (`#/matches/<part stem>`). Clicking the main row still opens the primary.
  The expansion state is kept across reloads of the list in the view's state.
- Record editor on a primary: unchanged, plus a "Link another video as part of this match" section
  listing the other analysed singles videos that are neither primary-with-parts-of-another nor
  parts, a "sides swapped" checkbox (default off) and a Link button (`PUT /api/matches/<part>`).
  Linked parts are listed with Unlink buttons (`PUT {}`), each showing its swap flag.
- Record editor on a video with no record: a "This video is part 2 of another match" dropdown of
  eligible primaries (videos that hold a singles record and are not parts), the "sides swapped"
  checkbox, and Link. A video that already holds its own record shows the dropdown disabled with the
  hint "clear this video's record first".
- A linked part never shows as its own row, so its editor is reached from the expanded sub-row
  (a "Linked to <primary> - Unlink" line with the swap flag).
- All new strings in zh and en (`i18n.js`); the i18n test enforces key parity.

## 9. Error handling

- Link target vanishes (primary deleted elsewhere, stale list): API returns `invalid_link`, UI shows
  the message and reloads the list.
- Hand-edited store with a dangling or chained link: the part resolves to `{}` (its stem then shows
  as an ordinary unrecorded video), nothing raises.
- Store unreadable/corrupt: existing handling (`_Edit`) applies, unchanged.

## 10. Test plan

Python (`tests/test_library_athletes.py`, `tests/test_insights_aggregate.py`,
`tests/test_app_athletes.py`): validate_match link accept/reject matrix (self, missing primary,
doubles primary, chained, part already has own record, extra keys, bad swapped type); resolver
(unlinked, linked, swapped flips sides and winner, dangling, chain); set_match writes/clears a link;
parts_of; aggregation (win counted once, trend one point, scope.stems counts matches, technique pools
both parts' units for the right athlete incl. swapped, one row with `parts`, part 2 not analysed
yet, status = least finished, undated primary); API (PUT link, GET resolved + parts, errors, unlink
by `{}`, video delete of the primary un-links the parts).

JS (`tests/js/*.mjs`, existing `node --test`-style files): `matchRows` parts pass-through and
sanitising, a model-level helper for eligible primaries/parts, i18n key parity.

Full suite: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp <unique>`
and the JS tests the repo already runs.

## 11. Implementation breakdown

1. Backend: `library/athletes.py` (validate, set_match, resolver, parts_of), `web/athletes_routes.py`
   (error codes, GET shape), `app.py` (`_forget_match_record`), `insights/aggregate.py`.
2. Frontend: `views/matches.js`, `core/i18n.js` (+ a pure helper in the same file for eligible
   links), tests.
3. QA review, then PR (owner merges; `main` is protected).
