from badminton_analysis.posture import coach_kb as kb


def test_all_languages_cover_every_key():
    en_keys = set(kb.I18N["en"].keys())
    assert en_keys, "English table must be populated"
    for lang in ("zh-Hant", "zh-Hans"):
        missing = en_keys - set(kb.I18N[lang].keys())
        assert not missing, f"{lang} missing keys: {sorted(missing)}"


def test_chinese_tables_are_distinct_from_english():
    # Sanity: the Chinese tables should not be verbatim copies of English
    # for the coaching text (at least the mechanism/drill/verdict keys differ).
    sample_keys = [k for k in kb.I18N["en"] if k.startswith(("mech", "drill", "verdict",
                                                             "elbow", "trunk", "wrist",
                                                             "knee", "hip", "weight", "generic"))]
    assert sample_keys, "expected some coaching-text keys"
    differing = sum(1 for k in sample_keys if kb.I18N["zh-Hans"].get(k) != kb.I18N["en"].get(k))
    assert differing >= max(1, len(sample_keys) // 2)


def test_hant_and_hans_are_not_identical():
    # Traditional vs Simplified should differ on most text (idiomatic, not identical).
    common = set(kb.I18N["zh-Hant"]) & set(kb.I18N["zh-Hans"])
    text_keys = [k for k in common if any(k.startswith(p) for p in
                 ("mech","drill","verdict","elbow","trunk","wrist","knee","hip","weight","generic"))]
    if text_keys:
        differing = sum(1 for k in text_keys
                        if kb.I18N["zh-Hant"][k] != kb.I18N["zh-Hans"][k])
        assert differing >= 1
