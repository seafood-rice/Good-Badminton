# Delete Management (Scoped Deletes + Preview Modal) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users permanently delete a video's artifacts at five scopes (match results / drill results / clips / reports / everything incl. the source video) from the Kestrel UI, behind a preview modal that lists exactly what will be removed, with server-side scope resolution that also closes the legacy delete route's path-traversal hole.

**Architecture:** One server-side resolver (`_delete_groups`) maps `(stem, scope)` → disjoint groups of filesystem paths; a preview route and a delete route both consume it, so the modal's list always matches what gets removed. Stem validation (`_safe_stem`) gates every delete surface including the legacy `DELETE /api/output/...` route. The frontend adds one modal component (`openDeleteModal`) plus wire-in buttons on library cards and both results screens.

**Tech Stack:** Python 3 / Flask + pathlib/shutil (backend), vanilla JS + CSS custom properties (frontend), pytest (backend tests).

## Global Constraints

- Bilingual **zh/en**, default `zh`; every user-facing string localized in the frontend. (Spec.)
- **Offline-safe:** no CDN/external references. (Spec.)
- Only `app.py`, `static/kestrel.js`, `static/kestrel.css`, and `tests/test_app_delete.py` (new) are touched. Old UI at `/` keeps working: the legacy `DELETE /api/output/<video>/<subpath>` route must keep succeeding for valid inputs while rejecting malformed stems and out-of-tree subpaths with 400.
- Theme tokens only; the danger button uses `var(--bad)`; accent and score hues constant across themes. (Spec.)
- **Deletes are permanent** — no trash/undo. The modal is the only confirmation.
- **Scopes are locked** (spec): `match` = everything directly under `outputs/<stem>/` EXCEPT `posture/` and `thumb.jpg`; `posture` = the whole `outputs/<stem>/posture/` dir; `clips` = `outputs/<stem>/clips/` + `outputs/<stem>/posture/rep_clips/`; `reports` = `outputs/<stem>/posture/coach_report_*`; `all` = entire `outputs/<stem>/` + source video `videos/<stem>.*` + court template `templates/_auto_<stem>*`.
- Preview rows are **disjoint** (no double counting), keys fixed: `all` → `source`,`match`,`posture`,`thumb`; `match` → `match`; `posture` → `posture`; `clips` → `clips_rally`,`clips_rep`; `reports` → `reports`. Zero-file rows omitted.
- `409` when any non-terminal analysis job (status `pending`/`running`/`reencoding`) matches the stem (match jobs: `job_id == stem` or `job['video_name']` filename whose stem matches; posture jobs: `job_id == 'posture_' + stem` or `job['video_name'] == stem`).
- **All existing tests stay green** (baseline **165**). Backend tasks are TDD via `./.venv/Scripts/python.exe -m pytest` (Windows; `PYTHONUTF8=1` when launching `app.py`). No JS test framework — frontend verified with `node --check static/kestrel.js` + running app + curl; browser dogfood happens at the controller level after the last task.
- Windows server cleanup: kill by PID (`for pid in $(netstat -ano | grep ":5096" | grep LISTENING | awk '{print $5}' | sort -u); do taskkill //F //PID "$pid"; done`), never bare `kill`.

---

### Task 1: Backend helpers — stem validation, scope resolver, stats, job guard (TDD)

**Files:**
- Modify: `app.py` (four module-level helpers, placed after `_rep_clip_window`)
- Test: `tests/test_app_delete.py` (create)

**Interfaces:**
- Produces (Task 2 consumes):
  - `_DELETE_SCOPES = ('match', 'posture', 'clips', 'reports', 'all')`
  - `_safe_stem(video_name) -> str | None` — None if empty/non-str, contains `/` `\` `..`, has surrounding whitespace, or names nothing that exists (no `outputs/<stem>/` dir and no `videos/<stem>.*` file).
  - `_delete_groups(stem, scope) -> list[tuple[str, list[Path]]]` — disjoint display groups (keys per Global Constraints); paths are files or directories; empty lists allowed.
  - `_paths_stats(paths) -> tuple[int, float]` — (file count incl. recursive dir contents, size in MB rounded to 1dp).
  - `_running_job_for(stem) -> str | None` — id of a matching non-terminal job.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_delete.py`:

