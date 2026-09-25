# Video Library Tags — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner put multiple free-form tags on a video, filter the library by several
tags at once (AND), and rename or delete a tag library-wide — folding the existing Match/Drill
Type control into the same mechanism.

**Architecture:** A pure store module (`badminton_analysis/library/tags.py`) owns
normalisation, the atomic write, and every mutation as a dict-in/dict-out function, with no
Flask import. `app.py` adds three thin routes over it and one extra field on `/api/videos`.
`static/kestrel.js` renders tags as chips, filters client-side alongside the existing filters,
and drops the Type segmented control — `match`/`drill` become *system tags* derived in the
client from the `has_match`/`has_posture` fields the rows already carry.

**Tech Stack:** Python 3.11 + Flask (no new dependencies), vanilla ES5-style JS in one file,
plain CSS with the existing custom-property tokens, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-library-tags-design.md`
**Interactive wireframe:** `docs/superpowers/specs/2026-09-22-library-tags-wireframe.html` —
open it in a browser. It is the reference for every UI task; where this plan and the wireframe
disagree, the plan wins and the disagreement is a bug in one of them worth reporting.

---

> ## ⚠ Read before Task 2: the `app.py` line numbers in this plan are stale
>
> This plan was written against `origin/main` at `d4c69d4`. **PR #7**
> (`claude/b11-rally-detection`) is open and adds ~88 lines to `app.py` above both regions
> this plan edits:
>
> | Symbol | In this plan (pre-#7) | After #7 merges |
> |---|---|---|
> | `@app.route('/api/videos')` | `app.py:279` | `app.py:367` |
> | `@app.route('/api/delete/<video_name>')` | `app.py:953` | `app.py:1043` |
>
> PRs on this repo are **squash-merged**, so once #7 lands this branch must be rebased onto
> the new `main` before Task 2. **Re-grep every `app.py` line reference before editing** —
> `git grep -n "^@app.route" -- app.py` — and trust the grep over the numbers printed here.
> `static/kestrel.js` line numbers are unaffected: #7 only touches it around line 600, well
> below everything this plan edits.
>
> Nothing else conflicts. This branch touches only its three doc files, so the rebase itself
> is clean.

## Global Constraints

- **Branch:** `claude/library-tags`, already cut from `origin/main` with upstream deliberately
  unset. **No writes to `main`** — it is the protected integration branch.
- **Never commit** `data/library_tags.json` (it is gitignored under `data/`), model weights,
  datasets, or the pre-existing untracked `BirdEye Prototype.html` at the repo root.
- **Windows env:** run everything as
  `PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider`.
- **Reserved tag names:** `match` and `drill`. They are derived, never stored.
- **Tag constraints:** non-empty after trimming, at most **40** characters, no control
  characters, no `/` or `\`. Stored lowercase, trimmed, de-duplicated, sorted.
- **Store path:** `data/library_tags.json`. `data/` already exists and is gitignored.
- **AND semantics:** selecting *n* tags shows only videos carrying all *n*.
- **Honest reporting:** a missing or corrupt store yields "no tags" and a working library,
  never an error page, and is **not** overwritten on read.
- `tests/test_ai_handoff.py` is known-flaky under long full-suite runs and unrelated to this
  work. Re-run it alone before calling it a regression.

### Testing reality, stated up front

**This repo has no JavaScript test runner** — no jest, vitest, or karma, and `package.json`
does not exist. The Python tasks (1–5) are strict TDD. The UI tasks (6–9) cannot be unit
tested here, so each one ends with `node --check` for syntax plus a **scripted browser
verification** with exact expected output. Do not invent a JS test harness to satisfy the
pattern; do not claim UI tasks are "tested" when they are verified by hand.

---

## File Structure

| Path | Responsibility |
|---|---|
| `badminton_analysis/library/__init__.py` | **New.** Empty package marker. |
| `badminton_analysis/library/tags.py` | **New.** The whole store: normalisation, load, atomic save, and each mutation as a pure function. No Flask, no request state, no I/O beyond the one file. |
| `app.py` | **Modified.** `TAGS_PATH` constant, `tags` on each `/api/videos` row, three routes, and a `forget` call in `api_delete`. |
| `static/kestrel.js` | **Modified.** Tag state, AND filter, chip row, card chips, editor popover, manage modal; Type control removed. |
| `static/kestrel.css` | **Modified.** Chip/popover/modal styles; `.vmode` removed. |
| `tests/test_library_tags.py` | **New.** Store behaviour, including the atomic write and corrupt-file recovery. |
| `tests/test_app_tags.py` | **New.** The three routes, the `/api/videos` field, and the delete integration. |

`tags.py` is kept free of Flask so its rules — normalisation, merge-on-rename, atomicity,
corrupt-file recovery — are testable without a request context, and so a future CLI could
reuse it.

---

### Task 1: The tag store

**Files:**
- Create: `badminton_analysis/library/__init__.py`
- Create: `badminton_analysis/library/tags.py`
- Test: `tests/test_library_tags.py`

**Interfaces:**
- Consumes: nothing.
- Produces, all pure (they return new dicts and never mutate their argument):
  - `RESERVED = ("match", "drill")`, `MAX_TAG_LEN = 40`, `SCHEMA_VERSION = 1`
  - `normalise(tag) -> str | None`
  - `normalise_all(tags) -> tuple[list[str], list]` — `(clean_sorted_unique, rejected_originals)`
  - `load(path) -> dict[str, list[str]]`
  - `save(data, path) -> None`
  - `tags_for(data, stem) -> list[str]`
  - `set_tags(data, stem, tags) -> dict`
  - `rename(data, old, new) -> dict`
  - `delete(data, tag) -> dict`
  - `forget(data, stem) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_library_tags.py
"""The video-library tag store.

Kept free of Flask so the rules that matter -- normalisation, merge-on-rename,
the atomic write, and recovering from a corrupt file -- are testable without a
request context.
"""
import json
import os

import pytest

from badminton_analysis.library import tags as libtags


# ---------------------------------------------------------------- normalise

def test_tags_are_trimmed_and_lowercased():
    assert libtags.normalise("  Smash ") == "smash"


def test_empty_and_whitespace_are_rejected():
    assert libtags.normalise("") is None
    assert libtags.normalise("   ") is None
    assert libtags.normalise(None) is None


def test_reserved_names_are_rejected():
    """match/drill are derived from analysis results, never user-owned."""
    for name in ("match", "drill", "  MATCH  "):
        assert libtags.normalise(name) is None


def test_overlong_tags_are_rejected_not_truncated():
    assert libtags.normalise("x" * libtags.MAX_TAG_LEN) == "x" * libtags.MAX_TAG_LEN
    assert libtags.normalise("x" * (libtags.MAX_TAG_LEN + 1)) is None


def test_control_characters_are_rejected():
    assert libtags.normalise("bad\ttag") is None
    assert libtags.normalise("bad\ntag") is None


def test_path_separators_are_rejected():
    """A tag is a URL path segment in DELETE /api/tags/<tag>; a slash breaks routing."""
    assert libtags.normalise("a/b") is None
    assert libtags.normalise("a\\b") is None


def test_normalise_all_splits_clean_from_rejected():
    clean, rejected = libtags.normalise_all([" Smash ", "match", "", "court-a", "smash"])
    assert clean == ["court-a", "smash"]          # sorted, de-duplicated
    assert rejected == ["match", ""]


def test_normalise_all_of_nothing():
    assert libtags.normalise_all([]) == ([], [])


# ---------------------------------------------------------------- load / save

def test_save_then_load_round_trips(tmp_path):
    p = tmp_path / "data" / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    assert libtags.load(p) == {"clip": ["smash"]}


def test_save_writes_the_documented_shape(tmp_path):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    payload = json.loads(p.read_text(encoding="utf-8"))
    assert payload["schema_version"] == libtags.SCHEMA_VERSION
    assert payload["videos"] == {"clip": ["smash"]}


def test_missing_file_loads_as_empty(tmp_path):
    assert libtags.load(tmp_path / "nope.json") == {}


def test_corrupt_file_loads_as_empty_and_is_not_overwritten(tmp_path):
    """A recoverable file must survive a read. Losing every tag to a bad parse
    would be worse than showing none."""
    p = tmp_path / "library_tags.json"
    p.write_text("{not json", encoding="utf-8")
    assert libtags.load(p) == {}
    assert p.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("junk", ['["a","b"]', '{"videos": []}', '"a string"', '123'])
def test_structurally_wrong_files_load_as_empty(tmp_path, junk):
    p = tmp_path / "library_tags.json"
    p.write_text(junk, encoding="utf-8")
    assert libtags.load(p) == {}


def test_non_list_and_non_string_entries_are_dropped(tmp_path):
    p = tmp_path / "library_tags.json"
    p.write_text(json.dumps({"videos": {"a": "smash", "b": ["ok", 7], "c": ["fine"]}}),
                 encoding="utf-8")
    assert libtags.load(p) == {"b": ["ok"], "c": ["fine"]}


def test_save_is_atomic_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(libtags.os, "replace", boom)
    with pytest.raises(OSError):
        libtags.save({"clip": ["smash", "court-a"]}, p)

    assert libtags.load(p) == {"clip": ["smash"]}, "the previous file must survive"


