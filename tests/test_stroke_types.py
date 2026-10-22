from badminton_analysis.stroke import stroke_types as st


def test_twelve_types_seven_groups():
    assert st.STROKE_TYPES == ("short_serve", "long_serve", "clear", "lift", "smash", "drop",
                               "net_shot", "block", "cross_net", "net_rush", "drive", "push")
    assert st.GROUPS == ("smash", "clear", "drop", "net", "lift", "drive", "serve")
    assert set(st.GROUP_OF) == set(st.STROKE_TYPES)
    assert set(st.GROUP_OF.values()) == set(st.GROUPS)
    assert st.group_of("push") == "drive" and st.group_of("net_rush") == "net"
    assert st.group_of("unknown") is None and st.group_of(None) is None


def test_raw_shuttleset_types_map_to_twelve():
    assert len(st.RAW_TO_TYPE) == 19          # 18 CSV spellings + the README's 過渡切球
    assert set(st.RAW_TO_TYPE.values()) == set(st.STROKE_TYPES)
    assert st.type_of_raw(" 挑球 ") == "lift" and st.type_of_raw("防守回挑") == "lift"
    assert st.type_of_raw("點扣") == "smash" and st.type_of_raw("推球") == "push"
    assert st.type_of_raw("未知球種") is None and st.type_of_raw("") is None
    assert st.type_of_raw(None) is None


def test_profiles():
    assert st.profile_for("clear") == "high_clear"
    assert st.profile_for("drop") == "drop_shot"
    assert st.profile_for("short_serve") == st.profile_for("long_serve") == "serve"
    assert st.profile_for("smash") == "smash"
    for s in ("lift", "net_shot", "block", "cross_net", "net_rush", "drive", "push", "unknown"):
        assert st.profile_for(s) is None
    # drill mode passes profile names straight through
    for p in st.PROFILES:
        assert st.profile_for(p) == p
    assert st.PROFILE_GROUP == {"high_clear": "clear", "smash": "smash", "drop_shot": "drop",
                                "serve": "serve"}
