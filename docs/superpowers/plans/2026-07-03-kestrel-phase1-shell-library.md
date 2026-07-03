# Kestrel Phase 1 — Shell, Design System & Video Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Kestrel UI foundation — a parallel light/dark themed shell with a sidebar and a card-based Video Library (stats, filters, first-frame thumbnails) — served at `/kestrel`, wired to enriched read-only endpoints, without touching the live app at `/`.

**Architecture:** Build new front-end assets (`kestrel.html` shell + `static/kestrel.css` + `static/kestrel.js` + `static/fonts` + `static/img`) served via Flask's built-in `/static/` handler, plus a temporary `@app.route('/kestrel')`. Enrich the existing read-only `/api/videos` and add `/api/stats` and first-frame thumbnail generation — all additive. The current `/` UI and the match/posture pipelines are untouched.

**Tech Stack:** Python 3 / Flask (backend), vanilla JS + CSS custom properties (frontend), OpenCV (`cv2`, already a dependency) for thumbnails, pytest for backend tests.

## Global Constraints

- Bilingual **zh/en**, default `zh`; every UI string via an i18n lookup. (Copied from spec.)
- **Offline-safe:** no CDN, no external network at load time. Fonts bundled locally. (Spec.)
- **Additive backend only:** do not modify match/posture pipeline logic; existing routes keep their current response shape plus new keys. (Spec.)
- **All 141 existing tests must stay green.** (Spec.)
- Theme tokens on `:root` (light) + `[data-theme="dark"]` override; **accent `#FF5A36` and score hues constant across themes**. (Spec.)
- Data/number labels use **IBM Plex Mono** (bundled woff2) with `ui-monospace` fallback; UI text uses the system sans stack. (Spec.)
- Frontend has **no JS test framework** in this repo; front-end tasks are verified manually in a browser against the running Flask app using existing `IMG_1270` (posture) and `IMG_1537` (match) outputs. Backend tasks are TDD with pytest.

---

### Task 1: Enrich `/api/videos` with library metadata

**Files:**
- Modify: `app.py:43-61` (`api_videos`)
- Test: `tests/test_app_library.py` (create)

**Interfaces:**
- Produces: each item in the `GET /api/videos` JSON array now also carries
  `date` (str, ISO date from file mtime), `duration_sec` (float or null),
  `thumb` (str URL or null), `has_match` (bool), `has_posture` (bool),
  `status` (str: `"analyzed"` | `"court_set"` | `"new"`). Existing keys
  (`name`, `filename`, `size_mb`, `has_result`, `has_annotations`,
  `output_dir`) are unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app_library.py
import json
import pytest
import app as webapp


@pytest.fixture
def client(tmp_path, monkeypatch):
    videos = tmp_path / "videos"
    videos.mkdir()
    monkeypatch.setattr(webapp, "VIDEOS", videos)
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "outputs")
    (tmp_path / "outputs").mkdir()
    webapp.app.config["TESTING"] = True
    return webapp.app.test_client(), videos, tmp_path / "outputs"


def test_videos_reports_status_and_metadata(client, monkeypatch):
    c, videos, outputs = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    # No duration decode in tests: stub the helper.
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: 12.5)
    out = outputs / "clip"
    out.mkdir(parents=True)
    (out / "detections.jsonl").write_text("{}\n", encoding="utf-8")

    data = c.get("/api/videos").get_json()
    row = next(r for r in data if r["name"] == "clip")
    assert row["has_match"] is True
    assert row["has_posture"] is False
    assert row["status"] == "analyzed"
    assert row["duration_sec"] == 12.5
    assert "date" in row and len(row["date"]) == 10  # YYYY-MM-DD


