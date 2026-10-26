"""Match strategy report routes for the Kestrel UI.

A Flask Blueprint over ``badminton_analysis.strategy``: the stored report plus its
job state, a regenerate action and a Markdown export. It owns no state: ``app.py``
passes in getters resolved on every request, so a test that re-points ``OUTPUTS`` /
``ATHLETES_PATH`` is honoured, and it queues work through the ``queue`` callable
(``app.queue_strategy_job``).

Error bodies are static bilingual strings (``中文 / English``); nothing from an
exception reaches a response.
"""
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from badminton_analysis.library import athletes as libathletes
from badminton_analysis.strategy import config as strategy_config
from badminton_analysis.strategy import report as strategy_report

_UNKNOWN_VIDEO = {'error': '未知的视频 / unknown video'}
_NO_REPORT = {'error': '尚无策略报告 / no strategy report yet'}
_BAD_FOCUS = {'error': '无效的视角 / invalid focus'}
_NO_ANALYSIS = {'error': '该视频尚无比赛分析 / this video has no match analysis yet'}
_BUSY = {'error': '该视频已在分析或排队中 / this video is already being analysed or queued'}
_FOCUS = ('upper', 'lower')
_ANALYSIS_FILES = ('rally_segments.json', 'insights.json')
_LANGS = ('en', 'zh')


def register(app, *, outputs, athletes_path, athletes_lock, jobs, queue, safe_stem,
             settings_path, position=None):
    """Register the routes on ``app``.

    ``outputs()`` / ``athletes_path()`` / ``settings_path()`` return the paths,
    ``athletes_lock()`` the lock guarding the store, ``jobs()`` the live job dict,
    ``queue(stem, focus)`` returns ``(ok, job_id, queued)``, ``safe_stem(name)`` the
    validated stem or ``None`` and ``position(job_id, job)`` the queue place (or
    ``None``). All are called per request.
    """
    bp = Blueprint('strategy', __name__)

    def _out_dir(stem):
        """The run directory of a safe stem, else None."""
        name = safe_stem(stem)
        if name is None:
            return None
        path = Path(outputs()) / name
        return path if path.is_dir() else None

    def _record(stem):
        with athletes_lock():
            data = libathletes.load(athletes_path())
        return libathletes.resolve_record(data, stem)

    def _job_view(stem):
        job_id = 'strategy_' + stem
        job = (jobs() or {}).get(job_id)
        if not isinstance(job, dict):
            return None
        status = job.get('status')
        if status not in ('pending', 'running', 'paused', 'resuming'):
            return None
        place = position(job_id, job) if position is not None else None
        return {'id': job_id, 'status': status, 'position': place}

    @bp.route('/api/strategy/<stem>', methods=['GET'])
    def get_strategy(stem):
        if stem.endswith('.md'):
            return _markdown(stem[:-3])
        out = _out_dir(stem)
        if out is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        enabled = strategy_config.load_config(settings_path()).enabled
        doc = strategy_report.read_report(out)
        job = _job_view(stem)
        stale = False
        if doc is not None:
            stale = bool(strategy_report.is_stale(out, doc, _record(stem)))
        if job is not None:
            status = job['status']
        elif doc is not None:
            status = doc.get('status') or 'none'
        else:
            status = 'none'
        return jsonify({'enabled': enabled, 'status': status, 'stale': stale,
                        'job': job, 'report': doc})

    @bp.route('/api/strategy/<stem>/regenerate', methods=['POST'])
    def regenerate(stem):
        out = _out_dir(stem)
        if out is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        focus = body.get('focus')
        if focus is not None and focus not in _FOCUS:
            return jsonify(_BAD_FOCUS), 400
        if not any((out / name).is_file() for name in _ANALYSIS_FILES):
            return jsonify(_NO_ANALYSIS), 404
        ok, job_id, queued = queue(stem, focus)
        if not ok:
            return jsonify(_BUSY), 409
        job = (jobs() or {}).get(job_id) or {}
        place = position(job_id, job) if position is not None else None
        return jsonify({'ok': True, 'job_id': job_id, 'queued': bool(queued),
                        'position': place})

    def _markdown(stem):
        out = _out_dir(stem)
        if out is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        doc = strategy_report.read_report(out)
        if doc is None:
            return jsonify(_NO_REPORT), 404
        lang = request.args.get('lang', 'en')
        lang = lang if lang in _LANGS else 'en'
        text = strategy_report.render_markdown(doc, lang)
        name = f'{stem}-strategy-{lang}.md'
        if name.isascii() and '"' not in name:
            disposition = f'attachment; filename="{name}"'
        else:
            # A non-ASCII stem cannot sit in a plain header value (RFC 5987).
            ascii_name = ''.join(c if c.isascii() and c != '"' else '_' for c in name)
            disposition = (f'attachment; filename="{ascii_name}"; '
                           f"filename*=UTF-8''{quote(name)}")
        return Response(text, mimetype='text/markdown', headers={
            'Content-Type': 'text/markdown; charset=utf-8',
            'Content-Disposition': disposition})

    app.register_blueprint(bp)
