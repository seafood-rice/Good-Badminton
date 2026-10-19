"""Insights and jobs routes for the Kestrel telemetry UI.

A Flask Blueprint over ``badminton_analysis.insights`` (per-video sidecars and
the overview aggregation) plus a read-only view of the analysis jobs. It owns no
state: ``app.py`` passes in getters that are resolved on every request, so a test
that re-points ``VIDEOS`` / ``OUTPUTS`` / ``ATHLETES_PATH`` is honoured.

Error bodies are static bilingual strings (``中文 / English``). An exception
text can carry an absolute filesystem path, so nothing from an exception ever
reaches a response; the exception type is printed server-side instead.
"""
import datetime
import json
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from badminton_analysis.insights import aggregate, sidecar
from badminton_analysis.library import athletes as libathletes

_UNKNOWN_VIDEO = {'error': '未知的视频 / unknown video'}
_UNKNOWN_ATHLETE = {'error': '未知的运动员 / unknown athlete'}
_UNKNOWN_SQUAD = {'error': '未知的小组 / unknown squad'}
_BAD_RANGE = {'error': '时间范围无效 / invalid range'}
_FAILED = {'error': '服务器内部错误 / internal server error'}

# A stem is a library run when its output directory holds any of these.
RUN_MARKERS = ('detections.jsonl', 'strokes.json', 'strokes.jsonl',
               'technique_summary.json', 'rally_segments.json')
VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
ACTIVE_STATUSES = ('pending', 'running', 'reencoding')
DETACHED_WINDOW_SEC = 600.0
_MESSAGE_MAX = 200


def _log_failure(what, exc):
    print(f'{what} failed ({type(exc).__name__})')