def test_videos_status_new_and_court_set(client, monkeypatch):
    c, videos, outputs = client
    (videos / "a.mp4").write_bytes(b"\x00")
    (videos / "b.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    (outputs / "b").mkdir(parents=True)
    (outputs / "b" / "court_annotations.txt").write_text("x", encoding="utf-8")

    rows = {r["name"]: r for r in c.get("/api/videos").get_json()}
    assert rows["a"]["status"] == "new"
    assert rows["b"]["status"] == "court_set"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_library.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute '_video_duration_sec'` / missing keys.

- [ ] **Step 3: Write minimal implementation**

Add a duration helper above `api_videos` and enrich the row. Replace `app.py:43-61`:

```python
def _video_duration_sec(path):
    """Best-effort video duration in seconds; None if it can't be read."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0 and frames > 0:
            return round(frames / fps, 1)
    except Exception:
        pass
    return None


@app.route('/api/videos')
def api_videos():
    """列出所有视频和对应的分析结果"""
    import datetime
    videos = []
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in sorted(VIDEOS.glob(ext)):
            name = p.stem
            out_dir = OUTPUTS / name
            has_match = (out_dir / 'detections.jsonl').exists()
            has_posture = (out_dir / 'posture' / 'drill_summary.json').exists()
            has_annotations = (out_dir / 'court_annotations.txt').exists()
            thumb_path = out_dir / 'thumb.jpg'
            if has_match or has_posture:
                status = 'analyzed'
            elif has_annotations:
                status = 'court_set'
            else:
                status = 'new'
            videos.append({
                'name': name,
                'filename': p.name,
                'size_mb': round(p.stat().st_size / 1024 / 1024, 1),
                'has_result': has_match,
                'has_annotations': has_annotations,
                'has_match': has_match,
                'has_posture': has_posture,
                'status': status,
                'date': datetime.date.fromtimestamp(p.stat().st_mtime).isoformat(),
                'duration_sec': _video_duration_sec(p),
                'thumb': f'/api/output/{name}/thumb.jpg' if thumb_path.exists() else None,
                'output_dir': str(out_dir) if has_match else None,
            })
    return jsonify(videos)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_library.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `143 passed` (141 existing + 2 new).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_library.py
git commit -m "feat(api): enrich /api/videos with status, date, duration, thumb, mode flags"
```

---

### Task 2: First-frame thumbnail generation

**Files:**
- Modify: `app.py` (`api_upload` at `:64-75`; add `_ensure_thumbnail` helper; lazy backfill in `api_videos`)
- Test: `tests/test_app_library.py` (extend)

**Interfaces:**
- Consumes: `_video_duration_sec` (Task 1).
- Produces: `_ensure_thumbnail(video_path, stem) -> Path | None` writes
  `OUTPUTS/<stem>/thumb.jpg` (first frame) and returns its path, or `None` on
  failure. `api_upload` calls it after saving; `api_videos` calls it lazily
  when `thumb.jpg` is missing so the `thumb` URL is populated.

- [ ] **Step 1: Write the failing test**

```python
def test_ensure_thumbnail_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(webapp, "OUTPUTS", tmp_path / "outputs")

    # Stub the frame grab so no real video decode is needed.
    def fake_write(video_path, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff")  # jpeg magic
        return True
    monkeypatch.setattr(webapp, "_write_first_frame", fake_write)

    out = webapp._ensure_thumbnail(tmp_path / "clip.mp4", "clip")
    assert out is not None and out.exists()


def test_videos_backfills_thumbnail(client, monkeypatch):
    c, videos, outputs = client
    (videos / "clip.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame",
                        lambda vp, dest: (dest.parent.mkdir(parents=True, exist_ok=True),
                                          dest.write_bytes(b"\xff\xd8\xff"), True)[-1])
    row = next(r for r in c.get("/api/videos").get_json() if r["name"] == "clip")
    assert row["thumb"] == "/api/output/clip/thumb.jpg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_library.py -k thumbnail -v`
Expected: FAIL — `_write_first_frame` / `_ensure_thumbnail` not defined.

- [ ] **Step 3: Write minimal implementation**

Add helpers near `_video_duration_sec`:

```python
def _write_first_frame(video_path, dest):
    """Grab frame 0 of the video and save a JPEG at dest. Returns bool."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return False
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / max(w, 1))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        return bool(cv2.imwrite(str(dest), frame))
    except Exception:
        return False


def _ensure_thumbnail(video_path, stem):
    """Ensure OUTPUTS/<stem>/thumb.jpg exists; return its Path or None."""
    dest = OUTPUTS / stem / 'thumb.jpg'
    if dest.exists():
        return dest
    if _write_first_frame(video_path, dest):
        return dest
    return None
```

In `api_upload`, after `file.save(...)` (before the return), add:

```python
    _ensure_thumbnail(save_path, save_path.stem)
