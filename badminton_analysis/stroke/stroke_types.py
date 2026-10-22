"""The one stroke taxonomy (spec 2026-10-22 section 3): 12 types, 7 groups, technique profiles.

Every stroke label in the app is one of STROKE_TYPES or UNKNOWN. Groups are what the
distribution, filters and insights count; profiles are the technique reference-range sets
(analysis/reference_ranges.py) a type is scored against. A type without a profile is
labelled but not scored.
"""
STROKE_TYPES = ("short_serve", "long_serve", "clear", "lift", "smash", "drop",
                "net_shot", "block", "cross_net", "net_rush", "drive", "push")
UNKNOWN = "unknown"
GROUPS = ("smash", "clear", "drop", "net", "lift", "drive", "serve")
GROUP_OF = {
    "short_serve": "serve", "long_serve": "serve", "clear": "clear", "lift": "lift",
    "smash": "smash", "drop": "drop", "net_shot": "net", "block": "net", "cross_net": "net",
    "net_rush": "net", "drive": "drive", "push": "drive",
}
RAW_TO_TYPE = {
    "發短球": "short_serve", "發長球": "long_serve", "長球": "clear",
    "挑球": "lift", "防守回挑": "lift", "殺球": "smash", "點扣": "smash",
    # The ShuttleSet README spells this 過渡切球; the CSVs use 過度切球 (both: passive drop).
    "切球": "drop", "過度切球": "drop", "過渡切球": "drop",
    "放小球": "net_shot", "擋小球": "block", "勾球": "cross_net", "撲球": "net_rush",
    "平球": "drive", "小平球": "drive", "後場抽平球": "drive", "防守回抽": "drive",
    "推球": "push",
}
PROFILES = ("high_clear", "smash", "drop_shot", "serve")
PROFILE_OF = {"clear": "high_clear", "smash": "smash", "drop": "drop_shot",
              "short_serve": "serve", "long_serve": "serve"}
PROFILE_GROUP = {"high_clear": "clear", "smash": "smash", "drop_shot": "drop", "serve": "serve"}


def type_of_raw(raw):
    """ShuttleSet's Chinese stroke name -> stroke type, or None (unknown or unmapped)."""
    return RAW_TO_TYPE.get((raw or "").strip())


def group_of(stroke):
    return GROUP_OF.get(stroke) if isinstance(stroke, str) else None


def profile_for(stroke):
    """The reference-range profile for a stroke type (or a drill-mode profile name), or None."""
    if not isinstance(stroke, str):
        return None
    if stroke in PROFILES:
        return stroke
    return PROFILE_OF.get(stroke)