def test_save_leaves_no_temp_file_behind(tmp_path):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    assert [f.name for f in tmp_path.iterdir()] == ["library_tags.json"]


def test_failed_save_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    p = tmp_path / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    monkeypatch.setattr(libtags.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        libtags.save({"clip": ["x"]}, p)
    assert sorted(f.name for f in tmp_path.iterdir()) == ["library_tags.json"]


def test_save_creates_the_directory(tmp_path):
    p = tmp_path / "made" / "up" / "library_tags.json"
    libtags.save({"clip": ["smash"]}, p)
    assert p.exists()


# ---------------------------------------------------------------- mutations

def test_set_tags_replaces_the_whole_list():
    data = {"clip": ["old"]}
    assert libtags.set_tags(data, "clip", ["new", "another"]) == {"clip": ["another", "new"]}


def test_set_tags_to_empty_drops_the_key():
    """An empty list is absence, not an empty array on disk."""
    assert libtags.set_tags({"clip": ["smash"]}, "clip", []) == {}


def test_set_tags_does_not_mutate_its_argument():
    data = {"clip": ["old"]}
    libtags.set_tags(data, "clip", ["new"])
    assert data == {"clip": ["old"]}


def test_tags_for_unknown_stem_is_empty():
    assert libtags.tags_for({}, "nope") == []


def test_rename_applies_everywhere():
    data = {"a": ["smash"], "b": ["smash", "net"], "c": ["net"]}
    assert libtags.rename(data, "smash", "smashes") == {
        "a": ["smashes"], "b": ["net", "smashes"], "c": ["net"]}


def test_rename_onto_an_existing_tag_merges_without_duplicating():
    data = {"a": ["smash", "net"]}
    assert libtags.rename(data, "smash", "net") == {"a": ["net"]}


def test_rename_of_an_unused_tag_changes_nothing():
    data = {"a": ["net"]}
    assert libtags.rename(data, "smash", "smashes") == {"a": ["net"]}


def test_delete_removes_the_tag_everywhere_and_drops_emptied_keys():
    data = {"a": ["smash"], "b": ["smash", "net"]}
    assert libtags.delete(data, "smash") == {"b": ["net"]}


def test_forget_drops_one_video():
    assert libtags.forget({"a": ["x"], "b": ["y"]}, "a") == {"b": ["y"]}


def test_forget_of_an_unknown_stem_is_a_no_op():
    assert libtags.forget({"a": ["x"]}, "zzz") == {"a": ["x"]}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_library_tags.py -q -p no:cacheprovider
```
Expected: FAIL — `No module named 'badminton_analysis.library'`.

- [ ] **Step 3: Create the package marker**

Create `badminton_analysis/library/__init__.py` containing only:

```python
"""Library-level metadata that belongs to the video, not to an analysis run."""
```

- [ ] **Step 4: Write the store**

```python
# badminton_analysis/library/tags.py
"""User-assigned tags for the video library.

Tags describe the VIDEO, not an analysis run, so they deliberately do not live
in ``outputs/<stem>/``: ``/api/delete`` with scope "all" removes that directory
whole, and clearing analysis results must not clear a video's labels.

Everything here is pure -- each mutation takes a dict and returns a new one --
so the rules can be tested without Flask, a request, or a temp server.
"""
import json
import os
import tempfile

RESERVED = ("match", "drill")
"""Names derived from analysis results (``has_match`` / ``has_posture``).

They are computed per request and never stored, so they cannot go stale and
cannot be deleted into a state the system still believes. Refusing them as user
tags keeps one meaning per name.
"""

MAX_TAG_LEN = 40
SCHEMA_VERSION = 1

_BAD_CHARS = ("/", "\\")


def normalise(tag):
    """Canonical form of a tag, or None if it is not usable.

    Lowercasing is deliberate and lossy: it stops ``Smash`` and ``smash`` both
    existing, which would make a rename ambiguous.
    """
    if not isinstance(tag, str):
        return None
    clean = tag.strip().lower()
    if not clean or len(clean) > MAX_TAG_LEN:
        return None
    if clean in RESERVED:
        return None
    if any(ch in clean for ch in _BAD_CHARS):
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in clean):
        return None
    return clean


def normalise_all(tags):
    """Split an incoming list into (clean, rejected).

    Rejected entries are returned rather than dropped so a caller can say which
    tag was refused and why, instead of silently saving fewer tags than asked.
    """
    clean, rejected = set(), []
    for tag in tags or []:
        norm = normalise(tag)
        if norm is None:
            rejected.append(tag)
        else:
            clean.add(norm)
    return sorted(clean), rejected


def load(path):
    """Tags from disk as {stem: [tag, ...]}; {} when missing or unusable.

    Never raises and never writes. A corrupt file is reported as empty and left
    exactly as it is: losing every tag to a bad parse would be worse than
    showing none, and the file may still be recoverable by hand.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    videos = payload.get("videos")
    if not isinstance(videos, dict):
        return {}
    out = {}
    for stem, tags in videos.items():
        if not isinstance(stem, str) or not isinstance(tags, list):
            continue
        clean = sorted({t for t in tags if isinstance(t, str)})
        if clean:
            out[stem] = clean
    return out


def save(data, path):
    """Write the store atomically.

    A temp file in the SAME directory plus os.replace, because os.replace is
    only atomic within a filesystem. This is why the store does not reuse
    badminton_analysis.data.writer.write_json, which opens the target in "w"
    and writes in place -- with every tag in one file, a crash mid-write there
    would lose all of them.
    """
    path = str(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "videos": data}
    handle_fd, tmp = tempfile.mkstemp(dir=directory, prefix=".library_tags-", suffix=".tmp")
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def tags_for(data, stem):
    return list(data.get(stem, []))


def set_tags(data, stem, tags):
    """Replace one video's tags. An empty result drops the key entirely."""
    out = dict(data)
    clean = sorted(set(tags))
    if clean:
        out[stem] = clean
    else:
        out.pop(stem, None)
    return out


def rename(data, old, new):
    """Rename across every video, merging when ``new`` is already present."""
    out = {}
    for stem, tags in data.items():
        if old in tags:
            kept = [t for t in tags if t != old]
            if new not in kept:
                kept.append(new)
            out[stem] = sorted(kept)
        else:
            out[stem] = list(tags)
    return out


def delete(data, tag):
    """Remove a tag from every video, dropping any video left with none."""
    out = {}
    for stem, tags in data.items():
        kept = [t for t in tags if t != tag]
        if kept:
            out[stem] = kept
    return out


def forget(data, stem):
    """Drop a deleted video's entry."""
    out = dict(data)
    out.pop(stem, None)
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_library_tags.py -q -p no:cacheprovider
```
Expected: PASS, no failures. The count is higher than the number of `def test_` lines because
`test_structurally_wrong_files_load_as_empty` is parametrised four ways.

- [ ] **Step 6: Commit**

```bash
git add badminton_analysis/library tests/test_library_tags.py
git commit -m "feat(library): tag store with an atomic write"
```

---

### Task 2: Serve tags on `/api/videos`

Purely additive: the library gains a field and nothing changes behaviourally, so this can land
and be reviewed on its own.

**Files:**
- Modify: `app.py` — imports near `:4-9`, a constant near `:11-13`, and `api_videos`
  (`:279` before PR #7, `:367` after — **re-grep**, see the warning at the top of this plan)
- Test: `tests/test_app_tags.py` (new)

**Interfaces:**
- Consumes: `badminton_analysis.library.tags` (Task 1).
- Produces:
  - `app.TAGS_PATH` — a module-level `Path`, monkeypatchable in tests exactly as the existing
    `VIDEOS` / `OUTPUTS` are.
  - Each `/api/videos` row gains `"tags": [...]` — **user tags only**, sorted. System tags stay
    derived in the client.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_tags.py
"""Tag routes and the tags field on the library listing."""
import json

import pytest

import app as webapp
from badminton_analysis.library import tags as libtags


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "TAGS_PATH", tmp_path / "data" / "library_tags.json")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), videos, outputs, tmp_path / "data" / "library_tags.json"


def test_rows_carry_their_tags(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    libtags.save({"clip": ["court-a", "smash"]}, store)

    row = next(r for r in c.get("/api/videos").get_json() if r["name"] == "clip")
    assert row["tags"] == ["court-a", "smash"]


def test_untagged_rows_carry_an_empty_list_not_null(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert next(r for r in c.get("/api/videos").get_json()
                if r["name"] == "clip")["tags"] == []


def test_a_missing_store_does_not_break_the_library(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert c.get("/api/videos").status_code == 200


def test_a_corrupt_store_does_not_break_the_library(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{not json", encoding="utf-8")

    res = c.get("/api/videos")
    assert res.status_code == 200
    assert next(r for r in res.get_json() if r["name"] == "clip")["tags"] == []


def test_the_store_is_read_once_not_per_video(client, monkeypatch):
    """The listing already globs the disk; N store reads would make it worse."""
    c, videos, _outputs, _store = client
    for name in ("a", "b", "c"):
        (videos / f"{name}.mp4").write_bytes(b"\x00")

    calls = []
    real = libtags.load
    monkeypatch.setattr(webapp.libtags, "load", lambda p: calls.append(p) or real(p))

    c.get("/api/videos")
    assert len(calls) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider
```
Expected: FAIL — `app` has no attribute `TAGS_PATH`.