```

In `api_videos`, before computing `thumb_path.exists()`, add a lazy backfill:

```python
            if not thumb_path.exists():
                _ensure_thumbnail(p, name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_library.py -v`
Expected: PASS (all).

- [ ] **Step 5: Full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (145 total).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_library.py
git commit -m "feat(api): generate first-frame thumbnails on upload + lazy backfill"
```

---

### Task 3: `/api/stats` dashboard aggregation

**Files:**
- Modify: `app.py` (add `api_stats` route near `api_videos`)
- Test: `tests/test_app_library.py` (extend)

**Interfaces:**
- Produces: `GET /api/stats` → JSON
  `{"videos": int, "analyzed": int, "rallies": int, "avg_technique_score": float|null}`.
  `analyzed` counts videos with match or posture output; `rallies` sums
  `rallies` arrays in each `rally_segments.json`; `avg_technique_score` is the
  mean of per-video technique averages from `technique_summary.json`
  (`by_type[*].avg_score`), or `null` when none exist.

- [ ] **Step 1: Write the failing test**

```python
def test_stats_aggregates_outputs(client, monkeypatch):
    c, videos, outputs = client
    (videos / "m.mp4").write_bytes(b"\x00")
    (videos / "n.mp4").write_bytes(b"\x00")
    monkeypatch.setattr(webapp, "_video_duration_sec", lambda p: None)
    monkeypatch.setattr(webapp, "_write_first_frame", lambda vp, d: False)

    m = outputs / "m"
    m.mkdir(parents=True)
    (m / "detections.jsonl").write_text("{}\n", encoding="utf-8")
    (m / "rally_segments.json").write_text(
        json.dumps({"rallies": [{}, {}, {}]}), encoding="utf-8")
    (m / "technique_summary.json").write_text(
        json.dumps({"by_type": {"smash": {"avg_score": 70.0},
                                "clear": {"avg_score": 50.0}}}), encoding="utf-8")

    s = c.get("/api/stats").get_json()
    assert s["videos"] == 2
    assert s["analyzed"] == 1
    assert s["rallies"] == 3
    assert s["avg_technique_score"] == 60.0


def test_stats_empty(client, monkeypatch):
    c, videos, outputs = client
    s = c.get("/api/stats").get_json()
    assert s == {"videos": 0, "analyzed": 0, "rallies": 0, "avg_technique_score": None}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_library.py -k stats -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Write minimal implementation**

```python
@app.route('/api/stats')
def api_stats():
    """Dashboard tiles: counts + aggregate scores across all outputs."""
    count = analyzed = rallies = 0
    score_sum = score_n = 0.0
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in VIDEOS.glob(ext):
            count += 1
            out = OUTPUTS / p.stem
            has_match = (out / 'detections.jsonl').exists()
            has_posture = (out / 'posture' / 'drill_summary.json').exists()
            if has_match or has_posture:
                analyzed += 1
            rf = out / 'rally_segments.json'
            if rf.exists():
                try:
                    with open(rf, encoding='utf-8') as f:
                        rallies += len(json.load(f).get('rallies', []))
                except Exception:
                    pass
            tf = out / 'technique_summary.json'
            if tf.exists():
                try:
                    with open(tf, encoding='utf-8') as f:
                        by_type = json.load(f).get('by_type', {})
                    vals = [v['avg_score'] for v in by_type.values()
                            if isinstance(v, dict) and v.get('avg_score') is not None]
                    if vals:
                        score_sum += sum(vals) / len(vals)
                        score_n += 1
                except Exception:
                    pass
    avg = round(score_sum / score_n, 1) if score_n else None
    return jsonify({'videos': count, 'analyzed': analyzed,
                    'rallies': rallies, 'avg_technique_score': avg})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_app_library.py -k stats -v`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (147 total).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_app_library.py
git commit -m "feat(api): add /api/stats dashboard aggregation"
```

---

### Task 4: `/kestrel` dev route + static scaffold

**Files:**
- Modify: `app.py` (add `kestrel` route beside `index`)
- Create: `kestrel.html`, `static/kestrel.css`, `static/kestrel.js`,
  `static/img/kestrel-mark.svg`, `static/fonts/` (IBM Plex Mono woff2 files)

**Interfaces:**
- Produces: `GET /kestrel` returns `kestrel.html`. Flask's built-in
  `/static/<path>` serves `static/`. `kestrel.js` exposes `window.Kestrel`
  bootstrapping (extended in later tasks).

- [ ] **Step 1: Add the route**

In `app.py`, directly after the `index()` function:

```python
@app.route('/kestrel')
def kestrel():
    return (PROJECT_ROOT / 'kestrel.html').read_text(encoding='utf-8')
```

- [ ] **Step 2: Fetch IBM Plex Mono woff2 (latin) into `static/fonts/`**

Download the 3 weights (400/500/600), latin subset, to `static/fonts/`:

```bash
mkdir -p static/fonts static/img
for w in 400 500 600; do
  curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexmono/IBMPlexMono-$([ $w = 400 ] && echo Regular || ([ $w = 500 ] && echo Medium || echo SemiBold)).ttf" \
    -o "static/fonts/IBMPlexMono-$w.ttf"
done
ls -la static/fonts/
```
Expected: three `.ttf` files present. (woff2 conversion optional; `.ttf`
works offline. If conversion tooling is available, convert to `.woff2` and
update the `@font-face` `src` accordingly.)

- [ ] **Step 3: Create the logo mark**

`static/img/kestrel-mark.svg`:

```xml
<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><rect width="24" height="24" rx="6" fill="#FF5A36"/></svg>
```

- [ ] **Step 4: Create minimal `kestrel.html` shell**

```html
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kestrel</title>
  <link rel="stylesheet" href="/static/kestrel.css">
</head>
<body>
  <div id="app" class="app-shell">
    <aside id="sidebar"></aside>
    <main id="main"></main>
  </div>
  <script src="/static/kestrel.js"></script>
</body>
</html>
```

- [ ] **Step 5: Create `static/kestrel.css` with theme tokens + @font-face**

Define `:root` light tokens and `[data-theme="dark"]` overrides using the exact
values from the spec's design-system section (accent `#FF5A36`,
`--accent-dark`, `--accent-soft`, `--sidebar-bg:#14161A`, `--paper`, `--card`,
`--border`, `--fill`, `--ink`, `--muted`, `--faint`, `--good/--mid/--bad`,
`--radius-sm/md/lg/pill`, `--space`; dark values `--paper:#0F1114`,
`--card:#1A1D1F`, `--border:#2A2D33`, `--fill:#22262B`, `--ink:#F3F1EC`,
`--muted:#9A9EA6`, `--faint:#787D85`). Add three `@font-face` blocks for
`'IBM Plex Mono'` pointing at `/static/fonts/IBMPlexMono-400/500/600.ttf`, and
base resets:

