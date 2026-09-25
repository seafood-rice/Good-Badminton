# Video Library Tags — Design

- **Date:** 2026-09-22
- **Status:** approved by the owner; ready for an implementation plan
- **Interactive wireframe:** `docs/superpowers/specs/2026-09-22-library-tags-wireframe.html`
  (open in a browser; every control works)

---

## 0. What exists today (verified against the code, not assumed)

| Thing | Where | Behaviour |
|---|---|---|
| Library listing | `app.py:367-407` (`api_videos`) | Rebuilt per request by globbing `VIDEOS`. Returns `name`, `has_match`, `has_posture`, `status`, `date`, `duration_sec`, `thumb`. |
| Client-side filter | `static/kestrel.js:37-51` (`filteredVideos`) | Filters `lib.videos` on `q` / `mode` / `status`, then sorts. |
| Filter controls | `static/kestrel.js:55-60` (`FILTER_DEFS`) | Segmented controls for Type, Status, Sort. |
| Type label | `static/kestrel.js:96-97` (`modeLabel`) | `has_posture && !has_match ? Drill : Match` — derived, never user-set. |
| Type badge | `static/kestrel.js:140-141`, `static/kestrel.css:86` (`.vmode`) | Drawn over the thumbnail. |
| Delete | `app.py:1043-1075` (`api_delete`) | Scope `all` removes `outputs/<stem>` **entirely**. |
| Stem validation | `app.py:63-77` (`_safe_stem`) | Rejects traversal and unknown stems. Reusable as-is. |
| JSON writing | `badminton_analysis/data/writer.py:28-32` (`write_json`) | **Not atomic** — opens the target path in `"w"` and writes in place. |

Two of these directly shape the design:

1. **`outputs/<stem>` is deleteable**, so tags cannot live there. Tags describe the *video*;
   clearing analysis results must not clear them.
2. **`write_json` is not atomic.** An earlier draft of this design claimed it was. For a
   single file holding every tag in the library, a crash or a full disk mid-write would
   truncate the file and lose all of them, so the store needs its own atomic write.

---

## 1. Goal

Let the owner label videos with multiple free-form tags, filter the library by several tags
at once, and rename or delete a tag across the whole library — and fold the existing Type
(Match / Drill) control into that same mechanism so there is one way to narrow the library,
not two.

---

## 2. Done means

**Tagging**
1. A video can carry any number of user tags, added and removed from the library card
   without leaving the page.
2. Adding a tag offers autocomplete over tags already in use; a tag that does not exist yet
   is created by typing it.
3. Tag edits persist immediately and survive a reload, an analysis run, and
   `/api/delete` at any scope short of deleting the video file itself.

**Filtering**
4. Selecting *n* tags shows only videos carrying **all** *n* (AND).
5. Each tag chip shows how many videos would remain **if that tag were also applied**;
   a chip that would yield zero is disabled, so the filter cannot be driven into an empty
   result by clicking.
6. The free-text search box matches tag text as well as file names.
7. The result line states the count and the active constraint in words.

**Type absorbed**
8. The Type segmented control, `modeLabel()`, and the `.vmode` thumbnail badge are gone.
9. `match` and `drill` appear as **system tags** in the same chip row, derived per request
   from `has_match` / `has_posture`, visually distinct, and not editable.
10. A video with both analyses carries **both** tags. A video with neither carries neither.

**Management**
11. A tag can be renamed across every video; renaming onto an existing tag merges them.
12. A tag can be deleted from every video, behind a confirmation, with undo.
13. System tags are listed in the manage view but cannot be renamed or deleted, and their
    names cannot be used for a user tag.

**Non-regression**
14. Status and Sort behave exactly as they do today.
15. The library still loads in the two requests it makes today.
16. A missing, empty, or corrupt tag store yields "no tags" and a working library, never an error page.

---

## 3. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **AND**, not OR, for multiple tags | Each tag narrows, matching the existing Status/Type controls. Owner-chosen. |
| D2 | Add/remove per video **plus** global rename and delete | Free-typed tags drift (`smash` / `Smash` / `smashes`); without rename the only fix is editing every video by hand. Owner-chosen. |
| D3 | Editing happens **on the library card** | Tagging where you browse; labelling a back catalogue is otherwise a page-open and back-navigation per video. Owner-chosen. |
| D4 | Single store at `data/library_tags.json` | One read per library load regardless of size; a global rename is a single-file edit. `data/` is already gitignored. |
| D5 | Type becomes **derived system tags**, not seeded user tags | Seeded tags go stale: a later analysis would not update them, and a user could delete a tag that the system still believes. Owner-chosen (this refactor). |
| D6 | Status stays a segmented control | It is a mutually-exclusive lifecycle state the system advances on its own, not a label the owner applies. Folding it in would mean chips that silently change themselves. |
| D7 | System tags carry a **key plus a localised label** | The UI is bilingual. `match` must display as 比赛 in Chinese while user tags display exactly as typed. Filtering matches on the key. |
| D8 | Card icons move from emoji to inline **SVG** | Emoji render per-platform and cannot be themed. Touches the existing delete button, so it is a small edit to shipped code rather than purely additive. Owner-chosen. |

