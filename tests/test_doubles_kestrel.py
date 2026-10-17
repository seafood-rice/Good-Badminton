from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "static" / "kestrel.js").read_text(encoding="utf-8")


def test_format_select_and_payload():
    assert "cfg-format" in JS
    assert "match_format: document.getElementById('cfg-format').value" in JS


def test_format_defaults_from_doubles_tag():
    assert "indexOf('doubles')" in JS


def test_format_default_uses_library_stem_not_filename():
    # wiz.video is a file name; lib.videos[].name is the stem.
    assert r"x.name === wiz.video.replace(/\.[^.]+$/, '')" in JS


def test_players_panel_reads_players_json():
    assert "/players.json" in JS
    assert "players-box" in JS


def test_unvalidated_and_withheld_notes():
    assert "unvalidated" in JS.lower()
    assert "Stroke types withheld for doubles" in JS


def test_player_names_are_escaped():
    i = JS.index("/players.json")
    window = JS[i:i + 4000]
    assert "escHTML(String(p.name || p.id))" in window
    assert "escHTML(String(d.reason))" in window