```css
:root{
  --accent:#FF5A36; --accent-dark:#E64A28; --accent-soft:rgba(255,90,54,0.12);
  --sidebar-bg:#14161A;
  --paper:#FAFAF8; --card:#fff; --border:#EBE8E2; --fill:#F3F1EC;
  --ink:#1A1D1F; --muted:#6B7280; --faint:#8A8F98;
  --good:#3FAE6A; --mid:#E8A93B; --bad:#E5484D;
  --radius-sm:8px; --radius-md:12px; --radius-lg:18px; --radius-pill:999px;
  --space:1;
}
[data-theme="dark"]{
  --paper:#0F1114; --card:#1A1D1F; --border:#2A2D33; --fill:#22262B;
  --ink:#F3F1EC; --muted:#9A9EA6; --faint:#787D85;
}
@font-face{font-family:'IBM Plex Mono';font-weight:400;font-display:swap;
  src:url('/static/fonts/IBMPlexMono-400.ttf');}
@font-face{font-family:'IBM Plex Mono';font-weight:500;font-display:swap;
  src:url('/static/fonts/IBMPlexMono-500.ttf');}
@font-face{font-family:'IBM Plex Mono';font-weight:600;font-display:swap;
  src:url('/static/fonts/IBMPlexMono-600.ttf');}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--paper);color:var(--ink);}
.mono{font-family:'IBM Plex Mono',ui-monospace,monospace;}
.app-shell{display:flex;min-height:100vh;}
#main{flex:1;min-width:0;padding:calc(40px*var(--space)) calc(48px*var(--space));max-width:1440px;}
```

- [ ] **Step 6: Create `static/kestrel.js` bootstrap stub**

