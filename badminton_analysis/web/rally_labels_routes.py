"""Owner rally-label routes (rally outcome spec section 7).

``GET`` / ``PUT /api/rally-labels/<stem>`` over ``badminton_analysis.outcomes.labels``.
A save carries the revision it was based on; a stale one is refused (409) so two
tabs cannot silently overwrite each other. Every save rebuilds
``rally_outcomes.json`` for the whole match, except while an analysis job is
active on the video: that job's own final build picks the labels up.

Like ``athletes_routes``, it owns no state: ``app.py`` passes getters resolved
per request. Error bodies are static bilingual strings.
"""
from flask import Blueprint, jsonify, request

from badminton_analysis.outcomes import build as outcomes_build
from badminton_analysis.outcomes import labels as liblabels

_BAD_BODY = {'error': '请求内容无效 / invalid request body'}
_UNKNOWN_VIDEO = {'error': '未知的视频 / unknown video'}
_STALE = {'error': '标注已在别处更改，请刷新 / labels were changed elsewhere; reload'}
_UNREADABLE = {'error': '标注文件不可读，未被覆盖 / rally label file unreadable, not overwritten'}
_SAVE_FAILED = {'error': '无法保存标注，请重试 / could not save labels, please retry'}


def register(app, *, labels_dir, athletes_path, outputs, lock, safe_stem, job_active):
    """Register the routes on ``app``; every argument is a getter called per request."""
    bp = Blueprint('rally_labels', __name__)

    @bp.route('/api/rally-labels/<stem>', methods=['GET'])
    def get_labels(stem):
        stem = safe_stem(stem)
        if stem is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        with lock():
            doc = liblabels.load(liblabels.labels_path(labels_dir(), stem))
        if doc is None:
            return jsonify(_UNREADABLE), 500
        return jsonify(doc)

    @bp.route('/api/rally-labels/<stem>', methods=['PUT'])
    def put_labels(stem):
        stem = safe_stem(stem)
        if stem is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        body = request.get_json(silent=True)
        revision = body.get('revision') if isinstance(body, dict) else None
        labels = liblabels.validate_labels(body.get('labels')) if isinstance(body, dict) else None
        if type(revision) is not int or labels is None:
            return jsonify(_BAD_BODY), 400
        path = liblabels.labels_path(labels_dir(), stem)
        with lock():
            current = liblabels.load(path)
            if current is None:
                return jsonify(dict(_UNREADABLE, reason='unreadable')), 409
            if revision != current['revision']:
                return jsonify(dict(_STALE, reason='stale', revision=current['revision'])), 409
            doc = {'schema': liblabels.SCHEMA, 'revision': current['revision'] + 1, 'labels': labels}
            try:
                liblabels.save(path, doc)
            except OSError as exc:
                print(f'Could not save rally labels for {stem}: {exc}')
                return jsonify(_SAVE_FAILED), 500
            if job_active(stem):
                return jsonify({'revision': doc['revision'], 'outcomes_rebuilt': [],
                                'rebuild_skipped': 'job_active'})
            rebuilt = outcomes_build.rebuild_match(outputs(), stem, labels_dir(), athletes_path())
        return jsonify({'revision': doc['revision'], 'outcomes_rebuilt': rebuilt})

    app.register_blueprint(bp)