### D5 in detail — what the refactor actually changes

`modeLabel`'s `has_posture && !has_match ? Drill : Match` forces an either/or that the data
does not support:

- A video with **both** analyses was labelled only `Match`; its drill half was invisible.
  It now carries both tags. Verified in the wireframe on `highclear1`.
- A video with **neither** analysis fell through the ternary and was labelled `Match`,
  which was simply untrue. It now carries no system tag.

So `match` and `drill` are **not mutually exclusive** under this design. That is a
deliberate accuracy gain, not an oversight — an earlier draft of this design wrongly
claimed selecting one would exclude the other.

---

## 4. Data model

```json
{
  "schema_version": 1,
  "videos": {
    "Dji 20260919111119 0007 D": ["court-a", "smash"],
    "IMG_1270": ["footwork"]
  }
}
```

- Keyed by **video stem**, the identity every other route already uses.
- Values are user tags only. System tags are never written to the store; they are computed
  on read, so they cannot go stale and cannot be corrupted.
- Stored normalised: trimmed, lowercased, de-duplicated, sorted. Display is the stored form.
  Normalising on write means `Smash` and `smash` cannot both exist.
- The set of known user tags is derived from the values. There is no separate tag list to
  keep in sync, so a tag cannot outlive its last use.
- A stem with no tags is absent from the map rather than holding `[]`.

### Reserved names

`match` and `drill` are reserved. The API rejects them as user tags with `400`, and the
editor refuses them inline with a message rather than silently dropping them.

### Constraints on a tag

Non-empty after trimming; at most 40 characters; no control characters. Longer or malformed
input is rejected with `400` and a reason, not truncated silently.

---

## 5. Components

| File | Responsibility |
|---|---|
| `badminton_analysis/library/tags.py` | **New.** Pure store: load, save (atomically), normalise, set-for-video, rename, delete, counts. No Flask, no request handling. |
| `app.py` | **Modified.** Three routes; `tags` added to each `/api/videos` row; tag entry dropped on video deletion. |
| `static/kestrel.js` | **Modified.** Tag chip row, AND filter, card chips, editor popover, manage modal; Type control removed. |
| `static/kestrel.css` | **Modified.** Chip, popover and modal styles; `.vmode` removed. |
| `tests/test_library_tags.py` | **New.** Store behaviour. |
| `tests/test_app_tags.py` | **New.** Routes. |

`tags.py` is deliberately free of Flask so the store's rules — normalisation, merge-on-rename,
atomic write, corrupt-file recovery — are testable without a request context.

### `tags.py` interface

```python
TAGS_PATH                                  # data/library_tags.json
RESERVED = ("match", "drill")
MAX_TAG_LEN = 40

def load(path=TAGS_PATH) -> dict            # {stem: [tag, ...]}; {} if missing/corrupt
def save(data, path=TAGS_PATH) -> None      # atomic: temp file in the same dir + os.replace
def normalise(tag) -> str | None            # trimmed+lowercased, or None if invalid/reserved
def tags_for(data, stem) -> list[str]
def set_tags(data, stem, tags) -> dict      # replaces; drops the key when empty
def rename(data, old, new) -> dict          # merges if `new` already exists
def delete(data, tag) -> dict
def forget(data, stem) -> dict              # drop a deleted video's entry
```

No `counts()`: the client derives counts from the `/api/videos` rows, so a server-side
counter would be a second source of the same number.

`save` writes a temp file in the **same directory** and `os.replace`s it, because
`os.replace` is only atomic within a filesystem. This is why the store does not reuse
`write_json`, which writes in place.

---

## 6. API

| Method | Route | Body / params | Returns |
|---|---|---|---|
| `PUT` | `/api/videos/<name>/tags` | `{"tags":["smash","court-a"]}` | `{"ok":true,"tags":[...]}` — the normalised, stored list |
| `POST` | `/api/tags/rename` | `{"from":"smash","to":"smashes"}` | `{"ok":true,"merged":bool,"affected":n}` |
| `DELETE` | `/api/tags/<tag>` | — | `{"ok":true,"affected":n}` |

`PUT` replaces the whole list rather than applying add/remove deltas, so the popover has no
partial-failure state to reconcile: one request per edit, and the response is the truth.

