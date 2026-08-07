import pytest
from badminton_analysis.posture import coach_kb as kb


def test_constants():
    assert kb.SUPPORTED_LANGS == ("en", "zh-Hant", "zh-Hans")
    assert set(kb.METRICS) == {"elbow_extension","trunk_rotation","wrist_flexion",
                               "knee_flexion","hip_shoulder_separation","weight_transfer"}
    assert set(kb.IMPACT_CATEGORIES) == {"power","accuracy","consistency","injury_risk"}
    assert set(kb.STROKES) == {"high_clear", "smash", "drop_shot", "serve"}


def test_lookup_weakness_specific_and_fallback():
    # A specific entry exists for smash + elbow under.
    e = kb.lookup_weakness("elbow_extension", "smash", "under")
    assert e["impact"] in kb.IMPACT_CATEGORIES
    assert "mechanism_key" in e and "drill_key" in e
    # An unusual combination still resolves via fallback (never raises/None).
    e2 = kb.lookup_weakness("wrist_flexion", "serve", "over")
    assert e2 is not None and "mechanism_key" in e2


def test_lookup_strength_resolves():
    s = kb.lookup_strength("trunk_rotation", "smash")
    assert s is not None and "text_key" in s


def test_t_resolves_english_and_formats():
    # every mechanism_key/drill_key referenced by a weakness entry must resolve in English
    e = kb.lookup_weakness("elbow_extension", "smash", "under")
    assert isinstance(kb.t("en", e["mechanism_key"]), str) and kb.t("en", e["mechanism_key"])
    assert isinstance(kb.t("en", e["drill_key"]), str) and kb.t("en", e["drill_key"])


def test_t_missing_key_falls_back_to_key_string():
    assert kb.t("en", "__nope__") == "__nope__"


def test_every_weakness_entry_text_keys_exist_in_english():
    # Iterate the whole KB; every referenced key must exist in English.
    for entry in kb.iter_all_entries():
        for k in ("mechanism_key", "drill_key", "text_key"):
            if k in entry:
                assert kb.t("en", entry[k]) != entry[k] or entry[k] == "", \
                    f"missing English text for {entry[k]}"


def test_lookup_weakness_final_generic_fallback():
    # Test the final fallback (_GENERIC_FALLBACK) by using a non-existent metric.
    # Since every real metric has both (metric, "*", "under") and (metric, "*", "over")
    # entries, we use an invalid metric that forces the third fallback level.
    result = kb.lookup_weakness("__nonexistent__", "smash", "under")
    assert result is kb._GENERIC_FALLBACK
    assert result["mechanism_key"] == "generic_mech"