```js
window.Kestrel = (function () {
  'use strict';
  var state = { lang: localStorage.getItem('kestrel_lang') || 'zh',
                theme: localStorage.getItem('kestrel_theme') ||
                       (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') };
  function applyTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
  }
  function init() {
    applyTheme();
    document.getElementById('main').textContent = 'Kestrel shell OK';
  }
  document.addEventListener('DOMContentLoaded', init);
  return { state: state, applyTheme: applyTheme };
})();
```

- [ ] **Step 7: Browser verification**

Start the app and open the new route:

```bash
PYTHONUTF8=1 ./.venv/Scripts/python.exe app.py --port 5090 &
```
Open `http://127.0.0.1:5090/kestrel` — expect a themed blank shell showing
"Kestrel shell OK". Open `http://127.0.0.1:5090/` — expect the **existing** UI,
unchanged. In devtools, confirm `/static/kestrel.css`, `/static/kestrel.js`, and
one `/static/fonts/*.ttf` all return 200; console has no errors.

- [ ] **Step 8: Commit**

```bash
git add app.py kestrel.html static/kestrel.css static/kestrel.js static/img/kestrel-mark.svg static/fonts/
git commit -m "feat(ui): Kestrel shell scaffold at /kestrel with theme tokens + fonts"
```

---

### Task 5: Sidebar + i18n + theme/language toggles

**Files:**
- Modify: `static/kestrel.js` (i18n dictionary, sidebar render, toggles),
  `static/kestrel.css` (sidebar styles)

**Interfaces:**
- Consumes: `window.Kestrel.state`, `applyTheme` (Task 4).
- Produces: `Kestrel.t(key)` returns the string for the current language;
  `Kestrel.setLang(lang)` and `Kestrel.setTheme(theme)` persist to
  `localStorage` and re-render. `Kestrel.renderSidebar()` builds the sidebar
  with nav items `dashboard` and `new` (nav routing is a no-op stub here;
  wired in Task 6+).

- [ ] **Step 1: Add the i18n dictionary and helpers to `kestrel.js`**

Inside the module, before `init`:

```js
var T = {
  zh: { brand: 'Kestrel', portal: '教练平台', nav_dashboard: '视频库',
        nav_new: '新建分析', persona: 'Coach Lee', team: 'Team Falcons',
        theme_light: '浅色', theme_dark: '深色' },
  en: { brand: 'Kestrel', portal: 'Coach Portal', nav_dashboard: 'Dashboard',
        nav_new: 'New Analysis', persona: 'Coach Lee', team: 'Team Falcons',
        theme_light: 'Light', theme_dark: 'Dark' }
};
function t(key) { return (T[state.lang] && T[state.lang][key]) || key; }
function setLang(lang) { state.lang = lang; localStorage.setItem('kestrel_lang', lang);
  document.documentElement.lang = lang; render(); }
function setTheme(theme) { state.theme = theme; localStorage.setItem('kestrel_theme', theme);
  applyTheme(); renderSidebar(); }
```

- [ ] **Step 2: Add `renderSidebar()` and a `render()` dispatcher**

```js
function renderSidebar() {
  var s = document.getElementById('sidebar');
  s.className = 'sidebar';
  s.innerHTML =
    '<div class="brand"><img src="/static/img/kestrel-mark.svg" width="24" height="24">' +
      '<span class="brand-name">' + t('brand') + '</span></div>' +
    '<div class="portal">' + t('portal') + '</div>' +
    '<nav class="nav">' +
      '<button class="nav-item" data-screen="dashboard">' + t('nav_dashboard') + '</button>' +
      '<button class="nav-item" data-screen="new">' + t('nav_new') + '</button>' +
    '</nav><div class="spacer"></div>' +
    '<div class="side-toggles">' +
      '<button data-lang="zh">中</button><button data-lang="en">EN</button>' +
      '<button data-theme="light">' + t('theme_light') + '</button>' +
      '<button data-theme="dark">' + t('theme_dark') + '</button>' +
    '</div>' +
    '<div class="persona"><div class="avatar mono">KL</div>' +
      '<div><div class="persona-name">' + t('persona') + '</div>' +
      '<div class="persona-team">' + t('team') + '</div></div></div>';
  s.querySelectorAll('[data-lang]').forEach(function (b) {
    b.onclick = function () { setLang(b.getAttribute('data-lang')); }; });
  s.querySelectorAll('[data-theme]').forEach(function (b) {
    b.onclick = function () { setTheme(b.getAttribute('data-theme')); }; });
}
function render() { renderSidebar(); /* main screen wired in Task 6 */ }
```