```python
import app as webapp


def _tree(tmp_path, monkeypatch, stem="vid1"):
    videos = tmp_path / "videos"
    outputs = tmp_path / "outputs"
    templates = tmp_path / "templates"
    for d in (videos, outputs, templates):
        d.mkdir()
    (videos / (stem + ".mp4")).write_bytes(b"v" * 10)
    out = outputs / stem
    out.mkdir()
    (out / "detections.jsonl").write_text("{}", encoding="utf-8")
    (out / "rally_segments.json").write_text("{}", encoding="utf-8")
    (out / "thumb.jpg").write_bytes(b"t")
    clips = out / "clips"
    clips.mkdir()
    (clips / "highlight_1.mp4").write_bytes(b"c" * 20)
    posture = out / "posture"
    posture.mkdir()
    (posture / "drill_summary.json").write_text("{}", encoding="utf-8")
    (posture / "coach_report_en.json").write_text("{}", encoding="utf-8")
    (posture / "coach_report_zh-Hans.html").write_text("x", encoding="utf-8")
    rep = posture / "rep_clips"
    rep.mkdir()
    (rep / "rep_1.mp4").write_bytes(b"r" * 30)
    (templates / ("_auto_" + stem + ".png")).write_bytes(b"p")
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", outputs)
    monkeypatch.setattr(webapp, "TEMPLATES", templates)
    return videos, outputs, templates


def test_safe_stem_accepts_existing(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    assert webapp._safe_stem("vid1") == "vid1"


def test_safe_stem_accepts_video_without_outputs(tmp_path, monkeypatch):
    videos, outputs, _ = _tree(tmp_path, monkeypatch)
    (videos / "fresh.mp4").write_bytes(b"x")
    assert webapp._safe_stem("fresh") == "fresh"


def test_safe_stem_rejects_malformed(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    for bad in ("", "..", "../vid1", "a/b", "a\\b", " vid1", "vid1 ", ".", "C:", "c:", None):
        assert webapp._safe_stem(bad) is None, bad


def test_safe_stem_rejects_unknown(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    assert webapp._safe_stem("ghost") is None


def test_delete_groups_match_keeps_posture_and_thumb(tmp_path, monkeypatch):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    groups = dict(webapp._delete_groups("vid1", "match"))
    names = {p.name for p in groups["match"]}
    assert "posture" not in names and "thumb.jpg" not in names
    assert {"detections.jsonl", "rally_segments.json", "clips"} <= names


def test_delete_groups_clips_and_reports(tmp_path, monkeypatch):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    clips = dict(webapp._delete_groups("vid1", "clips"))
    assert [p.name for p in clips["clips_rally"]] == ["clips"]
    assert [p.name for p in clips["clips_rep"]] == ["rep_clips"]
    reports = dict(webapp._delete_groups("vid1", "reports"))
    assert sorted(p.name for p in reports["reports"]) == [
        "coach_report_en.json", "coach_report_zh-Hans.html"]


def test_delete_groups_all_disjoint_and_complete(tmp_path, monkeypatch):
    videos, outputs, templates = _tree(tmp_path, monkeypatch)
    groups = dict(webapp._delete_groups("vid1", "all"))
    all_paths = [p for paths in groups.values() for p in paths]
    assert len(all_paths) == len(set(all_paths))
    covered = set()
    for p in all_paths:
        if p.is_dir():
            covered |= {f for f in p.rglob("*") if f.is_file()}
        else:
            covered.add(p)
    expected = {f for f in (outputs / "vid1").rglob("*") if f.is_file()}
    expected.add(videos / "vid1.mp4")
    expected.add(templates / "_auto_vid1.png")
    assert covered == expected


def test_delete_groups_all_excludes_sibling_stem_files(tmp_path, monkeypatch):
    videos, outputs, templates = _tree(tmp_path, monkeypatch)
    (videos / "vid10.mp4").write_bytes(b"x")
    (templates / "_auto_vid10.png").write_bytes(b"p")
    groups = dict(webapp._delete_groups("vid1", "all"))
    assert templates / "_auto_vid10.png" not in groups["source"]
    assert videos / "vid10.mp4" not in groups["source"]


def test_paths_stats_counts(tmp_path, monkeypatch):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    files, size_mb = webapp._paths_stats([outputs / "vid1" / "clips"])
    assert files == 1
    assert size_mb == 0.0


def test_running_job_for(tmp_path, monkeypatch):
    _tree(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "jobs", {})
    assert webapp._running_job_for("vid1") is None
    monkeypatch.setitem(webapp.jobs, "vid1", {"status": "running", "video_name": "vid1.mp4"})
    assert webapp._running_job_for("vid1") == "vid1"
    webapp.jobs["vid1"]["status"] = "completed"
    assert webapp._running_job_for("vid1") is None
    monkeypatch.setitem(webapp.jobs, "posture_vid1", {"status": "running", "video_name": "vid1"})
    assert webapp._running_job_for("vid1") == "posture_vid1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_delete.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_safe_stem'`.

