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


def test_en_instance_after_zh_instance_uses_default_font(tmp_path):
    """Regression test: constructing a zh visualizer must not leak SimHei into
    global matplotlib rcParams, which would otherwise make a later en visualizer
    (in the same process) render its charts in the Chinese font instead of the
    matplotlib default.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    detections = tmp_path / "detections.jsonl"
    detections.write_text("", encoding="utf-8")

    # Build a real zh instance first - this is what previously mutated global rcParams.
    zh = PlayerPositionVisualizer(
        str(detections), output_dir=str(tmp_path / "out_zh"), language="zh"
    )

    # Now build a real en instance in the SAME process.
    en = PlayerPositionVisualizer(
        str(detections), output_dir=str(tmp_path / "out_en"), language="en"
    )

    # Global rcParams must not have been forced to the Chinese font by the zh
    # instance's construction - this is the actual leak the defect described.
    # Checked first (before touching per-instance font attributes) so a
    # regression here fails on the real bug, not on an unrelated attribute name.
    family_str = str(plt.rcParams["font.family"]).lower()
    assert "simhei" not in family_str
    sans_serif_str = str(plt.rcParams["font.sans-serif"]).lower()
    assert "simhei" not in sans_serif_str

    # The en instance's font must be an explicit matplotlib default, NOT SimHei,
    # and must NOT depend on whatever rcParams happens to be at render time.
    assert "simhei" not in en.font_prop.get_name().lower()

    # The zh instance must actually be using the Chinese font (sanity check the
    # fixture/test itself is exercising the real font-loading code path).
    assert zh.font_prop.get_name() != en.font_prop.get_name()