- [ ] **Step 3: Add the import and the constant**

In `app.py`, after the existing `from badminton_analysis.training.plan_generator import generate_plan`
(line 9):

```python
from badminton_analysis.library import tags as libtags
```

And after `OUTPUTS = PROJECT_ROOT / 'outputs'` (line 13):

```python
# Library tags live outside outputs/: /api/delete scope "all" removes
# outputs/<stem> whole, and clearing analysis results must not clear a
# video's labels. data/ is already gitignored.
TAGS_PATH = PROJECT_ROOT / 'data' / 'library_tags.json'
```

- [ ] **Step 4: Read the store once and attach the field**

In `api_videos`, immediately after `videos = []` (line 283):

```python
    tag_data = libtags.load(TAGS_PATH)
```

and add to the appended dict, after `'output_dir': ...` (line 317):

```python
                'tags': libtags.tags_for(tag_data, name),
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider
```
Expected: PASS (5 passed).

- [ ] **Step 6: Confirm nothing else regressed**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_ai_handoff.py
```
Expected: PASS, previous count plus the new tests.

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_tags.py
git commit -m "feat(api): serve each video's tags on the library listing"
```

---

### Task 3: `PUT /api/videos/<name>/tags`

**Files:**
- Modify: `app.py` — a new route beside `api_videos`
- Test: `tests/test_app_tags.py` (extend)

**Interfaces:**
- Consumes: `libtags`, `TAGS_PATH` (Task 2); the existing `_safe_stem` at `app.py:63`.
- Produces: `PUT /api/videos/<video_name>/tags` taking `{"tags": [...]}` and returning
  `{"ok": true, "tags": [...normalised...]}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_tags.py`:

```python
def test_put_stores_normalised_tags(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    res = c.put("/api/videos/clip/tags", json={"tags": [" Smash ", "court-a", "smash"]})
    assert res.status_code == 200
    assert res.get_json()["tags"] == ["court-a", "smash"]
    assert libtags.load(store) == {"clip": ["court-a", "smash"]}


def test_put_replaces_rather_than_appends(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    libtags.save({"clip": ["old"]}, store)

    c.put("/api/videos/clip/tags", json={"tags": ["new"]})
    assert libtags.load(store) == {"clip": ["new"]}


def test_put_with_an_empty_list_clears_the_video(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    libtags.save({"clip": ["old"]}, store)

    assert c.put("/api/videos/clip/tags", json={"tags": []}).status_code == 200
    assert libtags.load(store) == {}


def test_put_rejects_a_reserved_tag_and_saves_nothing(client):
    c, videos, _outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    res = c.put("/api/videos/clip/tags", json={"tags": ["smash", "match"]})
    assert res.status_code == 400
    assert "match" in res.get_json()["rejected"]
    assert libtags.load(store) == {}, "a rejected request must not partially save"


def test_put_rejects_an_overlong_tag(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    res = c.put("/api/videos/clip/tags", json={"tags": ["x" * 41]})
    assert res.status_code == 400


def test_put_rejects_a_non_list_body(client):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    assert c.put("/api/videos/clip/tags", json={"tags": "smash"}).status_code == 400


def test_put_on_an_unknown_video_is_404(client):
    c, _videos, _outputs, _store = client
    assert c.put("/api/videos/ghost/tags", json={"tags": ["x"]}).status_code == 404


def test_put_on_a_traversal_attempt_is_404(client):
    c, _videos, _outputs, _store = client
    assert c.put("/api/videos/..%2Fetc/tags", json={"tags": ["x"]}).status_code in (404, 400)


def test_put_reports_a_write_failure_instead_of_claiming_success(client, monkeypatch):
    c, videos, _outputs, _store = client
    (videos / "clip.mp4").write_bytes(b"\x00")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(webapp.libtags, "save", boom)
    res = c.put("/api/videos/clip/tags", json={"tags": ["smash"]})
    assert res.status_code == 500
    assert "error" in res.get_json()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider -k put
```
Expected: FAIL — 404/405 for every `put`, because the route does not exist.

- [ ] **Step 3: Add the route**

In `app.py`, directly after `api_videos` (after line 319):

```python
@app.route('/api/videos/<video_name>/tags', methods=['PUT'])
def api_set_video_tags(video_name):
    """Replace one video's tags.

    Whole-list replacement rather than add/remove deltas: the editor sends the
    list it is showing, so there is no partial-failure state to reconcile and
    the response is the truth.
    """
    stem = _safe_stem(video_name)
    if stem is None:
        return jsonify({'error': '未知的视频 / unknown video'}), 404
    body = request.get_json(silent=True) or {}
    raw = body.get('tags')
    if not isinstance(raw, list):
        return jsonify({'error': 'tags must be a list'}), 400
    clean, rejected = libtags.normalise_all(raw)
    if rejected:
        # Refuse the whole request rather than saving the acceptable subset:
        # a silent partial save is indistinguishable from a successful one.
        return jsonify({'error': '标签无效或为保留名 / invalid or reserved tag',
                        'rejected': rejected}), 400
    try:
        data = libtags.set_tags(libtags.load(TAGS_PATH), stem, clean)
        libtags.save(data, TAGS_PATH)
    except OSError as exc:
        return jsonify({'error': f'无法保存标签 / could not save tags: {exc}'}), 500
    return jsonify({'ok': True, 'tags': clean})
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider
```
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_tags.py
git commit -m "feat(api): replace a video's tags"
```

---

### Task 4: Rename and delete a tag library-wide

**Files:**
- Modify: `app.py` — two routes after `api_set_video_tags`
- Test: `tests/test_app_tags.py` (extend)

**Interfaces:**
- Consumes: `libtags`, `TAGS_PATH` (Task 2).
- Produces:
  - `POST /api/tags/rename` taking `{"from": str, "to": str}` → `{"ok", "merged", "affected"}`
  - `DELETE /api/tags/<tag>` → `{"ok", "affected"}`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_tags.py`:

```python
def test_rename_applies_across_videos(client):
    c, videos, _outputs, store = client
    for n in ("a", "b"):
        (videos / f"{n}.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"], "b": ["smash", "net"]}, store)

    res = c.post("/api/tags/rename", json={"from": "smash", "to": "smashes"})
    assert res.status_code == 200
    assert res.get_json()["affected"] == 2
    assert res.get_json()["merged"] is False
    assert libtags.load(store) == {"a": ["smashes"], "b": ["net", "smashes"]}


def test_rename_onto_an_existing_tag_merges_and_says_so(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash", "net"]}, store)

    res = c.post("/api/tags/rename", json={"from": "smash", "to": "net"})
    assert res.status_code == 200
    assert res.get_json()["merged"] is True
    assert libtags.load(store) == {"a": ["net"]}


def test_rename_normalises_both_ends(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"]}, store)

    c.post("/api/tags/rename", json={"from": " SMASH ", "to": " Smashes "})
    assert libtags.load(store) == {"a": ["smashes"]}


def test_rename_to_a_reserved_name_is_refused(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"]}, store)

    assert c.post("/api/tags/rename", json={"from": "smash", "to": "match"}).status_code == 400
    assert libtags.load(store) == {"a": ["smash"]}


def test_rename_from_a_reserved_name_is_refused(client):
    c, _videos, _outputs, _store = client
    assert c.post("/api/tags/rename", json={"from": "match", "to": "x"}).status_code == 400


def test_rename_with_missing_fields_is_refused(client):
    c, _videos, _outputs, _store = client
    assert c.post("/api/tags/rename", json={"from": "smash"}).status_code == 400
    assert c.post("/api/tags/rename", json={}).status_code == 400


def test_delete_removes_the_tag_everywhere(client):
    c, videos, _outputs, store = client
    for n in ("a", "b"):
        (videos / f"{n}.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"], "b": ["smash", "net"]}, store)

    res = c.delete("/api/tags/smash")
    assert res.status_code == 200
    assert res.get_json()["affected"] == 2
    assert libtags.load(store) == {"b": ["net"]}


def test_delete_of_a_reserved_name_is_refused(client):
    c, _videos, _outputs, _store = client
    assert c.delete("/api/tags/match").status_code == 400


def test_delete_of_an_unused_tag_is_a_no_op_not_an_error(client):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["net"]}, store)

    res = c.delete("/api/tags/ghost")
    assert res.status_code == 200
    assert res.get_json()["affected"] == 0
    assert libtags.load(store) == {"a": ["net"]}


def test_rename_reports_a_write_failure(client, monkeypatch):
    c, videos, _outputs, store = client
    (videos / "a.mp4").write_bytes(b"\x00")
    libtags.save({"a": ["smash"]}, store)
    monkeypatch.setattr(webapp.libtags, "save",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert c.post("/api/tags/rename", json={"from": "smash", "to": "x"}).status_code == 500
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider -k "rename or delete_"
```
Expected: FAIL — the routes do not exist.

