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
    assert 'no labelled test shots' in _final(capsys)['reason']
    assert not list((weights2 / 'candidates').glob('*.pt'))


def test_main_turns_an_exception_into_a_final_line(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(retrain, 'run', lambda *a, **k: 1 / 0)
    code = retrain.main(['--labels-dir', 'a', '--results-dir', 'b', '--weights-dir', 'c',
                         '--project-root', 'd'])
    assert code == 1 and _final(capsys)['kept'] is False
