#!/usr/bin/env python3
"""Good-Badminton Web 前端"""

import os, sys, json, time, subprocess, glob, shutil, threading
from pathlib import Path
from urllib.parse import quote
from flask import Flask, request, jsonify, send_file
from badminton_analysis.data.writer import write_json
from badminton_analysis.training.plan_generator import generate_plan
from badminton_analysis.library import tags as libtags
from badminton_analysis.library import athletes as libathletes
from badminton_analysis.insights.rally import summarise_rally_payload
from badminton_analysis.web import athletes_routes, insights_routes, job_slots, proc_control

PROJECT_ROOT = Path(__file__).resolve().parent
VIDEOS = PROJECT_ROOT / 'videos'
OUTPUTS = PROJECT_ROOT / 'outputs'
# Library tags live outside outputs/: /api/delete scope "all" removes
# outputs/<stem> whole, and clearing analysis results must not clear a
# video's labels. data/ is already gitignored.
TAGS_PATH = PROJECT_ROOT / 'data' / 'library_tags.json'
# Flask serves requests on multiple threads; without this lock, two concurrent
# tag writes race load -> mutate -> save and one can silently clobber the
# other. One global lock rather than per-video: rename/delete touch the whole
# file, so a per-stem lock would not prevent the cross-video race.
_TAGS_LOCK = threading.Lock()
# Athletes, squads and match records: the same reasoning as the tags (outside
# outputs/, gitignored), and one lock over every read-modify-write.
ATHLETES_PATH = PROJECT_ROOT / 'data' / 'athletes.json'
_ATHLETES_LOCK = threading.Lock()
TEMPLATES = PROJECT_ROOT / 'templates'
WEIGHTS = PROJECT_ROOT / 'weights'