Update `init()` to call `render()` instead of setting textContent, and set
`document.documentElement.lang = state.lang`. Export `t`, `setLang`, `setTheme`,
`renderSidebar` on the returned object.

- [ ] **Step 3: Style the sidebar in `kestrel.css`**

Add sidebar styles matching the prototype (dark 232px sticky column, brand row,
uppercase portal label, nav buttons, toggles row, persona footer). Reference
`BirdEye Prototype.html` structure. Minimum:

```css
.sidebar{width:232px;flex-shrink:0;background:var(--sidebar-bg);color:#fff;
  display:flex;flex-direction:column;padding:24px 14px;position:sticky;top:0;
  height:100vh;overflow-y:auto;}
.brand{display:flex;align-items:center;gap:10px;padding:6px 10px 2px;}
.brand-name{font-weight:800;font-size:17px;}
.portal{font-size:10.5px;color:#5F636B;text-transform:uppercase;
  letter-spacing:.08em;padding:0 10px 24px;}
.nav-item{display:block;width:100%;text-align:left;padding:10px 12px;height:40px;
  border:none;background:transparent;color:#9A9EA6;font:500 14px inherit;
  border-radius:var(--radius-sm);cursor:pointer;}
.nav-item:hover,.nav-item.active{color:#fff;background:var(--accent-soft);}
.spacer{flex:1;}
.side-toggles{display:flex;gap:6px;flex-wrap:wrap;padding:8px 10px;}
.side-toggles button{background:#22262B;color:#cbd0d6;border:none;border-radius:6px;
  padding:5px 9px;font-size:12px;cursor:pointer;}
.persona{display:flex;gap:10px;align-items:center;padding:12px 10px;
  border-top:1px solid rgba(255,255,255,.08);margin-top:8px;}
.avatar{width:34px;height:34px;border-radius:50%;background:#2A2D33;color:var(--accent);
  display:flex;align-items:center;justify-content:center;font-weight:700;}
.persona-name{font-size:13px;font-weight:600;color:#fff;}
.persona-team{font-size:11.5px;color:#787D85;}
```

- [ ] **Step 4: Browser verification**

Reload `http://127.0.0.1:5090/kestrel`. Expect the dark sidebar with brand,
nav, toggles, and persona. Click **EN/中** → nav labels switch language and
persist across reload. Click **Dark/Light** → surfaces flip and persist; the
accent stays orange. No console errors.

- [ ] **Step 5: Commit**

```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): Kestrel sidebar with i18n and theme/language toggles"
```

---

### Task 6: Video Library screen (stats, cards, filters)

**Files:**
- Modify: `static/kestrel.js` (dashboard screen render + filters + data fetch),
  `static/kestrel.css` (stat tiles, card grid, filter bar)

**Interfaces:**
- Consumes: `GET /api/stats`, `GET /api/videos` (Tasks 1–3); `t`, `render`
  (Task 5).
- Produces: `Kestrel.renderDashboard()` fetches stats + videos and paints the
  Library into `#main`; `Kestrel.applyFilters()` filters the in-memory video
  list by search text, mode (`all|match|posture`), status
  (`all|analyzed|court_set|new`), and sort (`date|name`).

- [ ] **Step 1: Add dashboard state + data load to `kestrel.js`**

```js
var lib = { videos: [], filter: { q: '', mode: 'all', status: 'all', sort: 'date' } };
function loadDashboard() {
  Promise.all([
    fetch('/api/stats').then(function (r) { return r.json(); }),
    fetch('/api/videos').then(function (r) { return r.json(); })
  ]).then(function (res) {
    lib.stats = res[0]; lib.videos = res[1] || []; renderDashboard();
  }).catch(function () {
    document.getElementById('main').innerHTML = '<p class="error">' +
      (state.lang === 'zh' ? '加载失败' : 'Failed to load') + '</p>';
  });
}
function filteredVideos() {
  var f = lib.filter;
  var out = lib.videos.filter(function (v) {
    if (f.q && v.name.toLowerCase().indexOf(f.q.toLowerCase()) === -1) return false;
    if (f.mode === 'match' && !v.has_match) return false;
    if (f.mode === 'posture' && !v.has_posture) return false;
    if (f.status !== 'all' && v.status !== f.status) return false;
    return true;
  });
  out.sort(function (a, b) {
    return f.sort === 'name' ? a.name.localeCompare(b.name)
                             : (b.date || '').localeCompare(a.date || '');
  });
  return out;
}
function applyFilters(patch) { Object.assign(lib.filter, patch); renderCards(); }
```

