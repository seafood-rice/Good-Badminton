from badminton_analysis.posture import report_llm as rl


def _report():
    return {
        "lang": "en",
        "summary": {"mean_score": 68, "consistency": 10, "verdict_text": "ORIG verdict"},
        "weaknesses": [{"metric": "elbow_extension", "measured": 138, "ideal_range": [150, 170],
                        "impact_label": "Power", "mechanism_text": "ORIG mech", "drill_text": "ORIG drill"}],
        "strengths": [{"metric": "trunk_rotation", "impact_label": "Power", "text": "ORIG strength"}],
    }


def test_polish_off_is_noop():
    r = _report()
    out = rl.polish(r, "en", spec=None)
    assert out["summary"]["verdict_text"] == "ORIG verdict"
    out2 = rl.polish(r, "en", spec="off")
    assert out2["weaknesses"][0]["mechanism_text"] == "ORIG mech"


def test_polish_unresolvable_provider_is_noop(monkeypatch):
    # No provider resolves (auth returns None) -> unchanged.
    monkeypatch.setattr(rl, "resolve_auth", lambda name: None)
    out = rl.polish(_report(), "en", spec="anthropic:whatever")
    assert out["summary"]["verdict_text"] == "ORIG verdict"


def test_polish_with_fake_provider_changes_only_prose(monkeypatch):
    class _Fake(rl.LLMProvider):
        def polish_fields(self, fields, lang):
            return {k: "NEW:" + v for k, v in fields.items()}
    monkeypatch.setattr(rl, "get_provider", lambda spec: _Fake())
    r = _report()
    out = rl.polish(r, "en", spec="fake:model")
    # prose changed
    assert out["summary"]["verdict_text"].startswith("NEW:")
    assert out["weaknesses"][0]["mechanism_text"].startswith("NEW:")
    assert out["weaknesses"][0]["drill_text"].startswith("NEW:")
    assert out["strengths"][0]["text"].startswith("NEW:")
    # numbers/structure preserved
    assert out["summary"]["mean_score"] == 68
    assert out["weaknesses"][0]["measured"] == 138
    assert out["weaknesses"][0]["ideal_range"] == [150, 170]


def test_polish_provider_error_falls_back(monkeypatch):
    class _Boom(rl.LLMProvider):
        def polish_fields(self, fields, lang):
            raise RuntimeError("provider down")
    monkeypatch.setattr(rl, "get_provider", lambda spec: _Boom())
    out = rl.polish(_report(), "en", spec="boom:model")
    assert out["summary"]["verdict_text"] == "ORIG verdict"  # unchanged on error


def test_resolve_auth_prefers_env_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    auth = rl.resolve_auth("openai")
    assert auth is not None and auth["kind"] == "api_key"


def test_local_provider_uses_spec_url_over_env(monkeypatch):
    """get_provider('local:http://localhost:8000') must use the spec URL, not the env var."""
    captured = {}
    import sys, types as _types

    # Build a fake openai module so the `from openai import OpenAI` inside polish_fields works.
    fake_openai = _types.ModuleType("openai")

    class _FakeOpenAI:
        def __init__(self, base_url, api_key):
            captured["base_url"] = base_url
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    resp = _types.SimpleNamespace()
                    resp.choices = [_types.SimpleNamespace(
                        message=_types.SimpleNamespace(content='{"verdict": "ok"}')
                    )]
                    return resp

    fake_openai.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")

    provider = rl.LocalOpenAICompatProvider("http://localhost:8000", {"kind": "local"})
    try:
        provider.polish_fields({"verdict": "x"}, "en")
    except Exception:
        pass  # JSON parse may fail on the fake response; we only care about base_url
    assert captured.get("base_url") == "http://localhost:8000", \
        "LocalOpenAICompatProvider must use the spec URL, not the env var"