- [ ] **Step 3: Add both routes**

In `app.py`, after `api_set_video_tags`:

```python
@app.route('/api/tags/rename', methods=['POST'])
def api_rename_tag():
    """Rename a tag across every video, merging if the target already exists."""
    body = request.get_json(silent=True) or {}
    old = libtags.normalise(body.get('from'))
    new = libtags.normalise(body.get('to'))
    if old is None or new is None:
        return jsonify({'error': '标签无效或为保留名 / invalid or reserved tag'}), 400
    try:
        data = libtags.load(TAGS_PATH)
        affected = sum(1 for tags in data.values() if old in tags)
        merged = any(new in tags for tags in data.values())
        libtags.save(libtags.rename(data, old, new), TAGS_PATH)
    except OSError as exc:
        return jsonify({'error': f'无法保存标签 / could not save tags: {exc}'}), 500
    return jsonify({'ok': True, 'merged': merged, 'affected': affected})


@app.route('/api/tags/<tag>', methods=['DELETE'])
def api_delete_tag(tag):
    """Remove a tag from every video. The videos themselves are untouched."""
    name = libtags.normalise(tag)
    if name is None:
        return jsonify({'error': '标签无效或为保留名 / invalid or reserved tag'}), 400
    try:
        data = libtags.load(TAGS_PATH)
        affected = sum(1 for tags in data.values() if name in tags)
        libtags.save(libtags.delete(data, name), TAGS_PATH)
    except OSError as exc:
        return jsonify({'error': f'无法保存标签 / could not save tags: {exc}'}), 500
    return jsonify({'ok': True, 'affected': affected})
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider
```
Expected: PASS (24 passed).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_tags.py
git commit -m "feat(api): rename and delete a tag library-wide"
```

---

### Task 5: Forget a deleted video's tags

**Files:**
- Modify: `api_delete` in `app.py` (`:953` before PR #7, `:1043` after, and Tasks 2-4 shift it
  further still) — **always re-grep before editing:**
  `git grep -n "^@app.route('/api/delete/<video_name>'" -- app.py`
- Test: `tests/test_app_tags.py` (extend)

**Interfaces:**
- Consumes: `libtags.forget`, `TAGS_PATH`.
- Produces: no new API. `scope=all` drops the store entry; narrower scopes do not.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_tags.py`:

```python
def test_deleting_a_video_entirely_drops_its_tags(client):
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)
    libtags.save({"clip": ["smash"], "other": ["net"]}, store)

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
    assert libtags.load(store) == {"other": ["net"]}


def test_deleting_only_analysis_results_keeps_the_tags(client):
    """Tags describe the video. Clearing results must not clear its labels."""
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    out = outputs / "clip"
    out.mkdir(parents=True)
    (out / "detections.jsonl").write_text("{}\n", encoding="utf-8")
    libtags.save({"clip": ["smash"]}, store)

    assert c.post("/api/delete/clip", json={"scope": "match"}).status_code == 200
    assert libtags.load(store) == {"clip": ["smash"]}


def test_a_failed_tag_forget_does_not_fail_the_delete(client, monkeypatch):
    """The files are already gone by then; a tag-store hiccup must not report
    the delete as failed."""
    c, videos, outputs, store = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    (outputs / "clip").mkdir(parents=True)
    libtags.save({"clip": ["smash"]}, store)
    monkeypatch.setattr(webapp.libtags, "save",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))

    assert c.post("/api/delete/clip", json={"scope": "all"}).status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py -q -p no:cacheprovider -k "drops_its_tags or keeps_the_tags"
```
Expected: FAIL — the tag entry survives a `scope=all` delete.

- [ ] **Step 3: Drop the entry inside the `scope == 'all'` branch**

Locate the `if scope == 'all':` block in `api_delete` and add, after the existing job cleanup
loop and still inside that branch:

```python
            # Tags belong to the video, so they go only when the video does.
            # Never fatal: the files are already deleted by this point, and
            # reporting the delete as failed over a tag-store hiccup would be
            # worse than a stale entry, which is invisible anyway (a deleted
            # video has no /api/videos row to carry it).
            try:
                libtags.save(libtags.forget(libtags.load(TAGS_PATH), stem), TAGS_PATH)
            except OSError as exc:
                print(f'Tag cleanup skipped for {stem}: {exc}')
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest tests/test_app_tags.py tests/test_app_delete.py -q -p no:cacheprovider
```
Expected: PASS, including the existing delete tests.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app_tags.py
git commit -m "feat(api): drop a video's tags when the video is deleted"
```

---

### Task 6: Remove the Type control and show tags on cards

The first UI task, and deliberately display-only: after it the library shows tags and no longer
has a Type segmented control, but nothing is editable yet.

**Files:**
- Modify: `static/kestrel.js:23` (`lib.filter`), `:37-51` (`filteredVideos`), `:55-61`
  (`FILTER_DEFS`, `FILTER_GROUP_LABEL`), `:85` (`renderFilterBar`), `:96-97` (`modeLabel`),
  `:126-147` (`renderCards`)
- Modify: `static/kestrel.css:86` (`.vmode`), plus new chip rules

> **Do not touch `wiz.mode`** (`kestrel.js:24`, `:201-202`, `:221`, `:281`, `:497`, `:500`).
> That is the *wizard's* step state — match vs posture analysis — and has nothing to do with
> `lib.filter.mode`. They share a name and nothing else.

**Interfaces:**
- Consumes: `row.tags` from Task 2; the existing `row.has_match` / `row.has_posture`.
- Produces, for Tasks 7-9:
  - `SYS_TAGS = { match: ['比赛','Match'], drill: ['训练','Drill'] }`
  - `isSysTag(t) -> bool`
  - `sysTagsOf(v) -> string[]`
  - `tagsOf(v) -> string[]` — system tags then user tags
  - `tagLabel(t) -> string` — localised for system tags, verbatim for user tags

- [ ] **Step 1: Add the system-tag helpers**

In `static/kestrel.js`, immediately before `function modeLabel` (line 96):

```javascript
  // Match/Drill are DERIVED from what analysis produced, so they are computed
  // here rather than stored as user tags: a stored copy would go stale, and a
  // user could delete a tag the system still believes. They carry a stable key
  // plus a localised label, because the key is what filtering and storage use
  // while the UI is bilingual. User tags are the owner's own text and are
  // never translated.
  var SYS_TAGS = { match: ['比赛', 'Match'], drill: ['训练', 'Drill'] };
  function isSysTag(t) { return Object.prototype.hasOwnProperty.call(SYS_TAGS, t); }
  function tagLabel(t) { return isSysTag(t) ? _loc(SYS_TAGS[t], 0, 1) : t; }
  function sysTagsOf(v) {
    var out = [];
    if (v.has_match) out.push('match');
    if (v.has_posture) out.push('drill');
    return out;
  }
  function tagsOf(v) { return sysTagsOf(v).concat(v.tags || []); }
```

- [ ] **Step 2: Delete `modeLabel`**

Remove lines 96-97 entirely:

```javascript
  function modeLabel(v) { return v.has_posture && !v.has_match ? (state.lang==='zh'?'训练':'Drill')
                                                               : (state.lang==='zh'?'比赛':'Match'); }
```

It encoded `has_posture && !has_match ? Drill : Match`, which was wrong twice: a video with
**both** analyses showed only Match, hiding its drill half, and a video with **neither** fell
through to Match, which was untrue. `sysTagsOf` replaces it and can return two tags, one, or
none.

- [ ] **Step 3: Remove the Type filter**

At line 23, drop `mode` from the filter state:

```javascript
  var lib = { videos: [], filter: { q: '', tags: [], status: 'all', sort: 'date' } };
```

In `filteredVideos` (lines 37-51), delete these two lines:

```javascript
      if (f.mode === 'match' && !v.has_match) return false;
      if (f.mode === 'posture' && !v.has_posture) return false;
```

In `FILTER_DEFS` (line 55), delete the whole `mode:` entry; in `FILTER_GROUP_LABEL` (line 61)
delete `mode: ['类型', 'Type'],`. In `renderFilterBar` (line 85) change:

```javascript
    bar.innerHTML = searchHTML() + segGroup('status') + segGroup('sort');
```

- [ ] **Step 4: Make search match tags, and filter by tags**

Replace the body of `filteredVideos` (lines 37-51) with:

```javascript
  function filteredVideos() {
    var f = lib.filter;
    var out = lib.videos.filter(function (v) {
      if (f.q) {
        var q = f.q.toLowerCase();
        var inName = v.name.toLowerCase().indexOf(q) !== -1;
        // Match the key AND the localised label, so "比赛" finds Match videos
        // while the UI is in Chinese and "match" still finds them in English.
        var inTags = tagsOf(v).some(function (t) {
          return t.indexOf(q) !== -1 || tagLabel(t).toLowerCase().indexOf(q) !== -1;
        });
        if (!inName && !inTags) return false;
      }
      if (f.status !== 'all' && v.status !== f.status) return false;
      // AND: every selected tag must be present.
      return f.tags.every(function (t) { return tagsOf(v).indexOf(t) !== -1; });
    });
    out.sort(function (a, b) {
      return f.sort === 'name' ? a.name.localeCompare(b.name)
                               : (b.date || '').localeCompare(a.date || '');
    });
    return out;
  }