VIDEOS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)
TEMPLATES.mkdir(exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024
# Getters, not values: read at request time so a re-pointed ATHLETES_PATH is honoured.
athletes_routes.register(app, store_path=lambda: ATHLETES_PATH,
                         lock=lambda: _ATHLETES_LOCK,
                         safe_stem=lambda name: _safe_stem(name))
insights_routes.register(app, videos=lambda: VIDEOS, outputs=lambda: OUTPUTS,
                         athletes_path=lambda: ATHLETES_PATH,
                         athletes_lock=lambda: _ATHLETES_LOCK,
                         jobs=lambda: jobs,
                         safe_stem=lambda name: _safe_stem(name))

# ── Job store ────────────────────────────────────────────────────────────
jobs = {}
# Guards check-then-register of a job id (two clicks on the same video must not
# both pass the busy check and start two runs writing the same output folder),
# and every move of a job out of 'pending', so a queue-message update can never
# land on a job that has just started or been cancelled.
_JOBS_LOCK = threading.Lock()
# 'paused': frozen in place, holding no slot. 'resuming': paused and waiting for a slot.
_PAUSED_JOB_STATUSES = ('paused', 'resuming')
_ACTIVE_JOB_STATUSES = ('pending', 'running', 'reencoding') + _PAUSED_JOB_STATUSES
_JOB_BUSY = {'error': '该视频已在分析或排队中 / this video is already being analysed or queued'}


def _queue_changed(order):
    """Keep each queued job's message on its current place in line."""
    with _JOBS_LOCK:
        for index, job_id in enumerate(order):
            job = jobs.get(job_id)
            if job is None:
                continue
            if job.get('status') == 'pending':
                job['message'] = f'排队中，第 {index + 1} 位 / queued, #{index + 1} in line'
            elif job.get('status') == 'resuming':
                job['message'] = (f'等待空闲分析位以继续，第 {index + 1} 位 / '
                                  f'waiting for a free slot to resume, #{index + 1} in line')


# At most this many analyses (match or posture) run at once; the rest queue.
_MAX_JOBS, _MAX_JOBS_SOURCE = job_slots.resolve_max_jobs()
_job_slots = job_slots.JobSlots(_MAX_JOBS, on_queue_change=_queue_changed)
print(f'Analysis jobs: up to {_MAX_JOBS} at once ({_MAX_JOBS_SOURCE})')


def _register_job(job_id, job):
    """Store *job* under *job_id* unless a job with that id is still in flight."""
    with _JOBS_LOCK:
        current = jobs.get(job_id)
        if current is not None and current.get('status') in _ACTIVE_JOB_STATUSES:
            return False
        jobs[job_id] = job
        return True


def _fail_job(job, message):
    with _JOBS_LOCK:
        job['status'] = 'error'
        job['stage'] = 'error'
        job['message'] = message


def _mark_running(job, message):
    with _JOBS_LOCK:
        job['status'] = 'running'
        job['message'] = message
        job['updated'] = time.time()


def _start_tracker(job_id, job, track, proc, log_fh, release):
    """Run ``track(proc, log_fh)`` on a thread; the job's slot is freed however it ends.

    The slot is the one in ``job['release']``: a pause frees it early and a resume
    stores the slot it was given, so the tracker frees whichever the job holds last.
    """
    job['release'] = release

    def run():
        try:
            track(proc, log_fh)
        except Exception as exc:
            print(f'job {job_id} tracker failed ({type(exc).__name__})')
            if job.get('status') in _ACTIVE_JOB_STATUSES:
                _fail_job(job, '分析失败 / analysis failed')
        finally:
            # Under the lock, so a resume start either sees the job ended or hands
            # its new slot over before this reads it (no slot is ever leaked).
            with _JOBS_LOCK:
                job['ended'] = True
                last_release = job.get('release', release)
                queued_resume = job.pop('resume_start', None)
                # A frozen job killed from outside: never leave it looking paused.
                if job.get('status') in _PAUSED_JOB_STATUSES:
                    job['status'] = 'error'
                    job['stage'] = 'error'
                    job['message'] = '分析在暂停时中止 / analysis ended while paused'
            if queued_resume is not None:
                # A frozen job killed from outside while waiting to resume.
                _job_slots.cancel(job_id, start=queued_resume)
            last_release()

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        # Nothing would ever watch the process or free its slot: stop it now.
        try:
            proc.kill()
        except Exception:
            pass
        if log_fh is not None:
            log_fh.close()
        _fail_job(job, '分析无法启动 / analysis could not start')
        raise


def _queue_position(job_id, job):
    return _job_slots.position(job_id) if job.get('status') in ('pending', 'resuming') else None
def _find_venv_python():
    candidates = [
        PROJECT_ROOT / '.venv' / 'Scripts' / 'python.exe',  # Windows
        PROJECT_ROOT / '.venv' / 'bin' / 'python3',          # macOS/Linux
        PROJECT_ROOT / '.venv' / 'bin' / 'python',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable

_venv_python = _find_venv_python()


def _parse_progress_line(line):
    """Parse 'PROGRESS <pct> <stage>' emitted by a worker. None if not a progress line."""
    parts = line.strip().split()
    if len(parts) == 3 and parts[0] == "PROGRESS":
        try:
            return int(parts[1]), parts[2]
        except ValueError:
            return None
    return None


def _rep_clip_window(contact_frame, fps, padding=1.5):
    """Seconds window (start, duration) around a rep's contact frame, start clamped to 0."""
    if not fps or fps <= 0:
        fps = 30.0
    center = (contact_frame or 0) / fps
    start = max(0.0, center - padding)
    return round(start, 3), round(padding * 2, 3)


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
    for job_id, job in list(jobs.items()):
        if job.get('status') not in _ACTIVE_JOB_STATUSES:
            continue
        jv = str(job.get('video_name') or '')
        if (job_id == stem or job_id == 'posture_' + stem
                or jv == stem or jv.rsplit('.', 1)[0] == stem):
            return job_id
    return None


def _rally_summary(save_dir):
    """What ``rally_segments.json`` in *save_dir* may honestly claim.

    One reading shared by the job result, the library totals and (via the same
    rules) the Kestrel results panel. Returns ``{"count", "suppressed",
    "label", "signal", "degraded"}``:

    * ``shuttle`` / ``courtview`` -> the count is shown, label "rallies";
    * ``swing`` -> suppressed, label "segments" (the swing signal inflates
      rally COUNT by +36% and total rally time by +41%, design spec §0.17);
    * degraded or ``none`` -> suppressed, label "windows" (coarse windows are
      not rallies, §12a-C);
    * ``error``, a missing or unreadable file -> count 0, not suppressed,
      ``signal`` None: unknown, treated like a run with no data today
      (``failed`` marks the ``error`` and unreadable cases);
    * an older file with no ``detection`` block -> count shown, "rallies".

    "suppressed" means the count must not be presented (or summed) as a rally
    count; ``count`` itself is still the number of segments in the file.
    ``failed`` is True for a ``signal: "error"`` file and for an unreadable or
    malformed one: such a run is not a reliable rally count either, and
    /api/stats must not treat it as one.

    The ``shuttle`` path is unvalidated: no shuttle-based segmentation has been
    measured on any footage (§0.16 measured the swing path only). It is not
    known to be bad, so it is still counted; Kestrel hedges its wording.
    """
    path = os.path.join(str(save_dir), 'rally_segments.json')
    if not os.path.exists(path):
        return summarise_rally_payload(None)
    try:
        with open(path, encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as exc:
        print(f'Could not read {path}: {exc!r}; treating the rally count as unknown')
        return summarise_rally_payload([])      # a non-dict payload is the helper's "malformed -> failed" path
    # a JSON ``null`` is malformed, not "no file" (None means "file absent" to the helper)
    return summarise_rally_payload([] if payload is None else payload)


def _racket_weights(base=None):
    """Path to trained racket-detector weights when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    for name in ('yolo11n-racket.pt', 'yolo11s-racket.pt'):
        p = base / name
        if p.is_file():
            return str(p)
    return None


def _quality_weights(base=None):
    """Path to trained AI form-score weights when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'quality-high_clear.pt'
    return str(p) if p.is_file() else None


def _lift_weights(base=None):
    """Path to the MotionBERT 3D pose-lift checkpoint when installed, else None.

    Server-resolved on purpose: the path is fed to a ``torch.load`` that must
    unpickle (official checkpoints bundle non-tensor training state), so it must
    never come from request data. See docs/motionbert-weights.md.
    """
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'motionbert.pt'
    return str(p) if p.is_file() else None


def _stroke_weights(base=None):
    """Path to the stroke classifier weights when both they and their .json sidecar are installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'stroke-classifier.pt'
    return str(p) if p.is_file() and p.with_suffix('.json').is_file() else None


def _tracknet_weights(base=None):
    """Path to the TrackNetV3 tracking model when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'tracknet.pt'
    return str(p) if p.is_file() else None


def _inpaintnet_weights(base=None):
    """Path to the TrackNetV3 InpaintNet rectifier when installed, else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'inpaintnet.pt'
    return str(p) if p.is_file() else None


def _hit_model_weights(base=None):
    """Path to the learned hit detector when installed (weights + sidecar), else None."""
    base = Path(base) if base is not None else (PROJECT_ROOT / 'weights')
    p = base / 'hit-detector.pt'
    return str(p) if p.is_file() and (base / 'hit-detector.json').is_file() else None


def _read_progress_file(save_dir):
    """Read <save_dir>/progress.json written by the analysis subprocess, or None."""
    p = os.path.join(str(save_dir), 'progress.json')
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# Steps inside a long stage, written as progress.json "substage" (bilingual message).
_PROGRESS_SUBSTAGES = {
    'doubles_identity': ('识别球员身份', 'Identifying players'),
    'doubles_heatmaps': ('生成球员热图', 'Drawing player heatmaps'),
    'doubles_video': ('渲染球员视频', 'Rendering the players video'),
}


def _progress_message(prog):
    """Job message for a progress.json payload: frame counts, plus the sub-stage if known."""
    done, total = prog.get('current_frame', 0), prog.get('total_frames', 0)
    sub = _PROGRESS_SUBSTAGES.get(prog.get('substage'))
    if sub is None:
        return f"分析中... {done}/{total} 帧"
    msg = f'{sub[0]}... / {sub[1]}...'
    if done:
        msg += f' {done}/{total} 帧'
    return msg


def _reset_progress_file(save_dir):
    """Remove a stale progress.json before a (re-)run so the UI starts clean."""
    try:
        p = os.path.join(str(save_dir), 'progress.json')
        if os.path.isfile(p):
            os.remove(p)
    except OSError:
        pass


def _log_tail(path, n=40):
    """Return the last n lines of a log file, or '' if unreadable."""
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return ''.join(fh.readlines()[-n:])
    except OSError:
        return ''


# ═══════════════════════════════════════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════════════════════════════════════

REENCODE_MIN_TIMEOUT_SEC = 900.0
"""Floor for the browser-compat transcode budget.

Even a short clip can encode slowly on a cold or busy machine, so the budget
never drops below this regardless of duration.
"""

REENCODE_TIMEOUT_FACTOR = 6.0
"""Transcode budget as a multiple of video duration.

Measured on the 3840x2160 59.94 fps match that exposed this defect: ffmpeg
-preset fast -crf 23 encoded 20 s of video in 12.5 s, so the full 679 s needed
~425 s. The previous fixed 300 s budget killed it mid-write, leaving a
truncated file with no moov atom. 6x duration leaves generous headroom for
slower hardware while still bounding a genuinely hung ffmpeg.
"""


def _reencode_timeout_sec(duration_sec):
    """Transcode budget for a video of this duration, in seconds."""
    try:
        duration = float(duration_sec)
    except (TypeError, ValueError):
        return REENCODE_MIN_TIMEOUT_SEC
    if duration <= 0:
        return REENCODE_MIN_TIMEOUT_SEC
    return max(REENCODE_MIN_TIMEOUT_SEC, duration * REENCODE_TIMEOUT_FACTOR)


def _run_duration_sec(save_dir):
    """Analysed video's duration from a run's metadata.json, or None.

    Distinct from _video_duration_sec, which probes a video file with cv2:
    this reads the duration the analysis already recorded, with no decoding.
    """
    try:
        with open(os.path.join(str(save_dir), 'metadata.json'), encoding='utf-8') as fh:
            return float(json.load(fh)['video']['duration_sec'])
    except Exception:
        return None


def _reencode_to_h264(raw_video, h264_video, ffmpeg_bin, duration_sec):
    """Transcode ``raw_video`` to browser-playable H.264 in place.

    OpenCV falls back to MPEG-4 Part 2 whenever it cannot initialise an H.264
    encoder (on Windows, typically a mismatched openh264 DLL). No browser
    decodes MPEG-4 Part 2 in a <video> element -- it loads the container, shows
    the right duration, and renders black -- so this pass is what makes the
    analysed video watchable at all, not a nicety.

    Returns None on success, or a human-readable error string. On any failure
    the partial output is deleted: a truncated .mp4 left on disk is worse than
    none, because its plausible size is what made a broken run look finished.
    """
    error = None
    try:
        subprocess.run([
            ffmpeg_bin, '-y', '-i', raw_video,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-movflags', '+faststart', h264_video,
        ], check=True, capture_output=True,
            timeout=_reencode_timeout_sec(duration_sec))
    except subprocess.TimeoutExpired:
        error = (f'ffmpeg timed out after '
                 f'{_reencode_timeout_sec(duration_sec):.0f}s')
    except Exception as exc:
        error = f'ffmpeg failed: {exc}'

    if error is None:
        # Size alone is not proof: the timeout left an 848 MB file that no
        # player could open. It is only a guard against a zero-byte output
        # from an ffmpeg that somehow exited cleanly.
        if not os.path.exists(h264_video) or os.path.getsize(h264_video) <= 0:
            error = 'ffmpeg produced no output'

    if error is not None:
        try:
            if os.path.exists(h264_video):
                os.remove(h264_video)
        except OSError:
            pass
        return error

    os.replace(h264_video, raw_video)
    return None


def _reencode_players_video(save_dir, video_stem, ffmpeg_bin, duration_sec):
    """Browser-compat transcode of the doubles identity video, if one exists.

    The renderer writes mp4v for ``detect_<stem>_players.mp4``, which no browser
    decodes. Best effort by design: returns None when there is nothing to do or
    on success, or an error string after printing a warning. It never raises
    and the caller must not let it change the job's main status or message.
    """
    raw = os.path.join(str(save_dir), f'detect_{video_stem}_players.mp4')
    if not os.path.exists(raw):
        return None
    h264 = os.path.join(str(save_dir), f'detect_{video_stem}_players_h264.mp4')
    try:
        warning = _reencode_to_h264(raw, h264, ffmpeg_bin, duration_sec)
    except Exception as exc:
        warning = f'ffmpeg failed: {exc}'
    if warning:
        print(f'FFmpeg re-encode of the players video failed: {warning}; '
              f'it will not play in a browser')
    return warning


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


@app.route('/api/videos')
def api_videos():
    """列出所有视频和对应的分析结果"""
    import datetime
    videos = []
    with _TAGS_LOCK:
        tag_data = libtags.load(TAGS_PATH)
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in sorted(VIDEOS.glob(ext)):
            name = p.stem
            out_dir = OUTPUTS / name
            has_match = (out_dir / 'detections.jsonl').exists()
            has_posture = (out_dir / 'posture' / 'drill_summary.json').exists()
            has_annotations = (out_dir / 'court_annotations.txt').exists()
            thumb_path = out_dir / 'thumb.jpg'
            if not thumb_path.exists():
                _ensure_thumbnail(p, name)
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
                # Percent-encode the stem: a raw space makes this URL malformed, and
                # kestrel.js renders it inside an unquoted CSS url(), whose grammar
                # forbids spaces -- the parser drops the whole declaration and no
                # thumbnail appears. Flask decodes %20 back to a space when routing.
                'thumb': (f'/api/output/{quote(name, safe="")}/thumb.jpg'
                          if thumb_path.exists() else None),
                'output_dir': str(out_dir) if has_match else None,
                'tags': libtags.tags_for(tag_data, name),
            })
    return jsonify(videos)


# Static, bilingual error bodies for the tag routes: an f-string with {exc} can
# include an absolute filesystem path (e.g. a permission error names the
# file), which a 500 response must never leak to the client. The exception
# itself is still printed server-side for debugging.
_TAG_SAVE_FAILED = {'error': '无法保存标签，请重试 / could not save tags, please retry'}
_TAG_STORE_UNREADABLE = {
    'error': '标签存储不可读，未被覆盖 / tag store unreadable, not overwritten'}


def _tags_for_edit():
    """Load the store for a mutation (PUT tags / rename / delete-tag).

    Returns (data, error_response). ``error_response`` is None on success;
    otherwise the caller must return it immediately without saving. Must be
    called from inside ``_TAGS_LOCK``.

    Spec Sec.9 permits a user's tag edit to replace a corrupt store, so a
    StoreCorrupt backs the bad file up (untouched) and proceeds with data={}
    -- the edit wins, nothing already on disk is lost. StoreUnreadable means
    the file might hold tags this request never saw, so it refuses to write
    at all.
    """
    try:
        return libtags.load_for_update(TAGS_PATH), None
    except libtags.StoreCorrupt:
        backup = libtags.backup_corrupt(TAGS_PATH)
        print(f'Tag store at {TAGS_PATH} was corrupt; backed up to {backup} '
              'and replaced by this edit')
        return {}, None
    except libtags.StoreUnreadable as exc:
        print(f'Tag store unreadable at {TAGS_PATH}: {exc}')
        return None, (jsonify(_TAG_STORE_UNREADABLE), 500)


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
        return jsonify({'error': '标签必须是列表 / tags must be a list'}), 400
    clean, rejected = libtags.normalise_all(raw)
    if rejected:
        # Refuse the whole request rather than saving the acceptable subset:
        # a silent partial save is indistinguishable from a successful one.
        return jsonify({'error': '标签无效或为保留名 / invalid or reserved tag',
                        'rejected': rejected}), 400
    try:
        with _TAGS_LOCK:
            data, err = _tags_for_edit()
            if err:
                return err
            data = libtags.set_tags(data, stem, clean)
            libtags.save(data, TAGS_PATH)
    except OSError as exc:
        print(f'Could not save tags for {stem}: {exc}')
        return jsonify(_TAG_SAVE_FAILED), 500
    return jsonify({'ok': True, 'tags': clean})


@app.route('/api/tags/rename', methods=['POST'])
def api_rename_tag():
    """Rename a tag across every video, merging if the target already exists."""
    body = request.get_json(silent=True) or {}
    old = libtags.normalise(body.get('from'))
    new = libtags.normalise(body.get('to'))
    if old is None or new is None:
        return jsonify({'error': '标签无效或为保留名 / invalid or reserved tag'}), 400
    try:
        with _TAGS_LOCK:
            data, err = _tags_for_edit()
            if err:
                return err
            affected = sum(1 for tags in data.values() if old in tags)
            merged = any(new in tags for tags in data.values())
            libtags.save(libtags.rename(data, old, new), TAGS_PATH)
    except OSError as exc:
        print(f'Could not save tag rename {old} -> {new}: {exc}')
        return jsonify(_TAG_SAVE_FAILED), 500
    return jsonify({'ok': True, 'merged': merged, 'affected': affected})


@app.route('/api/tags/<tag>', methods=['DELETE'])
def api_delete_tag(tag):
    """Remove a tag from every video. The videos themselves are untouched."""
    name = libtags.normalise(tag)
    if name is None:
        return jsonify({'error': '标签无效或为保留名 / invalid or reserved tag'}), 400
    try:
        with _TAGS_LOCK:
            data, err = _tags_for_edit()
            if err:
                return err
            affected = sum(1 for tags in data.values() if name in tags)
            libtags.save(libtags.delete(data, name), TAGS_PATH)
    except OSError as exc:
        print(f'Could not save tag delete {name}: {exc}')
        return jsonify(_TAG_SAVE_FAILED), 500
    return jsonify({'ok': True, 'affected': affected})


@app.route('/api/stats')
def api_stats():
    """Dashboard tiles: counts + aggregate scores across all outputs."""
    count = analyzed = rallies = 0
    reliable_runs = suppressed_runs = 0
    score_sum = score_n = 0.0
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in VIDEOS.glob(ext):
            count += 1
            out = OUTPUTS / p.stem
            has_match = (out / 'detections.jsonl').exists()
            has_posture = (out / 'posture' / 'drill_summary.json').exists()
            if has_match or has_posture:
                analyzed += 1
            # Only reliable runs add to the total: a swing-derived or degraded
            # segmentation is not a rally count (see _rally_summary).
            rs = _rally_summary(out)
            if rs['suppressed']:
                suppressed_runs += 1
            elif not rs['failed']:
                # A failed (error) run has no count to add and is not a reliable
                # run: a library of only such runs shows a dash, not "0".
                rallies += rs['count']
                if os.path.exists(out / 'rally_segments.json'):
                    reliable_runs += 1
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
    # A library whose every segmented run is suppressed has no rally count to
    # show; a bare 0 would read as "no rallies were played". null renders as a dash.
    if suppressed_runs and not reliable_runs:
        rallies = None
    stats = {'videos': count, 'analyzed': analyzed,
             'rallies': rallies, 'avg_technique_score': avg}
    if suppressed_runs:
        # Present only when something was suppressed, so a library without such
        # runs returns exactly the payload it always did.
        stats['rallies_suppressed_runs'] = suppressed_runs
    return jsonify(stats)


@app.route('/api/models')
def api_models():
    """Which optional trained models (racket detector, AI quality scorer, stroke classifier, 3D pose lifter, learned hit detector) are installed."""
    return jsonify({'racket': _racket_weights() is not None,
                    'quality': _quality_weights() is not None,
                    'stroke': _stroke_weights() is not None,
                    'tracknet': _tracknet_weights() is not None,
                    'lift': _lift_weights() is not None,
                    'hit_detector': _hit_model_weights() is not None})


@app.route('/api/upload', methods=['POST'])
def api_upload():
    """上传视频"""
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '没有文件'}), 400
    name = file.filename
    if not name:
        return jsonify({'error': '空文件名'}), 400
    save_path = VIDEOS / name
    file.save(str(save_path))
    _ensure_thumbnail(save_path, save_path.stem)
    return jsonify({'ok': True, 'filename': name, 'size_mb': round(save_path.stat().st_size / 1024 / 1024, 1)})


@app.route('/api/detect', methods=['POST'])
def api_detect():
    """自动检测球场边界"""
    data = request.json
    video_name = data.get('video')
    if not video_name:
        return jsonify({'error': '缺少 video 参数'}), 400

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem
    save_dir.mkdir(exist_ok=True)

    env = os.environ.copy()
    env['PATH'] = f"{os.path.expanduser('~/.local/bin')}:{env.get('PATH', '')}"

    cmd = [_venv_python, str(PROJECT_ROOT / 'court_detect.py'), str(video_path), str(save_dir)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(PROJECT_ROOT), env=env)

        # 找到 JSON 块的起止位置
        stdout = r.stdout
        json_start = stdout.rfind('{')
        if json_start >= 0:
            result = json.loads(stdout[json_start:])
            return jsonify(result)

        return jsonify({
            'success': False,
            'error': '无法解析球场检测输出',
            'stdout': r.stdout[-500:],
            'stderr': r.stderr[-500:],
        }), 500
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': '球场检测超时'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/manual-annotate', methods=['POST'])
def api_manual_annotate():
    """保存手动标注的球场角点，并生成预览图"""
    data = request.json
    video_name = data.get('video')
    corners = data.get('corners')  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]

    if not video_name or not corners or len(corners) != 4:
        return jsonify({'error': '需要4个角点'}), 400

    import cv2
    import numpy as np

    save_dir = OUTPUTS / Path(video_name).stem
    save_dir.mkdir(exist_ok=True)

    # 查找模板图片
    template = TEMPLATES / f'_auto_{Path(video_name).stem}.png'
    if not template.exists():
        for t in TEMPLATES.iterdir():
            if t.stem.startswith(f'_auto_{Path(video_name).stem}'):
                template = t
                break
        else:
            return jsonify({'error': '找不到模板图'}), 400

    img = cv2.imread(str(template))
    if img is None:
        return jsonify({'error': '无法读取模板图'}), 400

    h, w = img.shape[:2]

    # 计算 ROI
    pts = np.array(corners, dtype=np.float32)
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)
    pad_x = (x_max - x_min) * 0.2
    pad_y = (y_max - y_min) * 0.2
    roi = [(max(0, int(x_min - pad_x)), max(0, int(y_min - pad_y))),
           (min(w, int(x_max + pad_x)), min(h, int(y_max + pad_y)))]

    # order: top-left, top-right, bottom-right, bottom-left
    top_y = (pts[0][1] + pts[1][1]) / 2
    bottom_y = (pts[2][1] + pts[3][1]) / 2
    mid_height = int((top_y + bottom_y) / 2)

    # 写入标注文件
    with open(str(save_dir / 'court_annotations.txt'), 'w') as f:
        f.write(f"corners={corners}\n")
        f.write(f"roi_corners={roi}\n")
        f.write(f"mid_height={mid_height}\n")

    # 生成带手动标注的预览图
    preview_img = img.copy()
    # 缩放到合理大小用于预览
    max_dim = 1200
    scale_val = min(1.0, max_dim / max(w, h))
    small = cv2.resize(preview_img, (int(w*scale_val), int(h*scale_val)))
    sc = [(int(x*scale_val), int(y*scale_val)) for x, y in corners]
    sr = [(int(x*scale_val), int(y*scale_val)) for x, y in roi]

    # 画四边形
    cv2.polylines(small, [np.array(sc, dtype=np.int32)], True, (0, 255, 0), 3)
    # 画角点和数字
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255)]
    for i, (pt, col) in enumerate(zip(sc, colors)):
        cv2.circle(small, pt, 8, col, -1)
        cv2.putText(small, str(i+1), (pt[0]+12, pt[1]-8), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255,255,255), 2)
    # 画 ROI 框
    if sr:
        cv2.rectangle(small, sr[0], sr[1], (255, 0, 0), 2)

    # 写标题
    cv2.rectangle(small, (0, 0), (small.shape[1], 40), (0, 0, 0), -1)
    cv2.putText(small, "Manual annotation - corners saved", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

    preview_path = str(save_dir / 'manual_court_preview.png')
    cv2.imwrite(preview_path, small)

    return jsonify({
        'ok': True,
        'corners': corners,
        'roi': roi,
        'mid_height': mid_height,
        'preview_path': f'/api/output/{Path(video_name).stem}/manual_court_preview.png',
    })


def _template_arg(video_path):
    """The video's auto court template when one exists, else the demo template."""
    auto = TEMPLATES / f'_auto_{video_path.stem}.png'
    return str(auto) if auto.exists() else str(TEMPLATES / 'demo.png')


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """启动视频分析（后台子进程）"""
    data = request.json
    video_name = data.get('video')
    language = data.get('language', 'zh')
    pose_family = data.get('pose_family', 'yolo-pose')
    analyze_technique = data.get('analyze_technique', True)
    quality = data.get('analysis_quality', 'accurate')
    match_format = data.get('match_format', 'singles')
    if match_format not in ('singles', 'doubles'):
        return jsonify({'error': 'invalid match_format'}), 400

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem
    save_dir.mkdir(exist_ok=True)

    # 检查球场标注
    has_annotations = (save_dir / 'court_annotations.txt').exists()
    template_arg = _template_arg(video_path)

    env = os.environ.copy()
    env['PATH'] = f"{PROJECT_ROOT / '.local' / 'bin'}:{env.get('PATH', '')}"

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'main.py'),
        '--video-path', str(video_path),
        '--template-path', template_arg,
        '--output-dir', str(save_dir),
        '--display', 'false',
        '--language', language,
        '--pose-family', pose_family,
        '--yolo-pose-model', str(WEIGHTS / 'yolo11n-pose.pt'),
        '--ball-model', str(WEIGHTS / 'yolo11s-ball.pt'),
        '--visualize-positions', 'true',
        '--performance-stats',
    ]
    cmd += ['--analysis-quality', quality]
    cmd += ['--match-format', match_format]
    if analyze_technique:
        cmd.append('--analyze-technique')

    racket_weights = _racket_weights()
    if racket_weights:
        cmd += ['--racket-model', racket_weights]

    stroke_weights = _stroke_weights()
    if stroke_weights:
        cmd += ['--stroke-model', stroke_weights]

    tracknet_weights = _tracknet_weights()
    if tracknet_weights:
        cmd += ['--tracknet-model', tracknet_weights]
    inpaintnet_weights = _inpaintnet_weights()
    if inpaintnet_weights:
        cmd += ['--inpaintnet-model', inpaintnet_weights]

    hit_weights = _hit_model_weights()
    if hit_weights:
        cmd += ['--hit-model', hit_weights]

    job_id = video_path.stem
    job = {
        'status': 'pending',
        'progress': 0,
        'message': '准备启动...',
        'video_name': video_name,
        'save_dir': str(save_dir),
        'stage': 'analyzing',
        'updated': time.time(),
    }
    if not _register_job(job_id, job):
        return jsonify(_JOB_BUSY), 409

    def start(release):
        # Runs when a slot is free: now, or later on the thread that freed it.
        # The auto template may have appeared while the job waited in the queue.
        cmd[cmd.index('--template-path') + 1] = _template_arg(video_path)
        log_path = os.path.join(str(save_dir), 'analyze.log')
        log_fh = None
        try:
            _reset_progress_file(save_dir)
            log_fh = open(log_path, 'w', encoding='utf-8')
            proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                                    text=True, cwd=str(PROJECT_ROOT), env=env)
        except Exception:
            # An open handle keeps analyze.log locked on Windows (delete, re-run).
            if log_fh is not None:
                log_fh.close()
            _fail_job(job, '分析无法启动 / analysis could not start')
            raise
        job['proc'] = proc
        job['log_path'] = log_path
        _mark_running(job, '准备启动...')
        _start_tracker(job_id, job, track_progress, proc, log_fh, release)

    # 进度跟踪（在子进程启动后的线程中运行）
    def track_progress(proc, log_fh):
        while job['status'] in ('running',) + _PAUSED_JOB_STATUSES and proc.poll() is None:
            # A paused job keeps its "paused" message; its progress file does not move anyway.
            prog = _read_progress_file(job['save_dir']) if job['status'] == 'running' else None
            if prog is not None:
                job['progress'] = int(prog.get('pct', job.get('progress', 0)))
                job['stage'] = prog.get('stage', job.get('stage'))
                job['updated'] = prog.get('updated')
                job['message'] = _progress_message(prog)
            time.sleep(1.0)

        proc.wait()
        try:
            log_fh.close()
        except Exception:
            pass

        if proc.returncode == 0:
            job['status'] = 'reencoding'
            job['stage'] = 'encoding'
            job['progress'] = 99
            job['message'] = '正在转码为浏览器兼容格式...'
            job['updated'] = time.time()

            # FFmpeg 转码为 H.264（浏览器兼容）
            vs = video_path.stem
            sd = str(save_dir)
            raw_video = os.path.join(sd, f'detect_{vs}.mp4')
            h264_video = os.path.join(sd, f'detect_{vs}_h264.mp4')
            ffmpeg_bin = shutil.which('ffmpeg') or os.path.expanduser('~/.local/bin/ffmpeg')

            video_warning = _reencode_to_h264(
                raw_video, h264_video, ffmpeg_bin, _run_duration_sec(sd))
            if video_warning:
                # Not fatal -- every other output of the run is still valid --
                # but it must not be silent. Without the transcode the video is
                # MPEG-4 Part 2, which plays as a black frame in the browser,
                # and reporting plain success is what hid that for a whole run.
                print(f'FFmpeg re-encode failed: {video_warning}; '
                      f'the analysed video will not play in a browser')

            # Doubles identity video (mp4v from the renderer): same transcode,
            # but never fatal and never reflected in the job's main status.
            if os.path.exists(os.path.join(sd, f'detect_{vs}_players.mp4')):
                job['message'] = '正在转码球员视频... / Encoding the players video...'
                job['updated'] = time.time()
            _reencode_players_video(sd, vs, ffmpeg_bin, _run_duration_sec(sd))

            job['status'] = 'completed'
            job['stage'] = 'done'
            job['progress'] = 100
            job['message'] = ('分析完成! (视频转码失败，浏览器可能无法播放) / '
                              'Analysis complete, but the video could not be '
                              'converted for browser playback'
                              if video_warning else '分析完成!')

            # 收集结果
            pos_dir = os.path.join(sd, 'position_visualizations')
            rs = _rally_summary(sd)
            rally_count = None if rs['suppressed'] else rs['count']

            job['result'] = {
                'video_name': vs,
                'output_video': f'/api/output/{vs}/detect_{vs}.mp4',
                'detections': f'/api/output/{vs}/detections.jsonl',
                'heatmap': f'/api/output/{vs}/position_visualizations/heatmaps/match_heatmap.png',
                'scatter': f'/api/output/{vs}/position_visualizations/scatter_plots/match_scatter.png',
                'preview': f'/api/output/{vs}/auto_court_preview.png',
                # None when suppressed (swing-derived or degraded segmentation):
                # the UI shows rally_label, not a rally count.
                'rally_count': rally_count,
                'rally_count_suppressed': rs['suppressed'],
                'rally_label': rs['label'],
                # None on success. When set, the served video is still the
                # OpenCV fallback codec and will not play in a browser; the UI
                # says so rather than showing a black player.
                'video_warning': video_warning,
            }
        else:
            job['status'] = 'error'
            job['stage'] = 'error'
            tail = _log_tail(job.get('log_path', ''), n=40)
            job['message'] = f'分析失败 (exit={proc.returncode})\n{tail}'

    queued = not _job_slots.submit(job_id, start)
    return jsonify({'ok': True, 'job_id': job_id, 'queued': queued,
                    'position': _job_slots.position(job_id)})