- [ ] **Step 3: Add the helpers to `app.py`** (immediately after `_rep_clip_window`)

```python
_DELETE_SCOPES = ('match', 'posture', 'clips', 'reports', 'all')


def _safe_stem(video_name):
    """Validated video stem for delete operations. None if malformed or unknown."""
    if not video_name or not isinstance(video_name, str):
        return None
    if '/' in video_name or '\\' in video_name or '..' in video_name:
        return None
    if video_name != video_name.strip():
        return None
    if Path(video_name).name != video_name:
        return None
    if (OUTPUTS / video_name).is_dir():
        return video_name
    if VIDEOS.exists() and any(p.is_file() and p.stem == video_name for p in VIDEOS.iterdir()):
        return video_name
    return None


def _delete_groups(stem, scope):
    """Disjoint (key, [paths]) groups a delete scope removes. Paths are files or dirs."""
    out = OUTPUTS / stem
    posture = out / 'posture'

    def match_paths():
        if not out.is_dir():
            return []
        return [p for p in out.iterdir() if p.name not in ('posture', 'thumb.jpg')]

    def source_paths():
        files = []
        if VIDEOS.exists():
            files += [p for p in VIDEOS.iterdir() if p.is_file() and p.stem == stem]
        if TEMPLATES.exists():
            files += [p for p in TEMPLATES.iterdir()
                      if p.is_file() and p.stem == '_auto_' + stem]
        return files

    if scope == 'match':
        return [('match', match_paths())]
    if scope == 'posture':
        return [('posture', [posture] if posture.is_dir() else [])]
    if scope == 'clips':
        rally = out / 'clips'
        rep = posture / 'rep_clips'
        return [('clips_rally', [rally] if rally.is_dir() else []),
                ('clips_rep', [rep] if rep.is_dir() else [])]
    if scope == 'reports':
        reports = ([p for p in posture.iterdir() if p.name.startswith('coach_report_')]
                   if posture.is_dir() else [])
        return [('reports', reports)]
    if scope == 'all':
        thumb = out / 'thumb.jpg'
        return [('source', source_paths()),
                ('match', match_paths()),
                ('posture', [posture] if posture.is_dir() else []),
                ('thumb', [thumb] if thumb.is_file() else [])]
    return []


def _paths_stats(paths):
    """(file count, size MB rounded 1dp) for files and recursive dir contents."""
    files = 0
    size = 0
    for p in paths:
        if p.is_dir():
            for f in p.rglob('*'):
                if f.is_file():
                    files += 1
                    size += f.stat().st_size
        elif p.is_file():
            files += 1
            size += p.stat().st_size
    return files, round(size / 1024 / 1024, 1)


def _running_job_for(stem):
    """Id of any non-terminal analysis job working on this video, else None."""
    active = ('pending', 'running', 'reencoding')
    for job_id, job in jobs.items():
        if job.get('status') not in active:
            continue
        jv = str(job.get('video_name') or '')
        if (job_id == stem or job_id == 'posture_' + stem
                or jv == stem or jv.rsplit('.', 1)[0] == stem):
            return job_id
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_delete.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `175 passed` (165 baseline + 10 new).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_delete.py
git commit -m "feat(api): delete-scope resolver, stem validation, stats + job guard helpers"
```

---

### Task 2: Backend routes — preview, delete, legacy hardening (TDD)

**Files:**
- Modify: `app.py` (two new routes after `api_posture_rep_clip`; harden the existing `delete_output` route)
- Test: `tests/test_app_delete.py` (append 7 tests)

**Interfaces:**
- Consumes: Task 1 helpers (exact names above).
- Produces (Task 3/4 consume):
  - `GET /api/delete-preview/<video_name>?scope=<scope>` → `200 {ok:true, scope, groups:[{key,files,size_mb}], total_files, total_size_mb}` | `400 {error}` (bad stem or scope).
  - `POST /api/delete/<video_name>` body `{scope}` → `200 {ok:true, scope, deleted_files}` | `400 {error}` | `409 {error}` (job running) | `500 {error}`.
  - Legacy `DELETE /api/output/<video_name>/<subpath>`: 400 on bad stem or subpath escaping `outputs/<stem>/`; unchanged 200 `{ok:true}` behavior otherwise.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_app_delete.py`)

```python
import pytest