```

- [ ] **Step 5: Render tags on the card and drop the `.vmode` badge**

In `renderCards` (lines 140-146), replace the returned markup with:

```javascript
      var tagHTML = tagsOf(v).map(function (t) {
        return '<span class="vtag' + (isSysTag(t) ? ' sys' : '') +
          (lib.filter.tags.indexOf(t) !== -1 ? ' hit' : '') + '">' + tagLabel(t) + '</span>';
      }).join('') || '<span class="vtag-none">' + (state.lang==='zh'?'无标签':'no tags') + '</span>';
      return '<div class="vcard" role="button" tabindex="0" data-name="' + nm + '"><div class="vthumb" style="' + thumb + '">' +
        '<span class="vdur mono">' + dur + '</span>' +
        '<button class="vdel" data-del="' + nm + '" aria-label="' + (state.lang==='zh'?'删除':'Delete') + '">🗑</button></div>' +
        '<div class="vbody"><div class="vname">' + v.name + '</div>' +
        '<div class="vdate mono">' + (v.date||'') + '</div>' +
        '<div class="vfoot">' + statusChip(v) + '</div>' +
        '<div class="vtags">' + tagHTML + '</div></div></div>';
```

- [ ] **Step 6: Add the card tag styles**

In `static/kestrel.css`, delete line 86 (`.vmode{...}`) and add after `.chip` (line 92):

```css
/* System tags are derived from analysis results, not owned by the user. The
   dashed outline and leading dot say "not yours to edit" before any tooltip
   does, and the distinction survives both themes. */
.vtags{margin-top:8px;display:flex;flex-wrap:wrap;gap:5px;align-items:center;}
.vtag{display:inline-flex;align-items:center;gap:5px;font:600 11px inherit;padding:3px 9px;
  border-radius:var(--radius-pill);background:var(--fill);color:var(--muted);}
.vtag.sys{background:transparent;border:1px dashed var(--border);}
.vtag.sys::before{content:'';width:4px;height:4px;border-radius:50%;background:var(--faint);flex:none;}
.vtag.hit{background:var(--accent-soft);color:var(--accent);border-style:solid;border-color:var(--accent);}
.vtag.hit::before{background:var(--accent);}
.vtag-none{font-size:11.5px;color:var(--faint);font-style:italic;}
```

- [ ] **Step 7: Check the syntax**

```bash
node --check static/kestrel.js
```
Expected: exit 0, no output.

- [ ] **Step 8: Verify in the browser**

Start the app, open the library, and confirm **all** of:

1. The **Type** segmented control is gone; Status and Sort remain and still work.
2. No Match/Drill badge on any thumbnail.
3. A video with match results shows a dashed **Match** chip under its status chip; one with
   posture results shows **Drill**; one with both shows **both**; an unanalysed one shows
   neither, and shows "no tags" if it has none.
4. Switching language changes Match → 比赛 while any user tags stay exactly as typed.
5. Typing `match` in the search box finds match videos; so does `比赛` with the UI in Chinese.

**Point 3 is the whole refactor** — if a video with both analyses shows only one chip,
`sysTagsOf` is wrong. Record the actual result; do not assume.

- [ ] **Step 9: Commit**

```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(library): show tags on cards, absorb the Type filter"
```

---

### Task 7: The tag filter row

**Files:**
- Modify: `static/kestrel.js` — new render function plus a call in `renderDashboard` (`:105-121`)
- Modify: `static/kestrel.css` — chip row styles

**Interfaces:**
- Consumes: `tagsOf`, `tagLabel`, `isSysTag` (Task 6); `lib.filter.tags`.
- Produces: `renderTagBar()`, and a `<div class="tag-bar">` in the dashboard markup that
  Task 9's Manage button attaches to.

- [ ] **Step 1: Add the tag bar to the dashboard markup**

In `renderDashboard` (line 120), between the filter bar and the cards:

```javascript
      '<div class="filter-bar" id="filter-bar"></div>' +
      '<div class="tag-bar" id="tag-bar"></div>' +
      '<p class="result-line" id="result-line"></p>' +
      '<div id="cards" class="card-grid"></div>';
```

- [ ] **Step 2: Write the tag bar**

Add after `renderFilterBar`:

```javascript
  // Count shown on a chip is PROJECTED: how many videos would remain if this
  // tag were also applied. With AND semantics a plain total invites clicking
  // into an empty list; a projected count makes the dead end visible, and a
  // chip that would yield zero disables itself.
  function projectedCount(tag) {
    var f = lib.filter;
    return lib.videos.filter(function (v) {
      if (f.status !== 'all' && v.status !== f.status) return false;
      if (!f.tags.every(function (t) { return tagsOf(v).indexOf(t) !== -1; })) return false;
      return f.tags.indexOf(tag) !== -1 || tagsOf(v).indexOf(tag) !== -1;
    }).length;
  }
  function knownTags() {
    var counts = {};
    lib.videos.forEach(function (v) {
      tagsOf(v).forEach(function (t) { counts[t] = (counts[t] || 0) + 1; });
    });
    var keys = Object.keys(counts);
    return keys.filter(isSysTag).sort().concat(keys.filter(function (t) { return !isSysTag(t); }).sort());
  }
  function renderTagBar() {
    var bar = document.getElementById('tag-bar'); if (!bar) return;
    var zh = state.lang === 'zh';
    var tags = knownTags();
    var chips = '', sepDone = false;
    tags.forEach(function (t) {
      if (!isSysTag(t) && !sepDone && tags.some(isSysTag)) {
        chips += '<span class="tag-sep" aria-hidden="true"></span>'; sepDone = true;
      }
      var on = lib.filter.tags.indexOf(t) !== -1;
      var n = projectedCount(t);
      // aria-label is explicit: with a title attribute present the chip
      // announces the tooltip instead of its own name.
      var aria = tagLabel(t) + ', ' + n + ' ' + (zh ? '个视频' : (n === 1 ? 'video' : 'videos')) +
        (isSysTag(t) ? ', ' + (zh ? '来自分析结果' : 'from analysis results') : '');
      chips += '<button class="tchip' + (isSysTag(t) ? ' sys' : '') + (on ? ' on' : '') +
        (!on && n === 0 ? ' zero' : '') + '" data-tag="' + t + '" role="switch" aria-checked="' + on +
        '" aria-label="' + aria + '"' + (!on && n === 0 ? ' disabled' : '') + '>' +
        tagLabel(t) + '<span class="n">' + n + '</span></button>';
    });
    bar.innerHTML = (tags.length
      ? '<span class="tag-bar-label">' + (zh ? '标签' : 'Tags') + '</span>' +
        '<div class="tag-chips" role="group" aria-label="' + (zh ? '按标签筛选' : 'Filter by tags') + '">' + chips + '</div>'
      : '') +
      '<div class="tag-bar-actions">' +
        (lib.filter.tags.length ? '<button class="linkish" id="t-clear">' + (zh ? '清除标签' : 'Clear tags') + '</button>' : '') +
        '<button class="linkish" id="t-manage">' + (zh ? '管理…' : 'Manage…') + '</button></div>';

    bar.querySelectorAll('.tchip').forEach(function (b) {
      b.onclick = function () {
        var t = b.getAttribute('data-tag'), i = lib.filter.tags.indexOf(t);
        if (i === -1) lib.filter.tags.push(t); else lib.filter.tags.splice(i, 1);
        renderTagBar(); renderCards(); renderResultLine();
      };
    });
    var clr = bar.querySelector('#t-clear');
    if (clr) clr.onclick = function () {
      lib.filter.tags = []; renderTagBar(); renderCards(); renderResultLine();
    };
  }
  function renderResultLine() {
    var el = document.getElementById('result-line'); if (!el) return;
    var zh = state.lang === 'zh', f = lib.filter, n = filteredVideos().length;
    if (!f.tags.length && !f.q) { el.innerHTML = '<b>' + n + '</b> ' + (zh ? '个视频' : 'videos'); return; }
    var bits = [];
    if (f.tags.length) bits.push((zh ? '同时包含 ' : 'all of ') +
      f.tags.map(function (t) { return '“' + tagLabel(t) + '”'; }).join(' + '));
    if (f.q) bits.push((zh ? '匹配 ' : 'matching ') + '“' + f.q + '”');
    el.innerHTML = '<b>' + n + '</b> / ' + lib.videos.length + ' — ' + bits.join(', ');
  }
```

- [ ] **Step 3: Call them wherever the cards are re-rendered**

In `renderDashboard`, after the existing `renderFilterBar();` call, add:

```javascript
    renderTagBar();
    renderResultLine();
```

And extend `applyFilters` (line 52) so search and status keep the chips and the count honest:

```javascript
  function applyFilters(patch) {
    Object.assign(lib.filter, patch); renderCards(); renderTagBar(); renderResultLine();
  }
