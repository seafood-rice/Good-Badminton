"""Athlete, squad and match-record routes for the Kestrel telemetry UI.

A Flask Blueprint over ``badminton_analysis.library.athletes``. It owns no state:
``app.py`` passes in getters (resolved on every request, so a test that
re-points ``ATHLETES_PATH`` is honoured), and the one lock that serialises every
``load_for_update -> mutate -> save`` sequence.

Error bodies are static bilingual strings (``中文 / English``). An exception
text can carry an absolute filesystem path, so nothing from an exception ever
reaches a response; it is printed server-side instead.
"""
from flask import Blueprint, jsonify, request

from badminton_analysis.library import athletes as libathletes

_SAVE_FAILED = {'error': '无法保存运动员数据，请重试 / could not save athlete data, please retry'}
_STORE_UNREADABLE = {
    'error': '运动员存储不可读，未被覆盖 / athlete store unreadable, not overwritten'}
_BAD_BODY = {'error': '请求内容无效 / invalid request body'}
_UNKNOWN_VIDEO = {'error': '未知的视频 / unknown video'}
_UNKNOWN_ATHLETE = {'error': '未知的运动员 / unknown athlete'}
_UNKNOWN_SQUAD = {'error': '未知的小组 / unknown squad'}

# ValueError text raised by the store (add_*/update_*) -> (status, static body)
_ATHLETE_ERRORS = {
    'invalid': (400, {'error': '运动员名称无效 / invalid athlete name'}),
    'duplicate': (409, {'error': '运动员名称已存在 / athlete name already exists'}),
    'unknown_squad': (400, _UNKNOWN_SQUAD),
}
_SQUAD_ERRORS = {
    'invalid': (400, {'error': '小组名称无效 / invalid squad name'}),
    'duplicate': (409, {'error': '小组名称已存在 / squad name already exists'}),
}
# validate_match error code -> static body (all 400)
_MATCH_ERRORS = {
    'invalid_record': {'error': '比赛记录无效 / invalid match record'},
    'invalid_date': {'error': '日期无效，应为 YYYY-MM-DD / invalid date, expected YYYY-MM-DD'},
    'invalid_side': {'error': '选手信息无效 / invalid player entry'},
    'invalid_result': {'error': '比分结果无效 / invalid result'},
    'invalid_players': {'error': '双打选手信息无效 / invalid doubles players'},
    'unknown_athlete': _UNKNOWN_ATHLETE,
}
_GENERIC_VALUE_ERROR = (400, _BAD_BODY)


def _error(status_body):
    status, body = status_body
    return jsonify(body), status