@pytest.fixture
def client():
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client()


def test_preview_math(tmp_path, monkeypatch, client):
    _tree(tmp_path, monkeypatch)
    r = client.get("/api/delete-preview/vid1?scope=all")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True and d["scope"] == "all"
    keys = [g["key"] for g in d["groups"]]
    assert keys == ["source", "match", "posture", "thumb"]
    assert d["total_files"] == sum(g["files"] for g in d["groups"])
    assert d["total_files"] == 10  # 2 source + 3 match (2 files + 1 clip) + 4 posture + 1 thumb


def test_preview_rejects_bad_input(tmp_path, monkeypatch, client):
    _tree(tmp_path, monkeypatch)
    assert client.get("/api/delete-preview/vid1?scope=nope").status_code == 400
    assert client.get("/api/delete-preview/ghost?scope=all").status_code == 400


def test_delete_match_removes_exactly_scope(tmp_path, monkeypatch, client):
    videos, outputs, templates = _tree(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "jobs", {})
    r = client.post("/api/delete/vid1", json={"scope": "match"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    out = outputs / "vid1"
    assert (out / "posture").is_dir() and (out / "thumb.jpg").is_file()
    assert not (out / "detections.jsonl").exists()
    assert not (out / "clips").exists()
    assert (videos / "vid1.mp4").is_file()


def test_delete_all_removes_source_and_outputs(tmp_path, monkeypatch, client):
    videos, outputs, templates = _tree(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "jobs", {})
    r = client.post("/api/delete/vid1", json={"scope": "all"})
    assert r.status_code == 200
    assert r.get_json()["deleted_files"] == 10
    assert not (outputs / "vid1").exists()
    assert not (videos / "vid1.mp4").exists()
    assert not (templates / "_auto_vid1.png").exists()


def test_delete_409_while_job_running(tmp_path, monkeypatch, client):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    monkeypatch.setattr(webapp, "jobs", {"vid1": {"status": "running", "video_name": "vid1.mp4"}})
    r = client.post("/api/delete/vid1", json={"scope": "all"})
    assert r.status_code == 409
    assert (outputs / "vid1").exists()


def test_legacy_delete_rejects_bad_stem_and_escape(tmp_path, monkeypatch, client):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    secret = tmp_path / "secret.txt"
    secret.write_text("keep", encoding="utf-8")
    r = client.delete("/api/output/ghost/anything")
    assert r.status_code == 400
    r2 = client.delete("/api/output/vid1/..%2f..%2fsecret.txt")
    assert r2.status_code in (400, 404)
    assert secret.exists()


def test_legacy_delete_still_works_for_valid_subpath(tmp_path, monkeypatch, client):
    _, outputs, _ = _tree(tmp_path, monkeypatch)
    r = client.delete("/api/output/vid1/clips")
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert not (outputs / "vid1" / "clips").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_delete.py -v`
Expected: the 7 new tests FAIL (404 on the new routes; legacy tests fail on missing validation), the 9 Task 1 tests still PASS.

- [ ] **Step 3: Add the routes** (place after `api_posture_rep_clip` in `app.py`)

```python
@app.route('/api/delete-preview/<video_name>')
def api_delete_preview(video_name):
    """What a delete scope would remove: disjoint groups with file counts + sizes."""
    scope = request.args.get('scope', '')
    stem = _safe_stem(video_name)
    if stem is None or scope not in _DELETE_SCOPES:
        return jsonify({'error': '无效的删除请求'}), 400
    rows = []
    total_files = 0
    total_size = 0.0
    for key, paths in _delete_groups(stem, scope):
        files, size_mb = _paths_stats(paths)
        if files:
            rows.append({'key': key, 'files': files, 'size_mb': size_mb})
            total_files += files
            total_size += size_mb
    return jsonify({'ok': True, 'scope': scope, 'groups': rows,
                    'total_files': total_files, 'total_size_mb': round(total_size, 1)})


@app.route('/api/delete/<video_name>', methods=['POST'])
def api_delete(video_name):
    """Permanently delete a scope's files. 409 while an analysis job runs on the video."""
    data = request.get_json(silent=True) or {}
    scope = data.get('scope', '')
    stem = _safe_stem(video_name)
    if stem is None or scope not in _DELETE_SCOPES:
        return jsonify({'error': '无效的删除请求'}), 400
    if _running_job_for(stem):
        return jsonify({'error': '该视频正在分析中，请等待完成后再删除'}), 409
    deleted = 0
    try:
        for _key, paths in _delete_groups(stem, scope):
            for p in paths:
                if p.is_dir():
                    deleted += sum(1 for f in p.rglob('*') if f.is_file())
                    shutil.rmtree(str(p))
                elif p.is_file():
                    p.unlink()
                    deleted += 1
    except Exception:
        return jsonify({'error': '删除失败'}), 500
    return jsonify({'ok': True, 'scope': scope, 'deleted_files': deleted})
```

- [ ] **Step 4: Harden the legacy route**

Replace the body of the existing `delete_output(video_name, subpath)` route with:

```python
    if _safe_stem(video_name) is None:
        return jsonify({'error': '无效的删除请求'}), 400
    base = (OUTPUTS / video_name).resolve()
    filepath = (OUTPUTS / video_name / subpath).resolve()
    try:
        filepath.relative_to(base)
    except ValueError:
        return jsonify({'error': '无效的删除请求'}), 400
    if filepath.is_dir():
        shutil.rmtree(str(filepath))
    elif filepath.exists():
        filepath.unlink()
    return jsonify({'ok': True})
```

(The docstring and decorator stay as they are.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_delete.py -v`
Expected: PASS (17 tests).

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `182 passed` (175 + 7). If any pre-existing test exercised `delete_output` with a stem that no longer validates, adapt THAT test's fixture to create the video/output first (report it if so).

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_app_delete.py
git commit -m "feat(api): scoped delete-preview + delete routes; harden legacy delete against traversal"
```

---

### Task 3: Delete modal component (frontend)

**Files:**
- Modify: `static/kestrel.js` (modal + labels; place after `loadCoachReport`, before `openResults`)
- Modify: `static/kestrel.css` (modal + danger button + card-trash styles)

**Interfaces:**
- Consumes: `GET /api/delete-preview/<stem>?scope=`, `POST /api/delete/<stem>` (Task 2 shapes).
- Produces (Task 4 consumes): `openDeleteModal(stem, scope, onDone)` — opens the modal, previews, deletes on confirm, then calls `onDone()`; `closeDeleteModal()`. CSS classes `.modal-overlay/.modal-card/.modal-row/.btn-danger/.vdel`.

- [ ] **Step 1: Add the modal code**

```js
  var DELETE_SCOPE_TITLES = {
    all: ['删除整个视频', 'Delete entire video'],
    match: ['删除比赛结果', 'Delete match results'],
    posture: ['删除训练结果', 'Delete drill results'],
    clips: ['删除生成的剪辑', 'Delete generated clips'],
    reports: ['删除教练报告', 'Delete coach reports']
  };
  var DELETE_GROUP_LABELS = {
    source: ['源视频与球场模板', 'Source video & court template'],
    match: ['比赛分析结果（含回合剪辑）', 'Match results (incl. rally clips)'],
    posture: ['训练分析结果（含逐次剪辑与报告）', 'Drill results (incl. rep clips & reports)'],
    thumb: ['缩略图', 'Thumbnail'],
    clips_rally: ['回合剪辑', 'Rally clips'],
    clips_rep: ['逐次剪辑', 'Rep clips'],
    reports: ['教练报告', 'Coach reports']
  };
  var _delGen = 0;
  var _delBusy = false;
  function _delEsc(e) { if (e.key === 'Escape' && !_delBusy) { closeDeleteModal(); } }
  function closeDeleteModal() {
    _delGen += 1;
    _delBusy = false;
    var m = document.getElementById('del-modal');
    if (m) { m.remove(); }
    document.removeEventListener('keydown', _delEsc);
  }
  function openDeleteModal(stem, scope, onDone) {
    closeDeleteModal();
    var gen = _delGen;
    var zh = state.lang === 'zh';
    var title = DELETE_SCOPE_TITLES[scope];
    var wrap = document.createElement('div');
    wrap.id = 'del-modal';
    wrap.className = 'modal-overlay';
    wrap.innerHTML =
      '<div class="modal-card" role="dialog" aria-modal="true">' +
        '<h3 class="modal-title">' + (zh ? title[0] : title[1]) + '</h3>' +
        '<div class="modal-sub mono">' + stem + '</div>' +
        '<div id="del-rows" class="modal-rows muted">' + (zh?'加载中…':'Loading…') + '</div>' +
        '<div id="del-err" class="modal-err"></div>' +
        '<div class="modal-actions">' +
          '<button class="btn-ghost" id="del-cancel">' + (zh?'取消':'Cancel') + '</button>' +
          '<button class="btn-danger" id="del-confirm" disabled>' + (zh?'永久删除':'Delete permanently') + '</button>' +
        '</div></div>';
    document.body.appendChild(wrap);
    wrap.onclick = function (e) { if (!_delBusy && e.target === wrap) { closeDeleteModal(); } };
    document.addEventListener('keydown', _delEsc);
    var cancelBtn = document.getElementById('del-cancel');
    var confirmBtn = document.getElementById('del-confirm');
    cancelBtn.onclick = function () { if (!_delBusy) { closeDeleteModal(); } };
    cancelBtn.focus();
    fetch('/api/delete-preview/' + stem + '?scope=' + scope)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (gen !== _delGen) { return; }
        var rows = document.getElementById('del-rows'); if (!rows) { return; }
        if (!d || !d.ok) { rows.textContent = zh?'预览失败':'Preview failed'; return; }
        if (!d.total_files) { rows.textContent = zh?'没有可删除的文件':'Nothing to delete'; return; }
        rows.classList.remove('muted');
        rows.innerHTML = d.groups.map(function (g) {
          var lbl = DELETE_GROUP_LABELS[g.key];
          return '<div class="modal-row"><span>' + (lbl ? (zh?lbl[0]:lbl[1]) : g.key) + '</span>' +
            '<span class="mono">' + g.files + (zh?' 个文件':' files') + ' · ' + g.size_mb + 'MB</span></div>';
        }).join('') +
          '<div class="modal-row modal-total"><span>' + (zh?'合计':'Total') + '</span>' +
          '<span class="mono">' + d.total_files + (zh?' 个文件':' files') + ' · ' + d.total_size_mb + 'MB</span></div>';
        confirmBtn.disabled = false;
      }).catch(function () {
        if (gen !== _delGen) { return; }
        var rows = document.getElementById('del-rows');
        if (rows) { rows.textContent = zh?'预览失败':'Preview failed'; }
      });
    confirmBtn.onclick = function () {
      _delBusy = true;
      confirmBtn.disabled = true;
      cancelBtn.disabled = true;
      confirmBtn.textContent = zh?'删除中…':'Deleting…';
      var err0 = document.getElementById('del-err');
      if (err0) { err0.textContent = ''; }
      fetch('/api/delete/' + stem, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: scope }) })
        .then(function (r) { return r.json().then(function (d) { return { s: r.status, d: d }; }); })
        .then(function (res) {
          if (gen !== _delGen) { return; }
          if (res.d && res.d.ok) { closeDeleteModal(); onDone(); return; }
          _delBusy = false;
          confirmBtn.disabled = false;
          cancelBtn.disabled = false;
          confirmBtn.textContent = zh?'永久删除':'Delete permanently';
          var err = document.getElementById('del-err');
          if (err) {
            err.textContent = res.s === 409
              ? (zh?'该视频正在分析中，请等待完成后再删除':'Analysis is running — wait for it to finish')
              : (zh?'删除失败':'Delete failed');
          }
        })
        .catch(function () {
          if (gen !== _delGen) { return; }
          _delBusy = false;
          confirmBtn.disabled = false;
          cancelBtn.disabled = false;
          confirmBtn.textContent = zh?'永久删除':'Delete permanently';
          var err = document.getElementById('del-err');
          if (err) { err.textContent = zh?'网络错误，请重试':'Network error — please try again'; }
        });
    };
  }
