"""Strategy config and the OpenAI-compatible client (against the local stub server)."""
import json

import pytest

from badminton_analysis.strategy.config import StrategyConfig, load_config
from badminton_analysis.strategy.llm import LLMClient, LLMError
from tests.strategy_stub import stub  # noqa: F401  (pytest fixture)


def cfg(url, timeout=5.0):
    return StrategyConfig(enabled=True, url=url, model="qwen3.8:27b", key="ollama",
                          timeout=timeout, langs=("en", "zh"))


def content(text):
    return {"choices": [{"message": {"content": text}}]}


def test_complete_json_parses_content(stub):
    stub.respond(200, content('{"a": 1}'))
    out = LLMClient(cfg(stub.url)).complete_json("sys", "user", {"type": "object"})
    assert out == {"a": 1}
    body = stub.requests[-1]["body"]
    assert body["model"] == "qwen3.8:27b" and body["reasoning_effort"] == "none"
    assert body["temperature"] == 0.3
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert stub.requests[-1]["path"] == "/v1/chat/completions"
    assert stub.requests[-1]["headers"]["Authorization"] == "Bearer ollama"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_strips_think_prefix(stub):
    stub.respond(200, content('<think>hm</think>\n{"a": 2}'))
    assert LLMClient(cfg(stub.url)).complete_json("s", "u", {}) == {"a": 2}


def test_code_fence_json_is_accepted(stub):
    stub.respond(200, content('```json\n{"a": 3}\n```'))
    assert LLMClient(cfg(stub.url)).complete_json("s", "u", {}) == {"a": 3}


def test_invalid_json_is_bad_response(stub):
    stub.respond(200, content("not json"))
    with pytest.raises(LLMError) as e:
        LLMClient(cfg(stub.url)).complete_json("s", "u", {})
    assert e.value.code == "bad_response"


def test_non_object_json_is_bad_response(stub):
    stub.respond(200, content("[1, 2]"))
    with pytest.raises(LLMError) as e:
        LLMClient(cfg(stub.url)).complete_json("s", "u", {})
    assert e.value.code == "bad_response"


def test_missing_choices_is_bad_response(stub):
    stub.respond(200, {"choices": []})
    with pytest.raises(LLMError) as e:
        LLMClient(cfg(stub.url)).complete_json("s", "u", {})
    assert e.value.code == "bad_response"


def test_unreachable():
    with pytest.raises(LLMError) as e:
        LLMClient(cfg("http://127.0.0.1:9/v1")).complete_json("s", "u", {})
    assert e.value.code == "llm_unreachable"


def test_timeout(stub):
    stub.respond(200, {"choices": []}, delay=2.0)
    with pytest.raises(LLMError) as e:
        LLMClient(cfg(stub.url, timeout=0.5)).complete_json("s", "u", {})
    assert e.value.code == "timeout"


def test_model_missing(stub):
    stub.respond(200, {"data": [{"id": "gemma4:31b"}]})
    with pytest.raises(LLMError) as e:
        LLMClient(cfg(stub.url)).check_model()
    assert e.value.code == "model_missing" and e.value.params == {"model": "qwen3.8:27b"}


def test_model_present(stub):
    stub.respond(200, {"data": [{"id": "qwen3.8:27b"}, {"id": "x"}]})
    client = LLMClient(cfg(stub.url))
    assert client.list_models() == ["qwen3.8:27b", "x"]
    client.check_model()
    assert stub.requests[-1]["path"] == "/v1/models"


def test_http_500_is_http_error(stub):
    stub.respond(500, {"error": "boom"})
    with pytest.raises(LLMError) as e:
        LLMClient(cfg(stub.url)).complete_json("s", "u", {})
    assert e.value.code == "http_error" and e.value.params == {"status": 500}


def test_extra_messages_are_appended(stub):
    stub.respond(200, content("{}"))
    extra = [{"role": "assistant", "content": "{}"}, {"role": "user", "content": "fix"}]
    LLMClient(cfg(stub.url)).complete_json("sys", "usr", {}, extra_messages=extra)
    assert stub.requests[-1]["body"]["messages"] == [
        {"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}, *extra]


def test_config_defaults():
    c = load_config(env={})
    assert c.enabled and c.url == "http://localhost:11434/v1" and c.model == "qwen3.8:27b"
    assert c.key == "ollama" and c.timeout == 600 and c.langs == ("en", "zh")


def test_config_env_beats_settings_beats_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"strategy": {"model": "x", "url": "http://a/v1/",
                                             "timeout": 42}}), encoding="utf-8")
    c = load_config(path, env={"GB_STRATEGY_LLM_MODEL": "y"})
    assert c.model == "y" and c.url == "http://a/v1" and c.timeout == 42


@pytest.mark.parametrize("value", ["OFF", "0", "false", "no"])
def test_config_off_values(value):
    assert load_config(env={"GB_STRATEGY_REPORT": value}).enabled is False
    assert load_config(env={"GB_STRATEGY_REPORT": "on"}).enabled is True


def test_config_bad_settings_file_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1,2", encoding="utf-8")
    assert load_config(path, env={}) == load_config(None, env={})
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_config(path, env={}).model == "qwen3.8:27b"
    assert load_config(tmp_path / "missing.json", env={}).model == "qwen3.8:27b"


def test_config_langs_filtered():
    assert load_config(env={"GB_STRATEGY_LANGS": "fr,zh"}).langs == ("zh",)
    assert load_config(env={"GB_STRATEGY_LANGS": "fr"}).langs == ("en", "zh")


def test_config_bad_timeout_falls_back():
    assert load_config(env={"GB_STRATEGY_LLM_TIMEOUT": "soon"}).timeout == 600
    assert load_config(env={"GB_STRATEGY_LLM_TIMEOUT": "-5"}).timeout == 600
