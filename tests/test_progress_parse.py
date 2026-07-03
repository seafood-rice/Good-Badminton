# tests/test_progress_parse.py
import app as webapp


def test_parse_progress_line_valid():
    assert webapp._parse_progress_line("PROGRESS 42 analyzing") == (42, "analyzing")
    assert webapp._parse_progress_line("  PROGRESS 5 loading\n") == (5, "loading")


def test_parse_progress_line_rejects_noise():
    assert webapp._parse_progress_line("Posture analysis: 8 reps") is None
    assert webapp._parse_progress_line("PROGRESS x analyzing") is None
    assert webapp._parse_progress_line("") is None