```

- [ ] **Step 4: Add the styles**

Append to `static/kestrel.css`:

```css
.tag-bar{display:flex;align-items:flex-start;gap:10px;margin-bottom:8px;flex-wrap:wrap;}
.tag-bar-label{font-size:12px;color:var(--faint);padding-top:8px;white-space:nowrap;}
.tag-chips{display:flex;flex-wrap:wrap;gap:6px;flex:1 1 300px;align-items:center;}
.tchip{display:inline-flex;align-items:center;gap:6px;font:600 12px inherit;padding:6px 11px;
  min-height:32px;border-radius:var(--radius-pill);background:var(--fill);color:var(--muted);
  border:1px solid transparent;cursor:pointer;transition:background .15s,color .15s,border-color .15s;}
.tchip:hover{color:var(--ink);border-color:var(--border);}
.tchip:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.tchip .n{font-weight:500;color:var(--faint);font-size:11px;}
.tchip.on{background:var(--accent-soft);color:var(--accent);border-color:var(--accent);}
.tchip.on .n{color:var(--accent);}
.tchip.zero{opacity:.4;cursor:not-allowed;}
.tchip.sys{background:transparent;border:1px dashed var(--border);}
.tchip.sys::before{content:'';width:5px;height:5px;border-radius:50%;background:var(--faint);flex:none;}
.tchip.sys.on{background:var(--accent-soft);border-style:solid;border-color:var(--accent);}
.tchip.sys.on::before{background:var(--accent);}
.tag-sep{width:1px;align-self:stretch;background:var(--border);margin:2px 4px;}
.tag-bar-actions{display:flex;gap:8px;align-items:center;}
.linkish{background:none;border:none;color:var(--muted);font:600 12px inherit;cursor:pointer;
  padding:6px 8px;border-radius:var(--radius-sm);}
.linkish:hover{color:var(--accent);background:var(--accent-soft);}
.result-line{font-size:12px;color:var(--faint);margin:0 0 18px;min-height:18px;}
.result-line b{color:var(--ink);}
@media (max-width:560px){ .tag-bar-label{display:none;} }
```

- [ ] **Step 5: Check the syntax**

```bash
node --check static/kestrel.js
```
Expected: exit 0.

- [ ] **Step 6: Verify in the browser**

With at least two tagged videos (set them via `curl` or the store file directly, since the
editor lands in Task 8):

1. Selecting one chip narrows the list; the result line names the tag.
2. Selecting a second narrows further and shows **both** tags in the result line.
3. Chips that would produce zero read `0` and cannot be clicked.
4. `Clear tags` appears only when something is selected and resets the list.
5. Selecting **Match** does **not** zero **Drill** if any video has both analyses — they are
   not mutually exclusive, deliberately.
6. Keyboard: Tab reaches each chip, Space toggles it, the focus ring is visible.

- [ ] **Step 7: Commit**

```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(library): filter by multiple tags with projected counts"
```

---

### Task 8: The tag editor popover

**Files:**
- Modify: `static/kestrel.js` — a tag button in `renderCards`, plus the popover
- Modify: `static/kestrel.css` — popover styles

**Interfaces:**
- Consumes: `tagsOf`, `sysTagsOf`, `isSysTag`, `tagLabel`, `knownTags` (Tasks 6-7);
  `PUT /api/videos/<name>/tags` (Task 3).
- Produces: `openTagEditor(video, anchorEl)`.

- [ ] **Step 1: Add the tag button to the card**

In `renderCards`, replace the single `.vdel` button with both buttons wrapped for hover reveal:

```javascript
        '<span class="vicons">' +
          '<button class="vicon" data-tagbtn="' + nm + '" aria-label="' +
            (state.lang==='zh'?'编辑标签':'Edit tags') + '">' + ICON_TAG + '</button>' +
          '<button class="vicon" data-del="' + nm + '" aria-label="' +
            (state.lang==='zh'?'删除':'Delete') + '">' + ICON_DEL + '</button>' +
        '</span></div>' +
```

and define the icons next to `SYS_TAGS` (inline SVG, not emoji — emoji render per-platform and
cannot be themed; this replaces the existing 🗑):

```javascript
  var ICON_TAG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 13.4 12 22l-9-9V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"/><circle cx="7.5" cy="7.5" r="1.2" fill="currentColor" stroke="none"/></svg>';
  var ICON_DEL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>';
```

Wire the button in the existing `wrap.querySelectorAll('.vcard')` loop area:

```javascript
    wrap.querySelectorAll('[data-tagbtn]').forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();   // the card itself opens the video
        var name = b.getAttribute('data-tagbtn');
        var v = lib.videos.filter(function (x) { return x.name === name; })[0];
        if (v) openTagEditor(v, b);
      };
    });
```

> The existing `.vdel` handler selects on `[data-del]`, which the new markup preserves. Re-read
> that block before editing and keep its behaviour unchanged. Attach the new handler in the
> same place, right after the existing `wrap.querySelectorAll('[data-del]')` loop.
>
> The `.vdel` rules in `static/kestrel.css` (including `.vcard:hover .vdel,.vdel:focus-visible`
> near line 274) become dead once the button is `.vicon` inside `.vicons`. Delete them in this
> task rather than leaving unreachable CSS behind — grep `vdel` and remove every hit.

- [ ] **Step 2: Write the popover**

```javascript
  var openPop = null;
  function closePop() {
    if (!openPop) return;
    openPop.backdrop.remove(); openPop.el.remove(); openPop = null;
    document.removeEventListener('keydown', popKey);
  }
  function popKey(e) { if (e.key === 'Escape') closePop(); }

  function openTagEditor(video, anchor) {
    closePop();
    var zh = state.lang === 'zh';
    var backdrop = document.createElement('div');
    backdrop.className = 'pop-backdrop'; backdrop.onclick = closePop;
    var el = document.createElement('div'); el.className = 'pop';
    document.body.appendChild(backdrop); document.body.appendChild(el);
    openPop = { el: el, backdrop: backdrop };
    document.addEventListener('keydown', popKey);
    var r = anchor.getBoundingClientRect();
    el.style.top = (window.scrollY + r.bottom + 8) + 'px';
    el.style.left = Math.max(8, Math.min(window.scrollX + r.left - 130, window.innerWidth - 306)) + 'px';

    // The editor works on a copy and PUTs the whole list. On failure the copy
    // is discarded and the popover stays open, so a save error can never look
    // like a save.
    var draft = (video.tags || []).slice();

    function persist(next, onFail) {
      fetch('/api/videos/' + encodeURIComponent(video.name) + '/tags', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tags: next })
      }).then(function (res) { return res.json().then(function (d) { return { ok: res.ok, d: d }; }); })
        .then(function (out) {
          if (!out.ok) { onFail((out.d && out.d.error) || (zh ? '保存失败' : 'Save failed')); return; }
          draft = out.d.tags; video.tags = out.d.tags;
          draw(); renderCards(); renderTagBar(); renderResultLine();
        })
        .catch(function () { onFail(zh ? '保存失败' : 'Save failed'); });
    }

    function draw(warning) {
      var sys = sysTagsOf(video);
      el.innerHTML =
        '<h3>' + (zh ? '标签' : 'Tags') + '</h3><p class="sub">' + video.name + '</p>' +
        (sys.length ? '<div class="pop-sys">' + sys.map(function (t) {
            return '<span class="vtag sys">' + tagLabel(t) + '</span>'; }).join('') + '</div>' +
          '<p class="pop-sys-note">' + (zh ? '来自分析结果，不可编辑。' : 'From analysis results — not editable.') + '</p>' : '') +
        '<div class="pop-tags">' + (draft.length
          ? draft.map(function (t) {
              return '<span class="ptag">' + t + '<button data-rm="' + t + '" aria-label="' +
                (zh ? '移除 ' : 'Remove ') + t + '">×</button></span>'; }).join('')
          : '<span class="vtag-none">' + (zh ? '暂无' : 'none yet') + '</span>') + '</div>' +
        '<input id="tag-in" autocomplete="off" placeholder="' + (zh ? '添加标签…' : 'Add a tag…') +
          '" aria-label="' + (zh ? '添加标签' : 'Add a tag') + '">' +
        (warning ? '<p class="warn" role="alert">' + warning + '</p>' : '') +
        '<div id="sugg"></div>' +
        '<p class="pop-hint">' + (zh ? '回车添加 · Esc 关闭 · 立即保存' : 'Enter to add · Esc to close · saves immediately') + '</p>';

      el.querySelectorAll('[data-rm]').forEach(function (b) {
        b.onclick = function () {
          var t = b.getAttribute('data-rm');
          persist(draft.filter(function (x) { return x !== t; }),
                  function (msg) { draw(msg); });
        };
      });

      var input = el.querySelector('#tag-in'), sugg = el.querySelector('#sugg'), cursor = -1;
      function drawSugg() {
        var val = input.value.trim().toLowerCase();
        var pool = knownTags().filter(function (t) { return !isSysTag(t); })
          .filter(function (t) { return draft.indexOf(t) === -1; })
          .filter(function (t) { return !val || t.indexOf(val) !== -1; });
        var rows = pool.slice(0, 5).map(function (t, i) {
          return '<button data-add="' + t + '" class="' + (i === cursor ? 'cursor' : '') + '">' + t + '</button>'; });
        if (val && pool.indexOf(val) === -1 && !isSysTag(val)) {
          rows.push('<button data-add="' + val + '" class="new">' +
            (zh ? '新建 “' : 'Create “') + val + '”</button>');
        }
        sugg.innerHTML = rows.join(''); sugg.className = rows.length ? 'sugg' : '';
        sugg.querySelectorAll('[data-add]').forEach(function (b) {
          b.onclick = function () { add(b.getAttribute('data-add')); }; });
      }
      function add(t) {
        t = (t || '').trim().toLowerCase(); if (!t) return;
        if (isSysTag(t)) { draw(zh ? '保留名称：由分析结果决定。' : 'Reserved — this name is set by the analysis.'); el.querySelector('#tag-in').focus(); return; }
        if (draft.indexOf(t) !== -1) { input.value = ''; drawSugg(); return; }
        persist(draft.concat([t]), function (msg) { draw(msg); });
      }
      input.oninput = function () { cursor = -1; drawSugg(); };
      input.onkeydown = function (e) {
        var opts = sugg.querySelectorAll('[data-add]');
        if (e.key === 'ArrowDown') { e.preventDefault(); cursor = Math.min(cursor + 1, opts.length - 1); drawSugg(); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); cursor = Math.max(cursor - 1, -1); drawSugg(); }
        else if (e.key === 'Enter') {
          e.preventDefault();
          if (cursor >= 0 && opts[cursor]) add(opts[cursor].getAttribute('data-add'));
          else add(input.value);
        }
      };
      drawSugg(); input.focus();
    }
    draw();
  }
