"""Stroke model card routes (stroke correction spec 2026-10-28 section 6.1).

``GET /api/stroke-model`` is the Pipeline card, ``POST /api/stroke-model/retrain`` queues the
training job and ``POST /api/stroke-model/rollback`` swaps the previous model back in. Owns no
state: ``app.py`` passes callables (status with its cache, the active job, the two queue
functions, rollback). Error bodies are static bilingual strings.
"""
from flask import Blueprint, jsonify

_BUSY = {'error': '模型训练或重新标注正在进行 / a training or relabel job is already running',
         'reason': 'busy'}
_NOT_ENOUGH = {'error': '标注数量不足，暂不能训练 / not enough labels to retrain yet',
               'reason': 'not_enough_labels'}
_NO_HISTORY = {'error': '没有可回滚的旧模型 / no earlier model to roll back to'}
_FAILED = {'error': '操作失败，请重试 / the operation failed, please retry'}


def register(app, *, status, active_job, queue_retrain, rollback, can_rollback=lambda: False):
    """Register the routes on ``app``.

    ``status()`` -> the card dict (cached by the caller), ``active_job()`` -> the active
    training/relabel job as a dict or None, ``queue_retrain()`` -> ``(ok, job_id, queued)``,
    ``rollback()`` -> ``'busy' | 'none' | 'ok'``, ``can_rollback()`` -> whether an earlier model exists.
    """
    bp = Blueprint('stroke_model', __name__)

    @bp.route('/api/stroke-model', methods=['GET'])
    def get_model():
        try:
            doc = dict(status())
        except Exception:
            return jsonify(_FAILED), 500
        doc['job'] = active_job()
        try:
            doc['can_rollback'] = bool(can_rollback())
        except Exception:
            doc['can_rollback'] = False
        return jsonify(doc)

    @bp.route('/api/stroke-model/retrain', methods=['POST'])
    def retrain():
        if active_job() is not None:
            return jsonify(_BUSY), 409
        try:
            card = status()
        except Exception:
            return jsonify(_FAILED), 500
        if not card.get('can_retrain'):
            return jsonify(dict(_NOT_ENOUGH, missing=list(card.get('missing') or []))), 409
        ok, job_id, queued = queue_retrain()
        if not ok:
            return jsonify(_BUSY), 409
        return jsonify({'job_id': job_id, 'queued': queued})

    @bp.route('/api/stroke-model/rollback', methods=['POST'])
    def do_rollback():
        try:
            outcome = rollback()
        except Exception:
            return jsonify(_FAILED), 500
        if outcome == 'busy':
            return jsonify(_BUSY), 409
        if outcome == 'none':
            return jsonify(_NO_HISTORY), 404
        return jsonify({'ok': True})

    app.register_blueprint(bp)