def _epoch(value):
    """An epoch-seconds float from a number or an ISO-8601 string, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value == value and abs(value) < 1e12 else None
    if isinstance(value, str):
        try:
            when = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        return when.timestamp()
    return None


def _video_stem(name):
    """``clip.mp4`` -> ``clip``; a name without a video extension is unchanged."""
    name = str(name or '')
    return Path(name).stem if name.lower().endswith(VIDEO_EXTS) else name


def _first_line(text):
    """A job message cut to its first line: an error message carries a log tail
    that can hold absolute paths."""
    first = str(text or '').split('\n', 1)[0]
    return first[:_MESSAGE_MAX]


def _int_pct(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 0
    return max(0, min(100, int(value)))


def register(app, *, videos, outputs, athletes_path, athletes_lock, jobs, safe_stem):
    """Register the routes on ``app``.

    ``videos()`` / ``outputs()`` return the directories, ``athletes_path()`` the
    store path, ``athletes_lock()`` the lock guarding it, ``jobs()`` the live job
    dict and ``safe_stem(name)`` the validated stem or ``None``. All are called
    per request.
    """
    bp = Blueprint('insights', __name__)

    # ---------------------------------------------------------------- library

    def _video_files():
        """``{stem: path}`` of the library videos (first one wins per stem)."""
        found = {}
        root = Path(videos())
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError:
            return found
        for path in entries:
            if path.suffix.lower() in VIDEO_EXTS and path.is_file():
                found.setdefault(path.stem, path)
        return found

    def _run_stems():
        """Names of the output directories that hold a run, sorted."""
        root = Path(outputs())
        stems = []
        try:
            entries = list(root.iterdir())
        except OSError:
            return stems
        for path in entries:
            try:
                if path.is_dir() and any((path / m).is_file() for m in RUN_MARKERS):
                    stems.append(path.name)
            except OSError:
                continue
        return sorted(stems)

    def _compute(stem, video_files):
        return sidecar.compute_video(stem, str(Path(outputs()) / stem),
                                     video_files.get(stem))

    def _running_stems():
        running = set()
        for job_id, job in list((jobs() or {}).items()):
            if not isinstance(job, dict) or job.get('status') not in ACTIVE_STATUSES:
                continue
            name = _video_stem(job.get('video_name'))
            if name:
                running.add(name)
            elif isinstance(job_id, str):
                running.add(job_id[len('posture_'):] if job_id.startswith('posture_')
                            else job_id)
        return frozenset(running)

    # --------------------------------------------------------------- insights

    @bp.route('/api/insights/overview', methods=['GET'])
    def insights_overview():
        athlete = request.args.get('athlete') or None
        squad = request.args.get('squad') or None
        range_ = request.args.get('range') or '30d'
        if range_ not in aggregate.RANGES:
            return jsonify(_BAD_RANGE), 400
        try:
            with athletes_lock():
                store = libathletes.load(athletes_path())
            video_files = _video_files()
            units_source = []
            for stem in _run_stems():
                try:
                    insights = _compute(stem, video_files)
                except Exception as exc:  # compute_video never raises; belt and braces
                    _log_failure('insights for one video', exc)
                    continue
                units_source.append({'stem': stem, 'insights': insights})
            result = aggregate.overview(units_source, store, athlete=athlete,
                                        squad=squad, range_=range_,
                                        running_stems=_running_stems())
        except Exception as exc:
            # aggregate.overview signals its three expected failures by text.
            code = str(exc.args[0]) if exc.args else ''
            if isinstance(exc, LookupError) and code == 'athlete':
                return jsonify(_UNKNOWN_ATHLETE), 404
            if isinstance(exc, LookupError) and code == 'squad':
                return jsonify(_UNKNOWN_SQUAD), 404
            if isinstance(exc, ValueError) and code == 'range':
                return jsonify(_BAD_RANGE), 400
            _log_failure('overview', exc)
            return jsonify(_FAILED), 500
        return jsonify(result)

    @bp.route('/api/insights/video/<stem>', methods=['GET'])
    def insights_video(stem):
        clean = safe_stem(stem)
        if clean is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        try:
            doc = _compute(clean, _video_files())
        except Exception as exc:
            _log_failure('video insights', exc)
            return jsonify(_FAILED), 500
        return jsonify(doc)

    # ------------------------------------------------------------------- jobs

    def _progress_file(save_dir):
        """``(doc|None, mtime|None)`` of ``<save_dir>/progress.json``."""
        path = os.path.join(str(save_dir), 'progress.json')
        try:
            mtime = os.path.getmtime(path)
            with open(path, encoding='utf-8') as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            try:
                return None, os.path.getmtime(path)
            except OSError:
                return None, None
        return (doc if isinstance(doc, dict) else None), mtime

    def _kind(job_id, stage):
        if str(job_id).startswith('posture_') or 'posture' in str(stage or '').lower():
            return 'posture'
        return 'match'

    @bp.route('/api/jobs', methods=['GET'])
    def list_jobs():
        now = time.time()
        rows = []  # (sort_ts, order, row)
        known = set()
        try:
            for order, (job_id, job) in enumerate(list((jobs() or {}).items())):
                if not isinstance(job, dict):
                    continue
                video = _video_stem(job.get('video_name') or job_id)
                known.add(str(job_id))
                known.add(video)
                status = job.get('status')
                stage = job.get('stage')
                updated = _epoch(job.get('updated'))
                sort_ts = updated
                if sort_ts is None and job.get('save_dir'):
                    doc, mtime = _progress_file(job['save_dir'])
                    sort_ts = _epoch((doc or {}).get('updated'))
                    if sort_ts is None:
                        sort_ts = mtime
                if sort_ts is None:
                    # No timestamp: a live job is current, a finished one is old.
                    sort_ts = now if status in ACTIVE_STATUSES else 0.0
                rows.append((sort_ts, order, {
                    'id': str(job_id), 'video': video, 'kind': _kind(job_id, stage),
                    'status': status if isinstance(status, str) else None,
                    'stage': stage if isinstance(stage, str) else None,
                    'progress': _int_pct(job.get('progress', 0)),
                    'message': _first_line(job.get('message')),
                    'updated': updated,
                }))

            base = len(rows)
            root = Path(outputs())
            try:
                entries = sorted(root.iterdir(), key=lambda p: p.name)
            except OSError:
                entries = []
            for offset, path in enumerate(entries):
                stem = path.name
                if stem in known or 'posture_' + stem in known:
                    continue
                try:
                    if not path.is_dir():
                        continue
                except OSError:
                    continue
                doc, mtime = _progress_file(path)
                if doc is None and mtime is None:
                    continue
                updated = _epoch((doc or {}).get('updated'))
                if updated is None:
                    updated = mtime
                if updated is None or now - updated > DETACHED_WINDOW_SEC:
                    continue
                doc = doc or {}
                stage = doc.get('stage')
                rows.append((updated, base + offset, {
                    'id': stem, 'video': stem, 'kind': _kind(stem, stage),
                    'status': 'detached',
                    'stage': stage if isinstance(stage, str) else None,
                    'progress': _int_pct(doc.get('pct', 0)),
                    'message': '', 'updated': updated,
                }))
        except Exception as exc:
            _log_failure('job listing', exc)
            return jsonify(_FAILED), 500
        rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
        return jsonify([r[2] for r in rows])

    app.register_blueprint(bp)