- [ ] **Step 2: Add `renderDashboard()`, `renderCards()`, badge helpers**

```js
function modeLabel(v) { return v.has_posture && !v.has_match ? (state.lang==='zh'?'训练':'Drill')
                                                             : (state.lang==='zh'?'比赛':'Match'); }
function statusChip(v) {
  var map = { analyzed:['已完成','Analyzed','var(--good)'],
              court_set:['已标注','Court set','var(--mid)'],
              new:['未分析','New','var(--faint)'] };
  var m = map[v.status] || map.new;
  return '<span class="chip" style="color:' + m[2] + '">' + (state.lang==='zh'?m[0]:m[1]) + '</span>';
}
function renderDashboard() {
  var s = lib.stats || {};
  var tiles = [['videos', state.lang==='zh'?'视频总数':'Videos'],
               ['analyzed', state.lang==='zh'?'已分析':'Analyzed'],
               ['rallies', state.lang==='zh'?'回合数':'Rallies'],
               ['avg_technique_score', state.lang==='zh'?'技术均分':'Avg Score']];
  var main = document.getElementById('main');
  main.innerHTML =
    '<div class="page-head"><h1>' + (state.lang==='zh'?'视频库':'Video Library') + '</h1></div>' +
    '<div class="stat-row">' + tiles.map(function (t2) {
      var val = s[t2[0]]; if (val === null || val === undefined) val = '—';
      return '<div class="stat"><div class="stat-label mono">' + t2[1] +
             '</div><div class="stat-val mono">' + val + '</div></div>';
    }).join('') + '</div>' +
    '<div class="filter-bar">' +
      '<input id="f-q" placeholder="' + (state.lang==='zh'?'搜索…':'Search…') + '">' +
      '<select id="f-mode"><option value="all">All</option><option value="match">Match</option><option value="posture">Drill</option></select>' +
      '<select id="f-status"><option value="all">All</option><option value="analyzed">Analyzed</option><option value="court_set">Court set</option><option value="new">New</option></select>' +
      '<select id="f-sort"><option value="date">Date</option><option value="name">Name</option></select>' +
    '</div><div id="cards" class="card-grid"></div>';
  main.querySelector('#f-q').oninput = function (e) { applyFilters({ q: e.target.value }); };
  main.querySelector('#f-mode').onchange = function (e) { applyFilters({ mode: e.target.value }); };
  main.querySelector('#f-status').onchange = function (e) { applyFilters({ status: e.target.value }); };
  main.querySelector('#f-sort').onchange = function (e) { applyFilters({ sort: e.target.value }); };
  renderCards();
}
function renderCards() {
  var wrap = document.getElementById('cards'); if (!wrap) return;
  var vids = filteredVideos();
  if (!vids.length) { wrap.innerHTML = '<p class="muted">' +
    (state.lang==='zh'?'暂无视频':'No videos') + '</p>'; return; }
  wrap.innerHTML = vids.map(function (v) {
    var thumb = v.thumb ? 'background-image:url(' + v.thumb + ')' : '';
    var dur = v.duration_sec ? Math.floor(v.duration_sec/60)+':'+('0'+Math.round(v.duration_sec%60)).slice(-2) : '';
    return '<div class="vcard"><div class="vthumb" style="' + thumb + '">' +
      '<span class="vmode mono">' + modeLabel(v) + '</span>' +
      '<span class="vdur mono">' + dur + '</span></div>' +
      '<div class="vbody"><div class="vname">' + v.name + '</div>' +
      '<div class="vdate mono">' + (v.date||'') + '</div>' +
      '<div class="vfoot">' + statusChip(v) + '</div></div></div>';
  }).join('');
}
```