def _json_object():
    """The request body if it is a JSON object, else None (not ``{}``-defaulted:
    a missing body must never be mistaken for an empty record)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


def register(app, *, store_path, lock, safe_stem):
    """Register the routes on ``app``.

    ``store_path()`` returns the store ``Path``, ``lock()`` the
    ``threading.Lock`` guarding it, ``safe_stem(name)`` the validated video stem
    or ``None``. All three are called per request.
    """
    bp = Blueprint('athletes', __name__)

    class _Edit:
        """One read-modify-write on the store. Use inside ``lock()`` only.

        ``load()`` is the strict read for a user edit: a corrupt file is
        remembered (not yet touched) and the edit starts from an empty store;
        ``save()`` then backs the bad file up byte-for-byte before replacing it,
        so a request that is rejected after loading never leaves a stray
        backup. An unreadable file may hold data this request never saw, so it
        is never written: ``load()`` returns an error response instead.
        """

        def __init__(self):
            self.path = store_path()
            self.corrupt = False

        def load(self):
            try:
                return libathletes.load_for_update(self.path), None
            except libathletes.StoreCorrupt:
                self.corrupt = True
                return libathletes.empty(), None
            except libathletes.StoreUnreadable:
                print('Athlete store unreadable; the edit was refused and nothing was written')
                return None, (jsonify(_STORE_UNREADABLE), 500)

        def save(self, data):
            if self.corrupt:
                backup = libathletes.backup_corrupt(self.path)
                print(f'Athlete store was corrupt; backed up to {backup} '
                      'and replaced by this edit')
            libathletes.save(data, self.path)

    def _save_failed(exc):
        print(f'Could not save athlete data: {exc}')
        return jsonify(_SAVE_FAILED), 500

    def _value_error(exc, table):
        return _error(table.get(str(exc), _GENERIC_VALUE_ERROR))

    # ---------------------------------------------------------------- listing

    @bp.route('/api/athletes', methods=['GET'])
    def list_athletes():
        with lock():
            data = libathletes.load(store_path())
        match_counts = {}
        for record in data['matches'].values():
            for athlete_id in libathletes.record_athlete_ids(record):   # once per match
                match_counts[athlete_id] = match_counts.get(athlete_id, 0) + 1
        member_counts = {}
        for athlete in data['athletes'].values():
            if athlete['squad_id'] is not None:
                member_counts[athlete['squad_id']] = member_counts.get(athlete['squad_id'], 0) + 1

        def by_name(item):
            return item['name'].casefold(), item['id']

        athletes = sorted(
            ({'id': aid, 'name': a['name'], 'squad_id': a['squad_id'],
              'matches': match_counts.get(aid, 0)} for aid, a in data['athletes'].items()),
            key=by_name)
        squads = sorted(
            ({'id': sid, 'name': s['name'], 'athletes': member_counts.get(sid, 0)}
             for sid, s in data['squads'].items()),
            key=by_name)
        return jsonify({'athletes': athletes, 'squads': squads})

    # --------------------------------------------------------------- athletes

    @bp.route('/api/athletes', methods=['POST'])
    def create_athlete():
        body = _json_object()
        if body is None:
            return jsonify(_BAD_BODY), 400
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                try:
                    data, athlete_id = libathletes.add_athlete(
                        data, body.get('name'), body.get('squad_id'))
                except ValueError as exc:
                    return _value_error(exc, _ATHLETE_ERRORS)
                edit.save(data)
        except OSError as exc:
            return _save_failed(exc)
        athlete = data['athletes'][athlete_id]
        return jsonify({'id': athlete_id, 'name': athlete['name'],
                        'squad_id': athlete['squad_id']}), 201

    @bp.route('/api/athletes/<athlete_id>', methods=['PUT'])
    def update_athlete(athlete_id):
        body = _json_object()
        if body is None:
            return jsonify(_BAD_BODY), 400
        fields = {key: body[key] for key in ('name', 'squad_id') if key in body}
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                try:
                    new = libathletes.update_athlete(data, athlete_id, **fields)
                except KeyError:
                    return jsonify(_UNKNOWN_ATHLETE), 404
                except ValueError as exc:
                    return _value_error(exc, _ATHLETE_ERRORS)
                if new != data:            # an empty or unchanged PUT writes nothing
                    edit.save(new)
                data = new
        except OSError as exc:
            return _save_failed(exc)
        athlete = data['athletes'][athlete_id]
        return jsonify({'ok': True, 'id': athlete_id, 'name': athlete['name'],
                        'squad_id': athlete['squad_id']})

    @bp.route('/api/athletes/<athlete_id>', methods=['DELETE'])
    def delete_athlete(athlete_id):
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                existed = athlete_id in data['athletes']
                data, rewritten = libathletes.delete_athlete(data, athlete_id)
                # Existence, not the rewrite count: an athlete with no matches is
                # still removed. An unknown id is a no-op and never writes (so a
                # corrupt store is not backed up and replaced for nothing).
                if existed:
                    edit.save(data)
        except OSError as exc:
            return _save_failed(exc)
        return jsonify({'ok': True, 'rewritten': rewritten})

    # ----------------------------------------------------------------- squads

    @bp.route('/api/squads', methods=['POST'])
    def create_squad():
        body = _json_object()
        if body is None:
            return jsonify(_BAD_BODY), 400
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                try:
                    data, squad_id = libathletes.add_squad(data, body.get('name'))
                except ValueError as exc:
                    return _value_error(exc, _SQUAD_ERRORS)
                edit.save(data)
        except OSError as exc:
            return _save_failed(exc)
        return jsonify({'id': squad_id, 'name': data['squads'][squad_id]['name']}), 201

    @bp.route('/api/squads/<squad_id>', methods=['PUT'])
    def update_squad(squad_id):
        body = _json_object()
        if body is None:
            return jsonify(_BAD_BODY), 400
        fields = {'name': body['name']} if 'name' in body else {}
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                try:
                    new = libathletes.update_squad(data, squad_id, **fields)
                except KeyError:
                    return jsonify(_UNKNOWN_SQUAD), 404
                except ValueError as exc:
                    return _value_error(exc, _SQUAD_ERRORS)
                if new != data:            # an empty or unchanged PUT writes nothing
                    edit.save(new)
                data = new
        except OSError as exc:
            return _save_failed(exc)
        return jsonify({'ok': True, 'id': squad_id, 'name': data['squads'][squad_id]['name']})

    @bp.route('/api/squads/<squad_id>', methods=['DELETE'])
    def delete_squad(squad_id):
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                existed = squad_id in data['squads']
                data, cleared = libathletes.delete_squad(data, squad_id)
                if existed:                # an unknown id is a no-op: no write
                    edit.save(data)
        except OSError as exc:
            return _save_failed(exc)
        return jsonify({'ok': True, 'cleared': cleared})

    # ---------------------------------------------------------------- matches

    @bp.route('/api/matches/<stem>', methods=['GET'])
    def get_match(stem):
        clean_stem = safe_stem(stem)
        if clean_stem is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        with lock():
            data = libathletes.load(store_path())
        return jsonify(libathletes.get_match(data, clean_stem))

    @bp.route('/api/matches/<stem>', methods=['PUT'])
    def put_match(stem):
        clean_stem = safe_stem(stem)
        if clean_stem is None:
            return jsonify(_UNKNOWN_VIDEO), 404
        record = _json_object()
        if record is None:
            # Absent / unparseable / non-object bodies are rejected, never read
            # as "{}": that would silently delete the record.
            return jsonify(_MATCH_ERRORS['invalid_record']), 400
        try:
            with lock():
                edit = _Edit()
                data, err = edit.load()
                if err:
                    return err
                # The store is needed to check that a referenced athlete exists.
                clean, code = libathletes.validate_match(record, data)
                if code:
                    return jsonify(_MATCH_ERRORS.get(code, _MATCH_ERRORS['invalid_record'])), 400
                data = libathletes.set_match(data, clean_stem, clean)
                edit.save(data)
        except OSError as exc:
            return _save_failed(exc)
        return jsonify({'ok': True, 'match': libathletes.get_match(data, clean_stem)})

    app.register_blueprint(bp)