```

- [ ] **Step 3: Add the styles**

```css
.vicons{display:flex;gap:6px;align-self:flex-start;opacity:0;transition:opacity .15s;}
.vcard:hover .vicons,.vcard:focus-within .vicons{opacity:1;}
.vicon{width:30px;height:30px;display:grid;place-items:center;border:none;cursor:pointer;
  border-radius:var(--radius-sm);background:rgba(0,0,0,.45);color:#fff;}
.vicon:hover{background:rgba(0,0,0,.7);}
.vicon:focus-visible{outline:2px solid #fff;outline-offset:1px;opacity:1;}
.vicon svg{width:15px;height:15px;}
.pop-backdrop{position:fixed;inset:0;background:rgba(10,12,14,.35);z-index:40;}
.pop{position:absolute;z-index:50;width:290px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--radius-md);box-shadow:0 18px 44px rgba(20,22,26,.22);padding:14px;}
.pop h3{margin:0 0 4px;font-size:13px;}
.pop .sub{font-size:11.5px;color:var(--faint);margin:0 0 10px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}
.pop-sys{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:6px;}
.pop-sys-note{font-size:10.5px;color:var(--faint);margin:0 0 10px;}
.pop-tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px;min-height:26px;}
.ptag{display:inline-flex;align-items:center;gap:5px;font:600 11.5px inherit;
  padding:4px 6px 4px 10px;border-radius:var(--radius-pill);background:var(--accent-soft);color:var(--accent);}
.ptag button{border:none;background:none;color:inherit;cursor:pointer;font-size:14px;
  line-height:1;width:18px;height:18px;border-radius:50%;}
.ptag button:hover{background:rgba(0,0,0,.12);}
.pop input{width:100%;height:36px;padding:0 10px;background:var(--paper);border:1px solid var(--border);
  border-radius:var(--radius-sm);color:var(--ink);font:13px inherit;}
.pop input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft);outline:none;}
.sugg{margin-top:6px;border:1px solid var(--border);border-radius:var(--radius-sm);overflow:hidden;}
.sugg button{display:block;width:100%;text-align:left;border:none;background:var(--card);
  color:var(--ink);font:13px inherit;padding:8px 10px;cursor:pointer;}
.sugg button:hover,.sugg button.cursor{background:var(--accent-soft);color:var(--accent);}
.sugg .new{color:var(--muted);font-style:italic;}
.warn{margin-top:6px;font-size:11px;color:var(--bad);}
.pop-hint{font-size:11px;color:var(--faint);margin-top:8px;}
```

- [ ] **Step 4: Check the syntax**

```bash
node --check static/kestrel.js
```
Expected: exit 0.

- [ ] **Step 5: Verify in the browser**

1. Hover a card → tag and delete buttons appear; both are SVG, not emoji.
2. Click the tag button → popover opens anchored below it.
3. Type `sm` → suggestions filter; Enter adds; the chip appears on the card immediately.
4. Type a brand-new word → "Create …" appears; Enter adds it; it joins the filter row.
5. Click × on a chip → it disappears from the card.
6. Type `match` → refused inline with the reserved message; nothing is saved.
7. Reload the page → every change survived.
8. Escape and backdrop-click both close the popover.
9. Stop the Flask server and try to add a tag → an error appears **in the popover** and the
   popover stays open. It must not close as though it saved.
10. **Keyboard only, no mouse:** Tab to a card — the tag and delete buttons must become
    visible via `:focus-within`, not stay hidden behind `:hover`. Tab to the tag button,
    press Enter, add a tag with the keyboard, then Escape out. A control that is reachable
    but invisible is a defect, not a pass.

- [ ] **Step 6: Commit**

```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(library): edit a video's tags from its card"
```

---

### Task 9: Manage tags — rename, delete, undo

**Files:**
- Modify: `static/kestrel.js` — the `#t-manage` handler and the modal
- Modify: `static/kestrel.css` — modal and toast styles

**Interfaces:**
- Consumes: `knownTags`, `isSysTag`, `tagLabel`, `closePop`, `popKey` (Tasks 6-8);
  `POST /api/tags/rename` and `DELETE /api/tags/<tag>` (Task 4).
- Produces: `openTagManager()`, `showToast(msg, undoFn)`.

- [ ] **Step 1: Wire the Manage button**

In `renderTagBar`, after the `#t-clear` handler:

```javascript
    var mng = bar.querySelector('#t-manage');
    if (mng) mng.onclick = openTagManager;
```

- [ ] **Step 2: Write the modal**