```

- [ ] **Step 2: Add the CSS** (append to `static/kestrel.css`)

```css
.modal-overlay{position:fixed;inset:0;background:rgba(10,12,14,.55);display:flex;align-items:center;
  justify-content:center;z-index:100;}
.modal-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-md);
  padding:22px 24px;width:min(420px,90vw);box-shadow:0 12px 40px rgba(0,0,0,.25);}
.modal-title{margin:0 0 4px;font-size:16px;color:var(--ink);}
.modal-sub{font-size:12px;color:var(--faint);margin-bottom:14px;}
.modal-rows{margin-bottom:8px;font-size:13px;}
.modal-row{display:flex;justify-content:space-between;gap:16px;padding:6px 0;
  border-bottom:1px solid var(--border);color:var(--muted);}
.modal-row span:first-child{color:var(--ink);}
.modal-total{border-bottom:none;font-weight:700;}
.modal-err{color:var(--bad);font-size:12.5px;min-height:16px;margin-bottom:6px;}
.modal-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:8px;}
.btn-danger{background:var(--bad);color:#fff;border:none;border-radius:var(--radius-sm);
  padding:10px 18px;font:700 14px inherit;cursor:pointer;}
.btn-danger:disabled{opacity:.5;cursor:default;}
.vdel{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.45);border:none;
  border-radius:var(--radius-sm);color:#fff;padding:4px 8px;cursor:pointer;opacity:0;transition:opacity .15s;}