All routes validate the stem with the existing `_safe_stem`, and reject reserved or
malformed tags with `400` and a reason. `/api/videos` gains `"tags": [...]` (user tags) per
row; system tags stay derived in the client from `has_match` / `has_posture`, which the row
already carries, so no extra request and no duplicated truth.

**There is deliberately no `GET /api/tags`.** An earlier draft had one. It is dead weight: a
tag exists only because some video carries it (§4), so once every row carries its `tags`, the
client can derive the full tag list, the counts, and the manage view from `/api/videos`
alone. Adding the route would mean a second source of the same truth and a second request on
load, for nothing. Three routes, not four.

---

## 7. UI

**Filter bar.** Type is removed. Search and Status keep their positions. A tag row sits
below: system chips first, a thin separator, then user chips alphabetically. Each chip shows
its projected count. `Clear tags` appears only when something is selected. `Manage…` sits at
the end as a quiet text button — a rare action that should not compete with the filters.

**Chips.** User tags use the filled `--fill` pill. System tags use a transparent pill with a
dashed border and a leading dot, meaning "derived, not yours to edit"; the shape says it
before the tooltip does. Selected chips use `--accent-soft` with a solid accent border, so
selection is carried by border and weight, not colour alone. Each is
`role="switch"` with `aria-checked` and an explicit `aria-label` of
`"<tag>, <n> videos[, from analysis results]"`.

> The `aria-label` is explicit for a reason found while testing the wireframe: with a
> `title` attribute present, the chip announced its tooltip instead of its own name.

**Cards.** The `.vmode` thumbnail badge is gone. Below the status chip, a tag row shows
system tags then user tags; tags matching the current filter are highlighted. A card with no
tags says so in muted italics rather than collapsing, so the affordance stays visible. The
tag and delete buttons sit in the thumbnail corner, revealed on hover **and on
`:focus-within`** so they are reachable by keyboard.

**Editor popover.** Anchored to the tag button, dismissed by Escape or backdrop click. Shows
system tags read-only with a one-line explanation, then removable user chips, then an input
with autocomplete (arrow keys, Enter, "Create …" for a new tag). Saves on every change.

**Manage modal.** Two groups: *From analysis* (listed, with counts, no actions) and *Your
tags* (rename inline, delete with confirmation). Delete shows an undo toast.

**Empty state.** When a tag combination matches nothing, the grid is replaced by a message
naming the number of selected tags and a button to clear them. With projected counts this
should be reachable only via search plus tags, not by clicking chips alone.

---

## 8. Localisation

System tags: `{"match": {"en": "Match", "zh": "比赛"}, "drill": {"en": "Drill", "zh": "训练"}}`,
defined in `kestrel.js` beside the existing `FILTER_DEFS`. Filtering and storage use the key;
only display uses the label. User tags are the owner's own text and are never translated.
Search matches both the key and the localised label, so typing `比赛` finds Match videos
while the UI is in Chinese.

---

## 9. Error handling

| Case | Behaviour |
|---|---|
| Store missing | Treated as `{}`. First write creates it and `data/` if needed. |
| Store corrupt or unreadable | Logged once, treated as `{}`. The library loads with no tags rather than failing. **Not** overwritten until the user makes an edit, so a recoverable file is not destroyed by a read. |
| Write fails | `500` with a reason; the UI reverts the chip and says the edit did not save. The popover does not close on failure. |
| Unknown stem | `404` via `_safe_stem`. |
| Reserved or malformed tag | `400` with a reason; shown inline in the popover. |
| Rename onto an existing tag | Merges, reports `merged: true`. Not an error. |
| Video deleted (`scope=all`) | Its entry is dropped from the store in the same request. |
| Tags for a video whose file is gone | Harmless and invisible: the client derives every tag and count from the `/api/videos` rows, and a deleted video has no row. `forget` removes the entry when the deletion goes through the app; an entry left by a file deleted outside it simply never surfaces. |

---

## 10. Testing

**Hermetic, in the committed suite:**

- *Store:* normalisation (case, whitespace, duplicates, length, control characters);
  reserved names rejected; `set_tags` dropping an emptied key; rename merging; delete
  across videos; `forget`.
- *Atomic write:* a simulated failure mid-write leaves the previous file intact; the temp
  file lands in the same directory; no temp file is left behind on success.
- *Corrupt store:* invalid JSON, a JSON array instead of an object, and a non-list value
  each yield `{}` without raising, and are not overwritten on read.
- *Routes:* each of the three, including `400` for reserved and malformed tags, `404` for an
  unknown stem, and `PUT` returning the normalised list.
- *`/api/videos`:* rows carry `tags`; a video with no tags carries `[]`.
- *Delete integration:* `scope=all` removes the tag entry; narrower scopes do not.

