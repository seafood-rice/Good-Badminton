"""Owner stroke-label routes (stroke correction spec 2026-10-28 section 5.3).

``GET`` / ``PUT /api/stroke-labels/<stem>`` over ``badminton_analysis.stroke.owner_labels``.
A save carries the revision it was based on; a stale one is refused (409) so two tabs
cannot silently overwrite each other. A save then rewrites ``strokes.json`` with the labels
applied, except while an analysis job is active on the video (that job's own
``strokes.json`` write re-applies them) or when the video has no ``strokes.json``.

Like ``rally_labels_routes``, it owns no state: ``app.py`` passes getters resolved per
request. Error bodies are static bilingual strings.
"""
from flask import Blueprint, jsonify, request

from badminton_analysis.stroke import owner_labels

_BAD_BODY = {'error': '请求内容无效 / invalid request body'}
_UNKNOWN_VIDEO = {'error': '未知的视频 / unknown video'}
_STALE = {'error': '标注已在别处更改，请刷新 / labels were changed elsewhere; reload'}
_UNREADABLE = {'error': '标注文件不可读，未被覆盖 / stroke label file unreadable, not overwritten'}
_SPLIT_LOCKED = {'error': '该视频已用于训练或评分，不能更改训练或测试划分 / '
                          'this video was already used for training or scoring; its split cannot change'}
_SAVE_FAILED = {'error': '无法保存标注，请重试 / could not save labels, please retry'}


def register(app, *, labels_dir, outputs, lock, safe_stem, job_active, split_locked):
    """Register the routes on ``app``; every argument is a getter called per request."""
    bp = Blueprint('stroke_labels', __name__)

    @bp.route('/api/stroke-labels/<stem>', methods=['GET'])
    def get_labels(stem):
        stem = safe_stem(stem)
        if stem is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        with lock():
            doc = owner_labels.load(owner_labels.labels_path(labels_dir(), stem))
        if doc is None:
            return jsonify(_UNREADABLE), 500
        return jsonify(dict(doc, split_locked=bool(split_locked(stem))))

    @bp.route('/api/stroke-labels/<stem>', methods=['PUT'])
    def put_labels(stem):
        stem = safe_stem(stem)
        if stem is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(_BAD_BODY), 400
        revision, split = body.get('revision'), body.get('split')
        labels = owner_labels.validate_labels(body.get('labels'))
        if type(revision) is not int or labels is None \
                or (split is not None and split not in owner_labels.SPLITS):
            return jsonify(_BAD_BODY), 400
        path = owner_labels.labels_path(labels_dir(), stem)
        with lock():
            current = owner_labels.load(path)
            if current is None:
                return jsonify(dict(_UNREADABLE, reason='unreadable')), 409
            if revision != current['revision']:
                return jsonify(dict(_STALE, reason='stale', revision=current['revision'])), 409
            if split is not None and split != current['split'] and split_locked(stem):
                return jsonify(dict(_SPLIT_LOCKED, reason='split_locked')), 409
            doc = {'schema': owner_labels.SCHEMA, 'revision': current['revision'] + 1,
                   'split': split or current['split'], 'labels': labels}
            try:
                owner_labels.save(path, doc)
            except OSError as exc:
                print(f'Could not save stroke labels for {stem}: {exc}')
                return jsonify(_SAVE_FAILED), 500
            if job_active(stem):
                return jsonify({'revision': doc['revision'], 'applied': False,
                                'apply_skipped': 'job_active'})
            result = owner_labels.apply_to_result(outputs() / stem, labels_dir(), stem)
        out = {'revision': doc['revision'], 'applied': result['applied']}
        if not result['applied']:
            out['apply_skipped'] = result['reason']
        return jsonify(out)

    app.register_blueprint(bp)