.vcard:hover .vdel,.vdel:focus-visible{opacity:1;}
```

- [ ] **Step 3: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start `PYTHONUTF8=1 ./.venv/Scripts/python.exe app.py --port 5096` (background); `curl -s http://127.0.0.1:5096/static/kestrel.js | grep -c "openDeleteModal\|btn-danger\|DELETE_GROUP_LABELS"` → non-zero; `curl -s "http://127.0.0.1:5096/api/delete-preview/IMG_1270?scope=reports"` → `{ok:true,...}` JSON. Kill server by PID.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): delete confirmation modal with per-group preview"
```

---

### Task 4: Wire delete actions into library + results screens

**Files:**
- Modify: `static/kestrel.js` (card trash button; danger buttons on both results screens; `afterModeDelete` helper)
- Modify: `static/kestrel.css` (danger-link ghost style)

**Interfaces:**
- Consumes: `openDeleteModal(stem, scope, onDone)` (Task 3); `state.resultsVideo/resultsMode/resultsModes`, `openResults`, `setScreen`, `loadDashboard`, `renderResults` (existing).
- Produces: user-facing delete entry points. `afterModeDelete(deletedMode)` navigates to the other mode or the library.

- [ ] **Step 1: Library card trash button**

In `renderCards`, the card template's thumb section currently reads:
```js
      return '<div class="vcard" role="button" tabindex="0" data-name="' + nm + '"><div class="vthumb" style="' + thumb + '">' +
        '<span class="vmode mono">' + modeLabel(v) + '</span>' +
        '<span class="vdur mono">' + dur + '</span></div>' +