@app.route('/api/status/<job_id>')
def api_status(job_id):
    """查询分析进度"""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'status': 'not_found'}), 404
    return jsonify({
        'status': job.get('status'),
        'progress': job.get('progress', 0),
        'message': job.get('message', ''),
        'result': job.get('result'),
        'stage': job.get('stage'),
        'updated': job.get('updated'),
        'queue_position': _queue_position(job_id, job),
    })


@app.route('/api/jobs/slots')
def api_job_slots():
    """How many analyses run at once, and which are running or queued."""
    return jsonify(_job_slots.snapshot())


@app.route('/api/jobs/<job_id>/cancel', methods=['POST'])
def api_job_cancel(job_id):
    """Drop a queued job before it starts. A running job cannot be cancelled here."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': '未知的任务 / unknown job'}), 404
    # A paused job waiting to resume is queued too, but cancelling it would orphan its process.
    if job.get('status') != 'pending' or not _job_slots.cancel(job_id):
        return jsonify({'error': '任务不在队列中 / job is not queued'}), 409
    with _JOBS_LOCK:
        job['status'] = 'cancelled'
        job['stage'] = 'cancelled'
        job['message'] = ''
        job['updated'] = time.time()
    return jsonify({'ok': True})


@app.route('/api/jobs/<job_id>/pause', methods=['POST'])
def api_job_pause(job_id):
    """Freeze a running analysis in place and hand its slot to the next queued job.

    The frozen process keeps its RAM and GPU memory, so no more jobs may be paused
    at once than run at once.
    """
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': '未知的任务 / unknown job'}), 404
    with _JOBS_LOCK:
        proc = job.get('proc')
        if job.get('status') != 'running' or proc is None or proc.poll() is not None:
            return jsonify({'error': '只有正在分析的任务可以暂停 / only a running analysis can be paused'}), 409
        paused = sum(1 for j in list(jobs.values()) if j.get('status') in _PAUSED_JOB_STATUSES)
        if paused >= _job_slots.max_jobs:
            limit = _job_slots.max_jobs
            return jsonify({'error': f'最多同时暂停 {limit} 个任务，请先继续一个 / '
                                     f'at most {limit} jobs can be paused at once; resume one first'}), 409
        try:
            proc_control.suspend_tree(proc.pid)
        except Exception as exc:
            print(f'job {job_id} could not be paused ({type(exc).__name__})')
            return jsonify({'error': '无法暂停：任务可能刚刚结束 / could not pause: the job may have just ended'}), 409
        job['status'] = 'paused'
        job['message'] = '已暂停 / paused'
        job['updated'] = time.time()
        release = job.get('release')
    # Outside the lock: freeing the slot may start a queued job, which takes the lock.
    if release is not None:
        release()
    return jsonify({'ok': True})


@app.route('/api/jobs/<job_id>/resume', methods=['POST'])
def api_job_resume(job_id):
    """Continue a paused analysis now, or as soon as a slot frees (ahead of new jobs)."""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': '未知的任务 / unknown job'}), 404
    proc = job.get('proc')

    def start(release):
        with _JOBS_LOCK:
            job.pop('resume_start', None)
            # The process ended while it waited (killed from outside): its tracker
            # sets the final status, so the slot just goes to the next job.
            if job.get('ended') or job.get('status') != 'resuming' or proc.poll() is not None:
                raise RuntimeError('job is no longer waiting to resume')
            try:
                proc_control.resume_tree(proc.pid)
            except Exception:
                # Still frozen: back to paused, so Resume can be tried again.
                job['status'] = 'paused'
                job['message'] = '无法继续，请重试 / could not resume; try again'
                job['updated'] = time.time()
                raise
            job['release'] = release
            job['status'] = 'running'
            job['message'] = '继续分析... / resuming...'
            job['updated'] = time.time()

    with _JOBS_LOCK:
        if job.get('status') != 'paused' or proc is None:
            return jsonify({'error': '任务未暂停 / job is not paused'}), 409
        if job.get('ended') or proc.poll() is not None:
            return jsonify({'error': '任务已结束 / the job has already ended'}), 409
        job['status'] = 'resuming'
        job['message'] = '等待空闲分析位以继续 / waiting for a free slot to resume'
        job['updated'] = time.time()
        job['resume_start'] = start
    queued = not _job_slots.submit(job_id, start, front=True)
    with _JOBS_LOCK:
        ended = job.get('ended')
        thaw_failed = not queued and job.get('status') == 'paused'
    if ended:
        # The tracker ended between the checks above and submit: drop the entry it could not see.
        _job_slots.cancel(job_id, start=start)
    if thaw_failed:
        return jsonify({'error': '无法继续，请重试 / could not resume; try again'}), 409
    return jsonify({'ok': True, 'queued': queued, 'position': _job_slots.position(job_id)})


@app.route('/api/clip', methods=['POST'])
def api_clip():
    """生成回合剪辑视频"""
    data = request.json
    video_name = data.get('video')
    mode = data.get('mode', 'highlights')
    padding = data.get('padding', 1.5)

    video_path = VIDEOS / video_name
    save_dir = OUTPUTS / video_path.stem
    rally_file = save_dir / 'rally_segments.json'

    if not rally_file.exists():
        return jsonify({'error': '还没有回合检测数据，请先运行分析'}), 400

    clip_output_dir = save_dir / 'clips'
    clip_output_dir.mkdir(exist_ok=True)

    env = os.environ.copy()
    env['PATH'] = f"{os.path.expanduser('~/.local/bin')}:{env.get('PATH', '')}"

    # 优先使用标注视频（有分析叠加层），否则回退到原始视频
    analyzed_video = save_dir / f'detect_{video_path.stem}.mp4'
    source_video = analyzed_video if analyzed_video.exists() else video_path

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'clip_video.py'),
        '--video-path', str(source_video),
        '--rally-file', str(rally_file),
        '--output-dir', str(clip_output_dir),
        '--mode', mode,
        '--padding', str(padding),
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT), env=env)
        print(r.stdout)

        # 查找生成的剪辑文件
        clips = sorted(clip_output_dir.glob('*.mp4'))
        result = {
            'ok': True,
            'mode': mode,
            'clips': [{
                'name': c.name,
                'url': f'/api/output/{video_path.stem}/clips/{c.name}',
                'size_mb': round(c.stat().st_size / 1024 / 1024, 1),
            } for c in clips],
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/output/<video_name>/<path:subpath>')
def serve_output(video_name, subpath):
    """提供输出文件，支持视频 Range 请求"""
    filepath = OUTPUTS / video_name / subpath
    if not filepath.exists():
        return jsonify({'error': f'文件不存在: {subpath}'}), 404
    if filepath.suffix in ('.mp4', '.webm'):
        return send_file(str(filepath), mimetype='video/mp4', conditional=True)
    if filepath.suffix == '.jsonl':
        return send_file(str(filepath), mimetype='application/x-ndjson', conditional=True)
    return send_file(str(filepath), conditional=True)


@app.route('/api/template/<video_name>')
def serve_template_image(video_name):
    """提供模板图片（用于手动标注）"""
    template = TEMPLATES / f'_auto_{video_name}.png'
    if template.exists():
        return send_file(str(template))
    # fallback: 尝试其他格式
    for t in TEMPLATES.iterdir():
        if t.stem.startswith(f'_auto_{video_name}'):
            return send_file(str(t))
    return jsonify({'error': '模板不存在'}), 404


@app.route('/api/output/<video_name>/<path:subpath>', methods=['DELETE'])
def delete_output(video_name, subpath):
    """删除某个输出文件或整个输出目录"""
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


@app.route('/api/technique/<video_name>')
def api_technique(video_name):
    """Return technique summary + per-stroke reports for a video."""
    out_dir = OUTPUTS / video_name
    summary_path = out_dir / 'technique_summary.json'
    if not summary_path.exists():
        return jsonify({'error': '还没有技术分析数据，请先用 --analyze-technique 运行分析'}), 404

    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)

        strokes = []
        strokes_path = out_dir / 'strokes.jsonl'
        if strokes_path.exists():
            with open(strokes_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        strokes.append(json.loads(line))
    except Exception as e:
        return jsonify({'error': 'failed to read report'}), 500

    return jsonify({'summary': summary, 'strokes': strokes})


def _load_or_make_training_plan(video_name, weeks=4, force=False):
    out_dir = OUTPUTS / video_name
    plan_path = out_dir / 'training_plan.json'
    summary_path = out_dir / 'technique_summary.json'

    if plan_path.exists() and not force:
        try:
            with open(plan_path, encoding='utf-8') as f:
                return json.load(f), 200
        except Exception as e:
            return {'error': str(e)}, 500

    if not summary_path.exists():
        return {'error': '没有技术分析数据，无法生成训练计划'}, 404

    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        plan = generate_plan(summary, weeks=weeks)
        write_json(str(plan_path), plan)
    except Exception as e:
        return {'error': str(e)}, 500

    return plan, 200


@app.route('/api/training-plan/<video_name>', methods=['GET'])
def api_training_plan(video_name):
    plan, status = _load_or_make_training_plan(video_name)
    return jsonify(plan), status


@app.route('/api/training-plan/<video_name>', methods=['POST'])
def api_training_plan_regenerate(video_name):
    data = request.json or {}
    try:
        weeks = int(data.get('weeks', 4))
    except (TypeError, ValueError):
        return jsonify({'error': 'weeks 必须是整数'}), 400
    plan, status = _load_or_make_training_plan(video_name, weeks=weeks, force=True)
    return jsonify(plan), status


@app.route('/api/posture/<video_name>')
def api_posture(video_name):
    """Return drill summary + per-rep reports for a posture-drill video."""
    out_dir = OUTPUTS / video_name / 'posture'
    summary_path = out_dir / 'drill_summary.json'
    if not summary_path.exists():
        return jsonify({'error': '还没有姿态训练分析数据，请先运行姿态分析'}), 404
    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        reps = []
        reps_path = out_dir / 'drill_reps.jsonl'
        if reps_path.exists():
            with open(reps_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        reps.append(json.loads(line))
        return jsonify({'summary': summary, 'reps': reps})
    except Exception as e:
        return jsonify({'error': 'failed to read report'}), 500


@app.route('/api/posture-rep-clip/<video_name>', methods=['POST'])
def api_posture_rep_clip(video_name):
    """On-demand ffmpeg crop of a single rep window from the annotated posture video."""
    data = request.get_json(silent=True) or {}
    rep_id = data.get('rep_id')
    if rep_id is None:
        return jsonify({'error': '需要 rep_id'}), 400
    out_dir = OUTPUTS / video_name / 'posture'
    reps_path = out_dir / 'drill_reps.jsonl'
    if not reps_path.exists():
        return jsonify({'error': '没有 rep 数据'}), 404
    rep = None
    try:
        with open(reps_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if str(r.get('rep_id')) == str(rep_id):
                    rep = r
                    break
    except Exception:
        return jsonify({'error': 'rep 数据读取失败'}), 500
    if rep is None:
        return jsonify({'error': 'rep 不存在'}), 404
    video_file = out_dir / ('detect_' + video_name + '.mp4')
    if not video_file.exists():
        return jsonify({'error': '没有标注视频'}), 404
    fps = 30.0
    meta_path = out_dir / 'metadata.json'
    if meta_path.exists():
        try:
            with open(meta_path, encoding='utf-8') as f:
                fps = (json.load(f).get('video', {}) or {}).get('fps') or 30.0
        except Exception:
            fps = 30.0
    start, duration = _rep_clip_window(rep.get('contact_frame', 0), fps)
    clip_dir = out_dir / 'rep_clips'
    clip_dir.mkdir(exist_ok=True)
    out_file = clip_dir / ('rep_' + str(rep_id) + '.mp4')
    ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
    try:
        subprocess.run([ffmpeg_bin, '-y', '-ss', str(start), '-i', str(video_file),
                        '-t', str(duration), '-c:v', 'libx264', '-preset', 'fast',
                        '-crf', '23', '-movflags', '+faststart', str(out_file)],
                       check=True, capture_output=True, timeout=120)
    except Exception as e:
        return jsonify({'error': 'ffmpeg 失败: ' + str(e)}), 500
    return jsonify({'ok': True,
                    'url': '/api/output/' + video_name + '/posture/rep_clips/rep_' + str(rep_id) + '.mp4'})


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


def _forget_match_record(stem):
    """Drop ``stem``'s match record from the athlete store after its video is deleted.

    The same contract as the tag cleanup in ``api_delete``: never raises (the
    files are already gone), writes only when a record exists, and skips a
    corrupt or unreadable store entirely -- no write, no backup -- since a
    video delete carries no data worth replacing the file for.

    A deleted primary of a split match takes its parts' links with it (they would
    dangle); the parts' own videos and analyses are untouched, they are just no
    longer linked. A deleted part only loses its own link.
    """
    with _ATHLETES_LOCK:
        try:
            data = libathletes.load_for_update(ATHLETES_PATH)
        except (libathletes.StoreCorrupt, OSError) as exc:
            print(f'Match record cleanup skipped for {stem}: {type(exc).__name__}')
            return
        if stem not in data['matches'] and not libathletes.has_parts(data, stem):
            return
        try:
            libathletes.save(libathletes.set_match(data, stem, None), ATHLETES_PATH)
        except OSError as exc:
            print(f'Match record cleanup skipped for {stem}: {exc}')


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
    groups = sorted(_delete_groups(stem, scope), key=lambda g: g[0] == 'source')
    try:
        for _key, paths in groups:
            for p in paths:
                if p.is_dir():
                    deleted += sum(1 for f in p.rglob('*') if f.is_file())
                    shutil.rmtree(str(p))
                elif p.is_file():
                    p.unlink()
                    deleted += 1
        # For scope "all", also delete the parent outputs directory
        if scope == 'all':
            out_dir = OUTPUTS / stem
            if out_dir.is_dir() and not list(out_dir.iterdir()):
                shutil.rmtree(str(out_dir))
            for stale_id in (stem, 'posture_' + stem):
                job = jobs.get(stale_id)
                if job and job.get('status') not in _ACTIVE_JOB_STATUSES:
                    jobs.pop(stale_id, None)
            # Tags belong to the video, so they go only when the video does.
            # Never fatal: the files are already deleted by this point, and
            # reporting the delete as failed over a tag-store hiccup would be
            # worse than a stale entry, which is invisible anyway (a deleted
            # video has no /api/videos row to carry it). A corrupt or
            # unreadable store is skipped entirely -- no write, no backup --
            # unlike a user's own tag edit (_tags_for_edit), which is allowed
            # to replace a corrupt file: a video delete carries no new tag
            # data worth trading the old file away for.
            with _TAGS_LOCK:
                try:
                    data = libtags.load_for_update(TAGS_PATH)
                except (libtags.StoreCorrupt, OSError) as exc:
                    print(f'Tag cleanup skipped for {stem}: {exc}')
                else:
                    if stem in data:
                        try:
                            libtags.save(libtags.forget(data, stem), TAGS_PATH)
                        except OSError as exc:
                            print(f'Tag cleanup skipped for {stem}: {exc}')
            _forget_match_record(stem)
    except Exception:
        return jsonify({'error': '删除失败'}), 500
    return jsonify({'ok': True, 'scope': scope, 'deleted_files': deleted})


def _load_or_make_posture_plan(video_name, weeks=4, force=False):
    out_dir = OUTPUTS / video_name / 'posture'
    plan_path = out_dir / 'training_plan.json'
    summary_path = out_dir / 'drill_summary.json'
    if plan_path.exists() and not force:
        try:
            with open(plan_path, encoding='utf-8') as f:
                return json.load(f), 200
        except Exception as e:
            return {'error': str(e)}, 500
    if not summary_path.exists():
        return {'error': '没有姿态分析数据，无法生成训练计划'}, 404
    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        plan = generate_plan(summary, weeks=weeks)
        write_json(str(plan_path), plan)
        return plan, 200
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/api/posture-plan/<video_name>', methods=['GET'])
def api_posture_plan(video_name):
    plan, status = _load_or_make_posture_plan(video_name)
    return jsonify(plan), status


@app.route('/api/posture-plan/<video_name>', methods=['POST'])
def api_posture_plan_regenerate(video_name):
    data = request.json or {}
    try:
        weeks = int(data.get('weeks', 4))
    except (TypeError, ValueError):
        return jsonify({'error': 'weeks 必须是整数'}), 400
    plan, status = _load_or_make_posture_plan(video_name, weeks=weeks, force=True)
    return jsonify(plan), status


@app.route('/api/posture/analyze', methods=['POST'])
def api_posture_analyze():
    data = request.json or {}
    video_name = data.get('video')
    stroke_type = data.get('stroke_type')
    dominant = data.get('dominant_hand', 'right')
    pose_family = data.get('pose_family', 'yolo-pose')
    report_llm = data.get('report_llm', 'off')
    if pose_family not in ('yolo-pose', 'rtmpose', 'rtmo'):
        return jsonify({'error': 'invalid pose_family'}), 400
    if not video_name or stroke_type not in ('high_clear', 'smash', 'drop_shot', 'serve'):
        return jsonify({'error': '需要 video 和有效的 stroke_type'}), 400

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem / 'posture'
    save_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'main_posture.py'),
        '--video-path', str(video_path),
        '--stroke-type', stroke_type,
        '--dominant-hand', dominant,
        '--pose-family', pose_family,
        '--output-dir', str(save_dir),
        '--display', 'false',
        '--report-llm', report_llm,
    ]

    racket_weights = _racket_weights()
    if racket_weights:
        cmd += ['--racket-model', racket_weights]

    quality_weights = _quality_weights()
    if quality_weights:
        cmd += ['--quality-model', quality_weights]

    # Resolved server-side like every other model path: the checkpoint is
    # unpickled by torch.load, so a request must never be able to name the file.
    lift_weights = _lift_weights()
    if lift_weights:
        cmd += ['--lift-model', lift_weights]
        lift_device = data.get('lift_device', 'auto')
        if lift_device in ('auto', 'cpu', 'cuda'):
            cmd += ['--lift-device', lift_device]

    job_id = 'posture_' + video_path.stem
    job = {'status': 'pending', 'progress': 0, 'message': '准备启动...',
           'video_name': video_path.stem, 'save_dir': str(save_dir),
           'updated': time.time()}
    if not _register_job(job_id, job):
        return jsonify(_JOB_BUSY), 409

    def start(release):
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, cwd=str(PROJECT_ROOT), env=os.environ.copy())
        except Exception:
            _fail_job(job, '姿态分析无法启动 / posture analysis could not start')
            raise
        job['proc'] = proc
        _mark_running(job, '姿态分析中...')
        _start_tracker(job_id, job, track, proc, None, release)

    def track(proc, _log_fh):
        job['stage'] = 'loading'
        for line in proc.stdout:
            parsed = _parse_progress_line(line)
            if parsed:
                job['progress'], job['stage'] = parsed[0], parsed[1]
        proc.wait()
        if proc.returncode == 0:
            job['stage'] = 'encoding'
            job['progress'] = max(job.get('progress', 0), 96)
            job['message'] = '完成!'
            vs = video_path.stem
            sd = str(save_dir)
            raw = os.path.join(sd, 'detect_' + vs + '.mp4')
            h264 = os.path.join(sd, 'detect_' + vs + '_h264.mp4')
            ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
            try:
                subprocess.run([ffmpeg_bin, '-y', '-i', raw, '-c:v', 'libx264',
                                '-preset', 'fast', '-crf', '23', '-movflags', '+faststart', h264],
                               check=True, capture_output=True, timeout=600)
                if os.path.exists(h264) and os.path.getsize(h264) > 0:
                    os.replace(h264, raw)
            except Exception as e:
                print('posture ffmpeg re-encode failed: ' + str(e))
            job['status'] = 'completed'
            job['progress'] = 100
            job['stage'] = 'done'
            job['result'] = {'video_name': vs,
                             'output_video': '/api/output/' + vs + '/posture/detect_' + vs + '.mp4'}
        else:
            job['status'] = 'error'
            job['stage'] = 'error'
            job['message'] = '姿态分析失败 (exit=' + str(proc.returncode) + ')'

    queued = not _job_slots.submit(job_id, start)
    return jsonify({'ok': True, 'job_id': job_id, 'queued': queued,
                    'position': _job_slots.position(job_id)})


@app.route('/api/posture-analyze-status/<job_id>')
def api_posture_analyze_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'status': 'not_found'}), 404
    return jsonify({'status': job.get('status'), 'progress': job.get('progress', 0),
                    'message': job.get('message', ''), 'result': job.get('result'),
                    'stage': job.get('stage'), 'updated': job.get('updated'),
                    'queue_position': _queue_position(job_id, job)})


_REPORT_LANGS = ("en", "zh-Hant", "zh-Hans")


@app.route('/api/posture-report/<video_name>')
def api_posture_report(video_name):
    lang = request.args.get('lang', 'en')
    if lang not in _REPORT_LANGS:
        return jsonify({'error': 'invalid lang'}), 400
    path = OUTPUTS / video_name / 'posture' / ('coach_report_' + lang + '.json')
    if not path.exists():
        return jsonify({'error': '还没有教练报告，请先运行姿态分析'}), 404
    try:
        with open(path, encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({'error': 'failed to read report'}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Static page
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    # return (PROJECT_ROOT / 'web_ui.html').read_text(encoding='utf-8')
    return (PROJECT_ROOT / 'kestrel.html').read_text(encoding='utf-8')


@app.route('/kestrel')
def kestrel():
    return (PROJECT_ROOT / 'kestrel.html').read_text(encoding='utf-8')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=5050)
    p.add_argument('--host', default='0.0.0.0')
    args = p.parse_args()

    print(f'''
╔══════════════════════════════════════════╗
║  🏸  Good Badminton Web Frontend       ║
║  打开浏览器访问: http://{args.host}:{args.port}  ║
╚══════════════════════════════════════════╝
''')
    app.run(host=args.host, port=args.port, debug=False)
