from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "static" / "kestrel" / "legacy" / "kestrel-legacy.js").read_text(encoding="utf-8")


def test_format_select_and_payload():
    assert "cfg-format" in JS
    assert "match_format: document.getElementById('cfg-format').value" in JS


def test_format_defaults_from_doubles_tag():
    assert "indexOf('doubles')" in JS


def test_format_default_uses_library_stem_not_filename():
    # wiz.video is a file name; /api/videos[].name is the stem.
    assert r"wantStem = wiz.video.replace(/\.[^.]+$/, '')" in JS
    assert "x.name === wantStem" in JS


def test_players_panel_reads_players_json():
    assert "/players.json" in JS
    assert "players-box" in JS


def test_unvalidated_and_withheld_notes():
    assert "unvalidated" in JS.lower()
    assert "Stroke types withheld for doubles" in JS


def test_technique_box_says_withheld_for_doubles():
    # A doubles run has no technique data by design: say so, not "No technique data yet".
    assert "Technique scoring withheld for doubles" in JS
    assert "双打不提供技术评分" in JS
    tech = JS[JS.index("// Technique analysis."):]
    tech = tech[:tech.index("}).catch(")]
    assert "No technique data yet" not in tech          # the empty text comes from the helper
    assert "techEmptyText()" in tech
    players = JS[JS.index("/players.json"):JS.index("// Technique analysis.")]
    assert "doublesRun = true" in players               # either fetch may resolve first
    assert "techEmptyText()" in players


def test_player_names_are_escaped():
    i = JS.index("/players.json")
    window = JS[i:i + 4000]
    assert "escHTML(String(p.name || p.id))" in window
    assert "escHTML(String(d.reason))" in window


def test_heartbeat_shown_while_reencoding():
    # The players-video transcode runs after the subprocess: keep the message visible.
    assert "d.status === 'running' || d.status === 'reencoding'" in JS


def test_identity_video_link_only_when_rendered():
    # players.json records the render outcome; a failed or skipped render must not
    # leave a link to a file that does not exist.
    assert "detect_' + encodeURIComponent(stem) + '_players.mp4" not in JS
    assert "d.identity_video" in JS and "vid.status === 'ok'" in JS
    assert "vid.file" in JS
