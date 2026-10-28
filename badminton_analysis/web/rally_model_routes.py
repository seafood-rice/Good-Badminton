"""Rally model card routes (rally-winner-learning spec 7.3).

``GET /api/rally-model`` is the Pipeline card, ``POST /api/rally-model/retrain`` queues the
training job and ``POST /api/rally-model/rollback`` swaps the previous models back in (and
queues the re-segment job). Owns no state: ``app.py`` passes callables (status with its cache,
the active training job, the queue function, rollback). Error bodies are static bilingual strings.
"""
from flask import Blueprint, jsonify

_BUSY = {'error': '模型训练、重新标注或重新分段正在进行 / a training, relabel or re-segment job is already running',
         'reason': 'busy'}
_NOT_ENOUGH = {'error': '已标注的比赛或得分数量不足，暂不能训练 / not enough labelled matches or points to retrain yet',
               'reason': 'not_enough_labels'}
_NO_HISTORY = {'error': '没有可回滚的旧模型 / no earlier model to roll back to'}
_FAILED = {'error': '操作失败，请重试 / the operation failed, please retry'}


def register(app, *, status, active_job, queue_retrain, rollback):
    """Register the routes on ``app``.

    ``status()`` -> the card dict (cached by the caller; carries ``can_rollback``), ``active_job()`` ->
    the active training job as a dict or None, ``queue_retrain()`` -> ``(ok, job_id, queued)``,
    ``rollback()`` -> ``'busy' | 'none' | 'ok'``.
    """
    bp = Blueprint('rally_model', __name__)

    @bp.route('/api/rally-model', methods=['GET'])
    def get_model():
        try:
            doc = dict(status())
        except Exception:
            return jsonify(_FAILED), 500
        doc['job'] = active_job()
        doc['can_rollback'] = bool(doc.get('can_rollback')) and doc['job'] is None
        return jsonify(doc)

    @bp.route('/api/rally-model/retrain', methods=['POST'])
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

    @bp.route('/api/rally-model/rollback', methods=['POST'])
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
