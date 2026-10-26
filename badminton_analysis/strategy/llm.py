"""A minimal OpenAI-compatible chat client on stdlib ``urllib`` (no new dependency).

Every failure is an ``LLMError`` whose ``code`` is one of ``llm_unreachable``,
``model_missing``, ``timeout``, ``bad_response`` or ``http_error``; ``params`` carries the
detail the UI shows. The API key is sent as a bearer token and never logged or put in an error.
"""
import json
import re
import socket
import urllib.error
import urllib.request

_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


class LLMError(Exception):
    def __init__(self, code, params=None):
        super().__init__(code)
        self.code = code
        self.params = dict(params or {})


def _parse_content(text):
    """The JSON object in a model reply (a leading think block and code fences are tolerated)."""
    text = _THINK.sub("", text or "")
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        doc = json.loads(text)
    except (ValueError, RecursionError):
        raise LLMError("bad_response", {"why": "json"})
    if not isinstance(doc, dict):
        raise LLMError("bad_response", {"why": "not_object"})
    return doc


class LLMClient:
    def __init__(self, config):
        self.config = config

    def _request(self, method, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.config.url + path, data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.config.key})
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LLMError("http_error", {"status": exc.code})
        except (socket.timeout, TimeoutError):
            raise LLMError("timeout", {"sec": self.config.timeout})
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LLMError("timeout", {"sec": self.config.timeout})
            raise LLMError("llm_unreachable", {})
        except (ValueError, OSError):
            raise LLMError("bad_response", {"why": "body"})

    def list_models(self):
        doc = self._request("GET", "/models")
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, list):
            raise LLMError("bad_response", {"why": "models"})
        return [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]

    def check_model(self):
        if self.config.model not in self.list_models():
            raise LLMError("model_missing", {"model": self.config.model})

    def complete_json(self, system, user, schema, *, extra_messages=()):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}, *extra_messages]
        doc = self._request("POST", "/chat/completions", {
            "model": self.config.model, "temperature": 0.3, "reasoning_effort": "none",
            "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "strategy_report", "strict": True, "schema": schema}}})
        try:
            text = doc["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("bad_response", {"why": "no_content"})
        if not isinstance(text, str):
            raise LLMError("bad_response", {"why": "no_content"})
        return _parse_content(text)
