# tests/test_player_positions_i18n.py
from badminton_analysis.visualization.player_positions import PlayerPositionVisualizer, _LANG


def test_language_selects_string_table():
    en = PlayerPositionVisualizer.__new__(PlayerPositionVisualizer)
    en.strings = _LANG["en"]
    assert en.strings["heatmap_title"] == "Player Position Heatmap"
    zh = PlayerPositionVisualizer.__new__(PlayerPositionVisualizer)
    zh.strings = _LANG["zh"]
    assert zh.strings["heatmap_title"] == "球员位置热力图"


def test_lang_tables_have_same_keys():
    assert set(_LANG["zh"]) == set(_LANG["en"])


def test_unknown_language_falls_back_to_zh(tmp_path):
    detections = tmp_path / "detections.jsonl"
    detections.write_text("", encoding="utf-8")
    viz = PlayerPositionVisualizer(str(detections), output_dir=str(tmp_path / "out"), language="fr")
    assert viz.strings is _LANG["zh"]
    assert viz.language == "fr"