```
Replace with:
```js
      return '<div class="vcard" role="button" tabindex="0" data-name="' + nm + '"><div class="vthumb" style="' + thumb + '">' +
        '<span class="vmode mono">' + modeLabel(v) + '</span>' +
        '<span class="vdur mono">' + dur + '</span>' +
        '<button class="vdel" data-del="' + nm + '" aria-label="' + (state.lang==='zh'?'删除':'Delete') + '">🗑</button></div>' +
```
After the existing `.vcard` click-wiring block in `renderCards`, add:
```js
    wrap.querySelectorAll('.vdel').forEach(function (b) {
      b.onclick = function (e) {
        e.stopPropagation();
        var name = b.getAttribute('data-del');
        openDeleteModal(name, 'all', function () {
          if (state.resultsVideo === name) { state.resultsVideo = null; }
          loadDashboard();
        });
      };
    });
```

- [ ] **Step 2: `afterModeDelete` helper** (place right after `openDeleteModal`)

```js
  function afterModeDelete(deletedMode) {
    var remaining = (state.resultsModes || []).filter(function (m) { return m !== deletedMode; });
    if (remaining.length) { openResults(state.resultsVideo, remaining[0], remaining); }
    else { state.resultsVideo = null; setScreen('dashboard'); }
  }
```

- [ ] **Step 3: Match results wiring**

In `renderMatchResults`, the `body.innerHTML` assignment currently ends with:
```js
      '<div class="res-section" id="plan-box"></div>';
```
Change to:
```js
      '<div class="res-section" id="plan-box"></div>' +
      '<div class="res-section"><button class="btn-ghost danger-link" id="match-del">🗑 ' + (zh?'删除比赛结果':'Delete match results') + '</button></div>';
```
In the same function, the rally box currently contains:
```js
          '<button class="btn-primary" id="clip-btn" disabled>' + (zh?'生成回合剪辑':'Generate clips') + '</button>' +
          '<div id="clip-list" class="clip-list"></div></div></div></div>' +
```
Change to:
```js
          '<button class="btn-primary" id="clip-btn" disabled>' + (zh?'生成回合剪辑':'Generate clips') + '</button> ' +
          '<button class="btn-ghost danger-link" id="clips-del">🗑 ' + (zh?'删除剪辑':'Delete clips') + '</button>' +
          '<div id="clip-list" class="clip-list"></div></div></div></div>' +