```javascript
  function reloadLibrary() {
    return fetch('/api/videos').then(function (r) { return r.json(); }).then(function (rows) {
      lib.videos = rows || [];
      // Drop filters for tags that no longer exist anywhere.
      var live = knownTags();
      lib.filter.tags = lib.filter.tags.filter(function (t) { return live.indexOf(t) !== -1; });
      renderCards(); renderTagBar(); renderResultLine();
    });
  }

  function openTagManager() {
    closePop();
    var zh = state.lang === 'zh';
    var backdrop = document.createElement('div'); backdrop.className = 'pop-backdrop';
    backdrop.onclick = closePop;
    var el = document.createElement('div'); el.className = 'modal';
    document.body.appendChild(backdrop); document.body.appendChild(el);
    openPop = { el: el, backdrop: backdrop };
    document.addEventListener('keydown', popKey);
    var editing = null, confirming = null, problem = null;

    function counts() {
      var c = {};
      lib.videos.forEach(function (v) { tagsOf(v).forEach(function (t) { c[t] = (c[t] || 0) + 1; }); });
      return c;
    }
    function draw() {
      var c = counts(), tags = knownTags();
      var sys = tags.filter(isSysTag), usr = tags.filter(function (t) { return !isSysTag(t); });
      function row(t) {
        if (editing === t) {
          return '<div class="trow"><input id="ren" value="' + t + '" aria-label="' +
            (zh ? '重命名 ' : 'Rename ') + t + '">' +
            '<button class="tbtn" data-save="' + t + '">' + (zh ? '保存' : 'Save') + '</button>' +
            '<button class="tbtn" data-cancel="1">' + (zh ? '取消' : 'Cancel') + '</button></div>';
        }
        if (isSysTag(t)) {
          return '<div class="trow sys"><span class="nm">' + tagLabel(t) + '</span>' +
            '<span class="ct">' + c[t] + '</span>' +
            '<span class="why">' + (zh ? '自动' : 'derived') + '</span></div>';
        }
        return '<div class="trow"><span class="nm">' + t + '</span><span class="ct">' + c[t] + '</span>' +
          '<button class="tbtn" data-ren="' + t + '">' + (zh ? '重命名' : 'Rename') + '</button>' +
          '<button class="tbtn danger" data-del="' + t + '">' + (zh ? '删除' : 'Delete') + '</button></div>';
      }
      el.innerHTML = '<h2>' + (zh ? '管理标签' : 'Manage tags') + '</h2>' +
        '<p class="sub">' + (zh ? '重命名或删除会应用到所有使用该标签的视频。'
                                : 'Renaming or deleting applies to every video that uses the tag.') + '</p>' +
        (sys.length ? '<div class="grp">' + (zh ? '来自分析' : 'From analysis') + '</div>' + sys.map(row).join('') : '') +
        (usr.length ? '<div class="grp">' + (zh ? '自定义标签' : 'Your tags') + '</div>' + usr.map(row).join('')
                    : '<p class="muted">' + (zh ? '暂无自定义标签' : 'No tags yet') + '</p>') +
        (problem ? '<p class="warn" role="alert">' + problem + '</p>' : '') +
        (confirming ? '<div class="confirm">' + (zh ? '删除 ' : 'Delete ') + '<b>' + confirming + '</b>' +
          (zh ? '？视频本身不受影响。' : '? The videos themselves are not touched.') +
          '<div class="modal-foot"><button class="btn-ghost" data-cancel="1">' + (zh ? '取消' : 'Cancel') + '</button>' +
          '<button class="btn-primary" data-confirm="' + confirming + '">' + (zh ? '删除' : 'Delete') + '</button></div></div>' : '') +
        '<div class="modal-foot"><button class="btn-ghost" data-close="1">' + (zh ? '完成' : 'Done') + '</button></div>';

      el.querySelectorAll('[data-ren]').forEach(function (b) {
        b.onclick = function () {
          editing = b.getAttribute('data-ren'); confirming = null; problem = null; draw();
          var i = el.querySelector('#ren'); if (i) { i.focus(); i.select(); }
        };
      });
      el.querySelectorAll('[data-save]').forEach(function (b) {
        b.onclick = function () {
          var from = b.getAttribute('data-save');
          var to = (el.querySelector('#ren').value || '').trim().toLowerCase();
          if (!to || to === from) { editing = null; draw(); return; }
          fetch('/api/tags/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ from: from, to: to }) })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (o) {
              if (!o.ok) { problem = (o.d && o.d.error) || (zh ? '重命名失败' : 'Rename failed'); draw(); return; }
              editing = null; problem = null;
              lib.filter.tags = lib.filter.tags.map(function (t) { return t === from ? to : t; })
                .filter(function (t, i, a) { return a.indexOf(t) === i; });
              reloadLibrary().then(draw);
            })
            .catch(function () { problem = zh ? '重命名失败' : 'Rename failed'; draw(); });
        };
      });
      el.querySelectorAll('[data-del]').forEach(function (b) {
        b.onclick = function () { confirming = b.getAttribute('data-del'); editing = null; problem = null; draw(); };
      });
      el.querySelectorAll('[data-confirm]').forEach(function (b) {
        b.onclick = function () {
          var t = b.getAttribute('data-confirm');
          // Snapshot for undo BEFORE the request: restoring means re-PUTting
          // the tag onto exactly the videos that had it.
          var had = lib.videos.filter(function (v) { return (v.tags || []).indexOf(t) !== -1; })
            .map(function (v) { return { name: v.name, tags: (v.tags || []).slice() }; });
          fetch('/api/tags/' + encodeURIComponent(t), { method: 'DELETE' })
            .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
            .then(function (o) {
              if (!o.ok) { problem = (o.d && o.d.error) || (zh ? '删除失败' : 'Delete failed'); draw(); return; }
              confirming = null; problem = null;
              lib.filter.tags = lib.filter.tags.filter(function (x) { return x !== t; });
              reloadLibrary().then(draw);
              showToast('“' + t + '” ' + (zh ? '已删除' : 'deleted'), function () {
                Promise.all(had.map(function (s) {
                  return fetch('/api/videos/' + encodeURIComponent(s.name) + '/tags',
                    { method: 'PUT', headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ tags: s.tags }) });
                })).then(reloadLibrary);
              });
            })
            .catch(function () { problem = zh ? '删除失败' : 'Delete failed'; draw(); });
        };
      });
      el.querySelectorAll('[data-cancel]').forEach(function (b) {
        b.onclick = function () { editing = null; confirming = null; problem = null; draw(); }; });
      el.querySelectorAll('[data-close]').forEach(function (b) { b.onclick = closePop; });
    }
    draw();
  }

  var toastTimer = null;
  function showToast(msg, undoFn) {
    clearTimeout(toastTimer);
    var old = document.querySelector('.toast'); if (old) old.remove();
    var t = document.createElement('div');
    t.className = 'toast'; t.setAttribute('role', 'status');
    t.innerHTML = '<span></span><button>' + (state.lang === 'zh' ? '撤销' : 'Undo') + '</button>';
    t.querySelector('span').textContent = msg;   // textContent: a tag is user input
    document.body.appendChild(t);
    t.querySelector('button').onclick = function () { t.remove(); if (undoFn) undoFn(); };
    toastTimer = setTimeout(function () { t.remove(); }, 6000);
  }
```

- [ ] **Step 3: Add the styles**

```css
.modal{position:fixed;z-index:50;top:50%;left:50%;transform:translate(-50%,-50%);
  width:min(460px,92vw);max-height:86vh;overflow:auto;background:var(--card);
  border:1px solid var(--border);border-radius:var(--radius-lg);
  box-shadow:0 24px 60px rgba(20,22,26,.3);padding:20px;}
.modal h2{margin:0 0 4px;font-size:17px;}
.modal .sub{font-size:12px;color:var(--faint);margin:0 0 14px;}
.grp{font-size:11px;font-weight:700;color:var(--faint);text-transform:uppercase;
  letter-spacing:.04em;margin:14px 0 2px;}
.trow{display:flex;align-items:center;gap:10px;padding:9px 0;border-top:1px solid var(--border);}
.trow .nm{flex:1;font-weight:600;font-size:13px;display:flex;align-items:center;gap:6px;}
.trow.sys .nm::before{content:'';width:5px;height:5px;border-radius:50%;background:var(--faint);}
.trow .ct{font-size:11.5px;color:var(--faint);}
.trow .why{font-size:11px;color:var(--faint);font-style:italic;}
.trow input{flex:1;height:32px;padding:0 9px;background:var(--paper);border:1px solid var(--accent);
  border-radius:var(--radius-sm);color:var(--ink);font:13px inherit;}
.tbtn{border:none;background:var(--fill);color:var(--muted);font:600 11.5px inherit;
  padding:6px 10px;border-radius:var(--radius-sm);cursor:pointer;min-height:30px;}
.tbtn:hover{color:var(--ink);}
.tbtn.danger:hover{color:var(--bad);background:rgba(229,72,77,.12);}
.modal-foot{display:flex;justify-content:flex-end;gap:8px;margin-top:16px;}
.confirm{background:rgba(229,72,77,.1);border:1px solid var(--bad);border-radius:var(--radius-sm);
  padding:10px 12px;font-size:12.5px;margin-top:10px;}
.confirm b{color:var(--bad);}
.toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:60;
  background:var(--ink);color:var(--paper);border-radius:var(--radius-sm);padding:11px 14px;
  font-size:13px;display:flex;align-items:center;gap:14px;box-shadow:0 12px 30px rgba(0,0,0,.3);}
.toast button{background:none;border:none;color:var(--accent);font:700 13px inherit;cursor:pointer;}
```

- [ ] **Step 4: Check the syntax**

```bash
node --check static/kestrel.js
```
Expected: exit 0.

- [ ] **Step 5: Verify in the browser**

1. `Manage…` opens the modal; system tags are grouped separately with no Rename/Delete.
2. Rename a user tag → every card using it updates; reload confirms it stuck.
3. Rename onto an existing tag → they merge, no duplicate chip on any card.
4. Delete a tag → confirmation first, then it vanishes everywhere and a toast appears.
5. Click **Undo** → the tag returns on exactly the videos that had it. Reload confirms.
6. Let the toast expire, reload → the deletion is permanent.
7. Escape closes the modal.

**Point 5 is the one to check hardest** — undo re-PUTs each affected video, so a video that had
other tags must keep them.

- [ ] **Step 6: Commit**

```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(library): rename, delete and undo tags library-wide"
```

---

## Final verification

- [ ] **Full suite, including the flaky continuity file**

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --basetemp "$TEMP/tags-final"
```
Expected: all pass. If `tests/test_ai_handoff.py` fails, re-run it alone to confirm it is the
known batch flake — and say so rather than assuming it.

- [ ] **Nothing forbidden staged**

```bash
git status --short
```
Expected: clean apart from the pre-existing untracked `BirdEye Prototype.html`. No
`data/library_tags.json`, no `outputs/`, no `*.pt`.

- [ ] **End-to-end pass against the wireframe's flows**

Walk the six flows listed in the wireframe's "What to try" panel against the real app. Anywhere
the app and the wireframe disagree, one of them is wrong — record which, do not quietly follow
the wireframe.

---

## What this plan does NOT deliver

Stated so no status line overclaims:

1. **No JavaScript unit tests.** The repo has no JS runner, and this plan does not add one.
   Tasks 6-9 are verified by `node --check` plus the scripted browser passes above. Treat UI
   behaviour as hand-verified, not covered.
2. **Status is not absorbed into tags** (spec D6). Only Type is.
3. **No tag editing from the results page** (spec D3 chose the card).
4. **No bulk tagging, colours, groups, or hierarchies** (spec §11).
5. **Tags do not survive a fresh clone** — `data/` is gitignored, like `videos/` and
   `outputs/`. They are local to this machine and there is no backup mechanism.
6. **Concurrent edits in two browser tabs.** Originally planned as accepted-not-locked
   (spec R2, single-user local app). A lost-update race found during browser verification
   silently reverted an Undo, so this shipped with a lock instead: every tag write (`PUT`
   tags, rename, delete-tag, the video-delete cleanup) and the `/api/videos` read now holds
   `app._TAGS_LOCK` for its whole load-mutate-save span. Two tabs no longer lose an edit to
   each other; two separate OS processes or machines sharing the file still could, but that
   is not this app's deployment shape.
