import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    'ai_overlap', Path(__file__).resolve().parents[1] / 'scripts' / 'ai_overlap.py')
ai_overlap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ai_overlap)


def test_matches_is_path_prefix_not_substring():
    assert ai_overlap._matches('app.py', ['app.py'])
    assert ai_overlap._matches('badminton_analysis/stroke/hit.py', ['badminton_analysis/stroke'])
    assert not ai_overlap._matches('badminton_analysis/stroke_recog/x.py', ['badminton_analysis/stroke'])


def test_no_prefixes_exits_zero(capsys):
    assert ai_overlap.main([]) == 0


def test_always_exits_zero_on_overlap_or_none():
    assert ai_overlap.main(['definitely/not/a/real/path']) == 0