```
After the `renderPlanPanel(stem, 'match');` line, add:
```js
    document.getElementById('match-del').onclick = function () {
      openDeleteModal(stem, 'match', function () { afterModeDelete('match'); });
    };
    document.getElementById('clips-del').onclick = function () {
      openDeleteModal(stem, 'clips', function () { renderResults(); });
    };
```

- [ ] **Step 4: Posture results wiring**

In `renderPostureResults`, the `body.innerHTML` assignment currently ends with:
```js
      '<div class="res-section" id="report-box"></div>';
```
Change to:
```js
      '<div class="res-section" id="report-box"></div>' +
      '<div class="res-section"><button class="btn-ghost danger-link" id="posture-del">🗑 ' + (zh?'删除训练结果':'Delete drill results') + '</button></div>';
```
After the existing `renderCoachReport(stem);` line, add:
```js
    document.getElementById('posture-del').onclick = function () {
      openDeleteModal(stem, 'posture', function () { afterModeDelete('posture'); });
    };
```
In `renderCoachReport`, the header currently contains:
```js
        '<span id="report-dl"></span></div>' +
```
Change to:
```js
        '<span id="report-dl"></span>' +
        '<button class="btn-ghost danger-link" id="report-del">🗑 ' + (zh?'删除报告':'Delete reports') + '</button></div>' +
```
And after the `loadCoachReport(reportCtx.lang);` line at the end of `renderCoachReport`, add:
```js
    var rd = document.getElementById('report-del');
    if (rd) { rd.onclick = function () { openDeleteModal(stem, 'reports', function () { renderResults(); }); }; }
```

- [ ] **Step 5: Danger-link CSS** (append to `static/kestrel.css`)

```css
.danger-link{color:var(--bad);border-color:var(--bad);}
.danger-link:hover{color:#fff;background:var(--bad);border-color:var(--bad);}
```

- [ ] **Step 6: Verify + commit**

Run: `node --check static/kestrel.js` → exit 0.
Start app on 5096; `curl -s http://127.0.0.1:5096/static/kestrel.js | grep -c "afterModeDelete\|match-del\|posture-del\|clips-del\|report-del\|vdel"` → non-zero; `/kestrel` → 200. Kill server by PID.
```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): delete actions on library cards + results screens"
```

---

## Self-Review

- **Spec coverage:** five locked scopes with server-side resolver (T1) ✓; disjoint preview rows with fixed keys (T1/T2) ✓; preview + delete routes with 400/409/500 contract (T2) ✓; legacy route hardening incl. containment (T2) ✓; modal with per-group counts/sizes, disabled-empty state, Esc/backdrop/cancel, 409 wording, inline errors (T3) ✓; library card trash → scope `all` (T4) ✓; results-screen granular buttons + post-delete navigation (other mode or library; section refresh for clips/reports) (T4) ✓; bilingual throughout; permanence (no undo) ✓. Deferred per spec: trash/undo, bulk, per-item deletes.
- **Placeholder scan:** none — all steps carry full code/tests.
- **Type consistency:** `_safe_stem/_delete_groups/_paths_stats/_running_job_for/_DELETE_SCOPES` names match between T1 (producer) and T2 (consumer); route shapes `{ok, scope, groups[{key,files,size_mb}], total_files, total_size_mb}` and `{ok, scope, deleted_files}` match T3's consumption incl. 409 handling; group keys (`source/match/posture/thumb/clips_rally/clips_rep/reports`) identical between T1 resolver, T2 preview, and T3 `DELETE_GROUP_LABELS`; `openDeleteModal(stem, scope, onDone)` signature identical T3→T4; T4 mount-point strings copied from the current committed file.
- **Count math (fixture):** source=2 (video+template), match=3 (detections, rally, clips/highlight_1), posture=4 (drill_summary, 2 coach_report, rep_clips/rep_1), thumb=1 → total 10 ✓ (matches `test_preview_math` and `test_delete_all_removes_source_and_outputs`).

## Notes for the executor

- Base commit is `c23cf75` (spec commit).
- Suite baseline 165 → 175 after T1 → 182 after T2. Frontend tasks: `node --check` + curl; browser dogfood is a controller pass after T4 (on a scratch copy — do NOT delete the real IMG_1270/IMG_1537 fixtures during dogfood; use an uploaded copy or restore from backup).
- Windows: kill servers by PID (see Global Constraints), never bare `kill`.
- The legacy-route hardening may interact with existing tests that call `delete_output` — if any pre-existing test fails, adapt its fixture to create the video/outputs dir first and report the adaptation.
