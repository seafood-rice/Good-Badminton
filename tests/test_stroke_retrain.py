"""badminton_analysis.stroke.retrain: the child that trains, scores, decides and promotes."""
import json
import subprocess

from badminton_analysis.stroke import model_eval, retrain


def _setup(monkeypatch, tmp_path, *, trained=True, shots=True, cand_acc=0.8, live_acc=0.7, live=True):
    weights = tmp_path / 'weights'
    weights.mkdir()
    if live:
        (weights / 'stroke-classifier.pt').write_bytes(b'live')

    class P:
        returncode = 0 if trained else 3

    def fake_run(cmd, cwd=None):
        if trained:
            out = cmd[cmd.index('--out') + 1]
            open(out, 'wb').write(b'cand')
        return P()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(model_eval, 'test_shots',
                        lambda l, r: [('t', [{'frame': 1}], ['clear'])] if shots else [])
    monkeypatch.setattr(model_eval, '_label_docs', lambda l, s: [])

    def score(w, shots_, r):
        acc = cand_acc if 'candidates' in str(w) else live_acc
        return {'accuracy': acc, 'unknown_rate': 0.0}

    monkeypatch.setattr(model_eval, 'score_weights', score)
    return weights


def _final(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_keep_promotes_and_prints_final_line(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 0
    out = _final(capsys)
    assert out['kept'] is True and out['candidate']['accuracy'] == 0.8 and out['live']['accuracy'] == 0.7
    assert (weights / 'stroke-classifier.pt').read_bytes() == b'cand'
    assert model_eval.history(weights)[-1]['kept'] is True
    assert model_eval.history(weights)[-1]['test_videos'] == ['t']


def test_reject_keeps_live_and_deletes_candidate(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path, cand_acc=0.5)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 0
    assert _final(capsys)['kept'] is False
    assert (weights / 'stroke-classifier.pt').read_bytes() == b'live'
    assert not list((weights / 'candidates').glob('*.pt'))
    assert model_eval.history(weights)[-1]['kept'] is False


def test_no_live_model_scores_candidate_only(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path, live=False)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 0
    out = _final(capsys)
    assert out['kept'] is True and out['live'] is None


def test_training_failure_and_no_test_shots(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path, trained=False)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 1
    assert _final(capsys)['kept'] is False
    (tmp_path / 'x').mkdir()
    weights2 = _setup(monkeypatch, tmp_path / 'x', shots=False)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights2, tmp_path) == 1
    assert _final(capsys)['reason'] == {'code': 'no_test_shots'}
    assert not list((weights2 / 'candidates').glob('*.pt'))


def test_main_turns_an_exception_into_a_final_line(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(retrain, 'run', lambda *a, **k: 1 / 0)
    code = retrain.main(['--labels-dir', 'a', '--results-dir', 'b', '--weights-dir', 'c',
                         '--project-root', 'd'])
    assert code == 1 and _final(capsys)['kept'] is False


def test_retrain_records_bar_met_live_after_and_train_labels(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 0
    entry = model_eval.history(weights)[-1]
    assert entry['bar_met'] is False and 'train_labels' in entry
    assert entry['live_after']['accuracy'] == 0.8 and entry['live_after']['bar_met'] is False


def _leaky_trainer(monkeypatch, sidecar, live_test, shots_by_stem):
    def fake_run(cmd, cwd=None):
        # the owner flips splits while the trainer runs
        live_test.discard('t')
        live_test.add('v')
        out = cmd[cmd.index('--out') + 1]
        open(out, 'wb').write(b'cand')
        open(out.replace('.pt', '.json'), 'w').write(json.dumps(sidecar))

        class P:
            returncode = 0
        return P()

    monkeypatch.setattr(subprocess, 'run', fake_run)
    monkeypatch.setattr(model_eval, 'test_shots',
                        lambda l, r: [shots_by_stem[s] for s in sorted(live_test)])


def test_test_stems_are_snapshotted_before_training_and_leaked_ones_dropped(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path)
    shots_by_stem = {'t': ('t', [{'frame': 1}], ['clear']), 'u': ('u', [{'frame': 2}], ['lift']),
                     'v': ('v', [{'frame': 3}], ['drop'])}
    # u was trained on (sidecar); v became a test video after the snapshot
    _leaky_trainer(monkeypatch, {'train_matches': ['owner:u'], 'val_matches': []}, {'t', 'u'}, shots_by_stem)
    seen = []

    def score(w, shots_, r):
        seen.append([s for s, _, _ in shots_])
        return {'accuracy': 0.8 if 'candidates' in str(w) else 0.7, 'unknown_rate': 0.0}

    monkeypatch.setattr(model_eval, 'score_weights', score)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 0
    assert seen == [['t'], ['t']]
    assert model_eval.history(weights)[-1]['test_videos'] == ['t']


def test_all_test_stems_leaked_is_no_test_shots(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path)
    shots_by_stem = {'t': ('t', [{'frame': 1}], ['clear'])}
    _leaky_trainer(monkeypatch, {'val_matches': ['dji:t']}, {'t'}, shots_by_stem)
    monkeypatch.setattr(model_eval, 'test_shots', lambda l, r: [shots_by_stem['t']])
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 1
    assert _final(capsys)['reason'] == {'code': 'no_test_shots'}
    assert not list((weights / 'candidates').glob('*.pt'))


def test_promote_phase_line_is_printed_before_the_swap_only_when_kept(monkeypatch, tmp_path, capsys):
    weights = _setup(monkeypatch, tmp_path)
    order = []
    real = model_eval.promote
    monkeypatch.setattr(model_eval, 'promote',
                        lambda *a, **k: (order.append(capsys.readouterr().out), real(*a, **k))[1])
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 0
    assert 'PHASE promote' in order[0].splitlines()
    (tmp_path / 'x').mkdir()
    weights2 = _setup(monkeypatch, tmp_path / 'x', cand_acc=0.1)
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights2, tmp_path) == 0
    assert 'PHASE promote' not in capsys.readouterr().out


def test_no_test_shots_reject_keeps_live_after_of_the_live_model(monkeypatch, tmp_path, capsys):
    from badminton_analysis.strategy import facts
    weights = _setup(monkeypatch, tmp_path)
    (weights / 'stroke-classifier.json').write_text(json.dumps({'created_utc': 'v1'}), encoding='utf-8')
    model_eval._append_history(weights, {
        'utc': 'x', 'kept': True, 'live_after': {'version': 'v1', 'accuracy': 0.7,
                                                  'unknown_rate': 0.1, 'bar_met': False}})
    monkeypatch.setattr(model_eval, 'test_shots', lambda l, r: [])
    assert retrain.run(tmp_path / 'l', tmp_path / 'r', weights, tmp_path) == 1
    after = model_eval.history(weights)[-1]['live_after']
    assert after['version'] == 'v1' and after['accuracy'] == 0.7 and after['bar_met'] is False
    monkeypatch.setattr(facts, 'WEIGHTS_DIR', weights)
    assert facts._stroke_model_quality() == {'accuracy': 0.7, 'bar_met': False}