Update `render()` to call `loadDashboard()` for the main area, and export
`renderDashboard`, `applyFilters`, `loadDashboard`.

- [ ] **Step 3: Style stat tiles, filter bar, and card grid in `kestrel.css`**

```css
.page-head h1{font-size:26px;font-weight:800;letter-spacing:-.02em;margin-bottom:24px;}
.stat-row{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:28px;}
.stat{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-md);padding:18px 20px;}
.stat-label{font-size:11.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.04em;}
.stat-val{font-size:28px;font-weight:600;margin-top:8px;color:var(--ink);}
.filter-bar{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap;}
.filter-bar input,.filter-bar select{background:var(--card);border:1px solid var(--border);
  border-radius:var(--radius-sm);padding:8px 12px;color:var(--ink);font:14px inherit;}
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px;}
.vcard{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-md);
  overflow:hidden;transition:box-shadow .2s,transform .2s;}
.vcard:hover{box-shadow:0 10px 26px rgba(20,22,26,.08);transform:translateY(-3px);}
.vthumb{height:130px;background:#222 center/cover;position:relative;display:flex;
  justify-content:space-between;padding:10px 12px;}
.vmode{font-size:11px;color:#fff;background:rgba(0,0,0,.4);padding:4px 9px;border-radius:var(--radius-pill);align-self:flex-start;}
.vdur{position:absolute;bottom:8px;right:10px;font-size:11px;color:#fff;}
.vbody{padding:14px 16px;}
.vname{font-size:14.5px;font-weight:700;}
.vdate{font-size:12px;color:var(--faint);margin-top:2px;}
.vfoot{margin-top:10px;}
.chip{font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:var(--radius-pill);background:var(--fill);}
.muted{color:var(--muted);} .error{color:var(--bad);}
```

- [ ] **Step 4: Browser verification**

Reload `http://127.0.0.1:5090/kestrel`. Expect: 4 stat tiles (populated from
existing `IMG_1270`/`IMG_1537` outputs), a filter bar, and video cards with
**first-frame thumbnails**, mode/status badges, dates, durations. Test each
filter (search "1270", mode=Drill, status=Analyzed, sort=Name) and confirm the
grid updates. Toggle language → labels/badges switch. Toggle dark → cards and
tiles recolor. Confirm `/` still serves the old UI.

- [ ] **Step 5: Commit**

```bash
git add static/kestrel.js static/kestrel.css
git commit -m "feat(ui): Kestrel Video Library — stats, filters, thumbnail cards"
```

---

## Self-Review

- **Spec coverage (Phase 1 rows):** design system + light/dark tokens + toggle
  (Tasks 4–5) ✓; sidebar + persona + language toggle (Task 5) ✓; Library with
  stats, filters, first-frame thumbnails, status/mode badges (Tasks 1–3, 6) ✓;
  parallel `/kestrel` non-regression build (Task 4) ✓; enriched `/api/videos`,
  `/api/stats`, thumbnail generation (Tasks 1–3) ✓. Deferred to later phases:
  wizard, progress, results, rep scrubber, training plan, exercise content,
  final `/` swap — correct for Phase 1.
- **Placeholder scan:** no TBD/TODO; all backend steps carry full test +
  implementation code; frontend steps carry concrete code and explicit browser
  checks (repo has no JS test framework — stated in Global Constraints).
- **Type consistency:** `_video_duration_sec`, `_write_first_frame`,
  `_ensure_thumbnail` names consistent across Tasks 1–2; `/api/videos` keys
  (`has_match`, `has_posture`, `status`, `date`, `duration_sec`, `thumb`) used
  consistently in Task 6's `filteredVideos`/`renderCards`; `/api/stats` keys
  (`videos`, `analyzed`, `rallies`, `avg_technique_score`) match Task 3 and the
  Task 6 tiles.

## Notes for the executor

- Use the venv Python: `./.venv/Scripts/python.exe` (Windows) with
  `PYTHONUTF8=1` when running `app.py` directly (the startup banner needs UTF-8).
- There is an **uncommitted pending change** in the working tree from a prior
  task (the `--analyze-technique` flag in `app.py` + `web_ui.html` restore-on-
  select + `tests/test_app_technique_routes.py`). Phase 1 does not depend on it
  and does not touch those lines. Commit or set it aside before starting so
  Phase 1 commits stay clean.