**Controller-run, not in the suite:** a click-through of the wireframe's flows against the
real app — tag two videos, filter by both, rename, delete, undo, switch language — plus a
keyboard-only pass (tab to a card, reach the tag button via `:focus-within`, operate the
popover, Escape out).

---

## 11. Out of scope

- Tag colours, groups, hierarchies, or descriptions.
- Bulk tagging by multi-select.
- Tag-based auto-suggestions or any analysis that consumes tags.
- Editing tags from the results page (D3 chose the card; a second editor can follow if the
  first proves insufficient).
- Absorbing Status into tags (D6).
- Sharing or syncing tags between machines — `data/` is gitignored, like `videos/` and
  `outputs/`, so tags are local to this machine and do not survive a fresh clone.

---

## 12. Risks

- **R1 — One file holds every tag.** Mitigated by the atomic write and by treating a corrupt
  file as empty without overwriting it, but a lost `data/library_tags.json` loses all tags.
  There is no backup mechanism and none is proposed; the file is small and easy to copy.
- **R2 — Concurrent writes.** Two browser tabs editing different videos could interleave
  read-modify-write and lose one edit. **Superseded by what shipped:** a lost-update race
  found during browser verification silently reverted an Undo, so every write path
  (`PUT` tags, rename, delete-tag, the video-delete cleanup, and the `/api/videos` read) now
  holds a single process-wide `app._TAGS_LOCK` for the whole load-mutate-save (or load-only)
  span. This still does not survive two separate OS processes or machines sharing the file —
  the lock is in-process — but that is not this app's deployment shape.
- **R3 — Normalising to lowercase is lossy.** `Court-A` becomes `court-a` permanently. Chosen
  over case-insensitive matching with preserved display because the latter needs a canonical
  form anyway and makes rename ambiguous.
- **R4 — Client-side filtering does not scale forever.** Consistent with the existing filters
  and fine for a library of this size; a library in the thousands would need server-side
  filtering, which this design does not attempt.
- **R5 — Removing `.vmode` changes a familiar card.** The mode moves from the thumbnail to
  the tag row. Intentional, and the wireframe is the check on whether it reads well.

---

## 13. Global constraints

- **Branch.** This is a feature and does not belong on `claude/b11-rally-detection`, which
  carries six video/pose bug fixes that should merge independently. Cut a fresh branch from
  `origin/main`.
- **No writes to `main`**; `main` is the protected integration branch.
- **Never commit** `data/library_tags.json`, weights, datasets, or the pre-existing local
  dirt (the untracked `BirdEye Prototype.html`).
- **Windows env:** `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider`.
- `tests/test_ai_handoff.py` is known-flaky under long full-suite runs and unrelated to this
  work.

---

## 14. Implementation notes

Recorded after a whole-branch review, so this section describes what shipped rather than
what was planned — see R2 above for the concurrency change in particular.

- **Every user-derived string that reaches `innerHTML` or an HTML attribute goes through
  `escHTML`** (`static/kestrel.js`). Tags are free-form text and can contain `< > " & '`;
  nothing bypasses this on the way into a card, chip, popover, or manage-modal row.
- **Corrupt or unreadable store, in the form it actually shipped in** (supersedes the table
  in §9 for a write path):
  - A **read** (`GET /api/videos`, or any other caller of `tags.load`) never raises and
    never writes, exactly as §9 says. A corrupt or unreadable file is logged once per
    process per path and treated as `{}`.
  - A **user's tag edit** (`PUT` tags, rename, delete-tag) uses the stricter
    `tags.load_for_update`, which raises instead of guessing:
    - Missing file -> `{}` (a first edit may create the store).
    - Corrupt (unparseable, or the wrong shape) -> the route backs the file up
      byte-for-byte to `library_tags.json.corrupt-<UTC timestamp>` next to it, then treats
      the store as `{}` and saves the edit. The edit wins; nothing already on disk is
      silently lost, because it is still sitting in the backup file.
    - Unreadable (any other `OSError`, e.g. a permission error) -> the route refuses to
      write at all and returns `500` with a static bilingual message. The file might hold
      tags this request never saw, so guessing `{}` here could throw them away for real.
  - A **video delete**'s tag cleanup (`forget`) also uses `load_for_update`, but on either
    `StoreCorrupt` or `StoreUnreadable` it skips the cleanup entirely — no write, no
    backup — and the delete still returns `200`. A delete carries no new tag data worth
    trading the old file away for, unlike a user's own edit.
  - `forget` never creates the store file for an untagged video and never rewrites an
    unchanged store: the cleanup is skipped up front when the stem has no entry.
- **Dot-only tags are rejected** (`tags.normalise`). `.`/`..`/`...` used to normalise
  successfully, so they could be created but not deleted: a browser collapses
  `DELETE /api/tags/..` as a path-traversal segment before the request leaves, so the tag
  had no way to be removed again through the API.
