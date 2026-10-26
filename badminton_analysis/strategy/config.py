"""Strategy report settings (spec section 7).

Precedence: an environment variable wins over the ``strategy`` object of
``data/settings.json``, which wins over the default. ``load_config`` is total: a missing,
unreadable or non-object settings file is ignored and a bad value falls back to its default.
"""
import json
import os
from dataclasses import dataclass

DEFAULT_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen3.8:27b"
DEFAULT_KEY = "ollama"
DEFAULT_TIMEOUT = 600.0
LANGS = ("en", "zh")
_OFF = ("off", "0", "false", "no")


@dataclass(frozen=True)
class StrategyConfig:
    enabled: bool
    url: str                    # no trailing slash
    model: str
    key: str
    timeout: float              # seconds, per language
    langs: tuple


def _settings(path):
    """The ``strategy`` object of the settings file, or ``{}``."""
    if path is None:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError, RecursionError, MemoryError):
        return {}
    section = doc.get("strategy") if isinstance(doc, dict) else None
    return section if isinstance(section, dict) else {}


def _text(value, default):
    return value.strip() if isinstance(value, str) and value.strip() else default


def _timeout(value):
    if isinstance(value, bool):
        return DEFAULT_TIMEOUT
    try:
        sec = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return sec if 0 < sec < float("inf") else DEFAULT_TIMEOUT


def _langs(value):
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = [v for v in value if isinstance(v, str)]
    else:
        items = []
    out = []
    for item in items:
        item = item.strip().lower()
        if item in LANGS and item not in out:
            out.append(item)
    return tuple(out) if out else LANGS


def _enabled(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _OFF


def load_config(settings_path=None, env=None):
    env = os.environ if env is None else env
    conf = _settings(settings_path)

    def pick(env_name, key):
        value = env.get(env_name)
        return value if value is not None and str(value).strip() != "" else conf.get(key)

    enabled = pick("GB_STRATEGY_REPORT", "enabled")
    return StrategyConfig(
        enabled=True if enabled is None else _enabled(enabled),
        url=_text(pick("GB_STRATEGY_LLM_URL", "url"), DEFAULT_URL).rstrip("/"),
        model=_text(pick("GB_STRATEGY_LLM_MODEL", "model"), DEFAULT_MODEL),
        key=_text(pick("GB_STRATEGY_LLM_KEY", "key"), DEFAULT_KEY),
        timeout=_timeout(pick("GB_STRATEGY_LLM_TIMEOUT", "timeout")),
        langs=_langs(pick("GB_STRATEGY_LANGS", "langs")),
    )
