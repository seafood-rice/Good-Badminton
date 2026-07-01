"""Optional, provider-pluggable prose polish for coach reports. Default OFF.

Only rewrites prose fields (verdict/mechanism/drill/strength text); never
numbers, scores, structure, or drill selection. Any failure falls back to the
curated text unchanged. Builds NO OAuth flow — it only reads tokens that a
sanctioned first-party CLI already stored, or an API key from env/config, or
uses a local no-auth endpoint.
"""
import copy
import os

_ENV_KEY = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}


def resolve_auth(provider_name):
    """Return an auth descriptor dict or None. Order: subscription token, API key, local."""
    if provider_name == "local":
        return {"kind": "local"}
    # 1) subscription/OAuth token from a sanctioned first-party CLI credential store.
    token = _read_subscription_token(provider_name)
    if token:
        return {"kind": "subscription_token", "token": token}
    # 2) API key from env.
    env = _ENV_KEY.get(provider_name)
    if env and os.environ.get(env):
        return {"kind": "api_key", "key": os.environ[env]}
    return None


def _read_subscription_token(provider_name):
    """Best-effort read of an existing sanctioned-CLI token. Returns str or None.
    Reads only; never writes. Locations are best-effort and may not exist."""
    try:
        if provider_name == "anthropic":
            path = os.path.expanduser("~/.claude/.credentials.json")
            if os.path.exists(path):
                import json
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                # structure is provider-defined; return whatever token field exists.
                return (data.get("access_token") or data.get("token")
                        or (data.get("claudeai") or {}).get("access_token"))
        # google/openai subscription tokens: no sanctioned third-party path -> None.
    except Exception:
        return None
    return None


class LLMProvider(object):
    def polish_fields(self, fields, lang):
        raise NotImplementedError


class _SDKProvider(LLMProvider):
    def __init__(self, model, auth):
        self.model = model
        self.auth = auth


class AnthropicProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        # Anthropic Messages API; model id supplied at runtime via the spec string.
        import anthropic  # lazy
        client = anthropic.Anthropic(api_key=self.auth.get("key")) if self.auth.get("kind") == "api_key" \
            else anthropic.Anthropic()  # env/subscription resolved by SDK
        # send fields as JSON, ask for same-keys rewrite in `lang`, natural human coach voice.
        return _sdk_rewrite_via_messages(client, self.model, fields, lang)


class OpenAIProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        from openai import OpenAI  # lazy
        client = OpenAI(api_key=self.auth.get("key")) if self.auth.get("kind") == "api_key" else OpenAI()
        return _openai_rewrite(client, self.model, fields, lang)


class GoogleProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        import google.generativeai as genai  # lazy
        if self.auth.get("kind") == "api_key":
            genai.configure(api_key=self.auth["key"])
        return _google_rewrite(genai, self.model, fields, lang)


class LocalOpenAICompatProvider(_SDKProvider):
    def polish_fields(self, fields, lang):
        from openai import OpenAI  # lazy; local OpenAI-compatible endpoint
        base = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        client = OpenAI(base_url=base, api_key="not-needed")
        return _openai_rewrite(client, self.model, fields, lang)


_PROVIDERS = {
    "anthropic": AnthropicProvider, "openai": OpenAIProvider,
    "google": GoogleProvider, "local": LocalOpenAICompatProvider,
}


def get_provider(spec):
    if not spec or spec == "off":
        return None
    if ":" in spec:
        name, model = spec.split(":", 1)
    else:
        name, model = spec, ""
    cls = _PROVIDERS.get(name)
    if cls is None:
        return None
    auth = resolve_auth(name)
    if auth is None:
        return None
    return cls(model, auth)


_PROSE_SYSTEM = (
    "You are a seasoned badminton coach writing feedback. Rewrite each provided text "
    "in {lang} in a natural, direct human-coach voice. Do not invent numbers or claims; "
    "keep the meaning; avoid AI-sounding filler, hedging, and lists of three. Return the "
    "same keys with rewritten values."
)


def polish(report_dict, lang, spec=None):
    provider = get_provider(spec)
    if provider is None:
        return report_dict
    # Deep-copy to avoid mutating the caller's dict.
    report_dict = copy.deepcopy(report_dict)
    # Gather prose fields with stable ids.
    fields = {}
    fields["verdict"] = report_dict.get("summary", {}).get("verdict_text", "")
    for i, w in enumerate(report_dict.get("weaknesses", [])):
        fields["w%d_mech" % i] = w.get("mechanism_text", "")
        fields["w%d_drill" % i] = w.get("drill_text", "")
    for i, s in enumerate(report_dict.get("strengths", [])):
        fields["s%d_text" % i] = s.get("text", "")
    try:
        new = provider.polish_fields(fields, lang)
    except Exception:
        return report_dict
    if not isinstance(new, dict):
        return report_dict
    # Apply back only known ids; ignore anything unexpected.
    if "verdict" in new:
        report_dict["summary"]["verdict_text"] = new["verdict"]
    for i, w in enumerate(report_dict.get("weaknesses", [])):
        if "w%d_mech" % i in new:
            w["mechanism_text"] = new["w%d_mech" % i]
        if "w%d_drill" % i in new:
            w["drill_text"] = new["w%d_drill" % i]
    for i, s in enumerate(report_dict.get("strengths", [])):
        if "s%d_text" % i in new:
            s["text"] = new["s%d_text" % i]
    return report_dict


# --- SDK rewrite helpers (thin; each imports nothing at module top) ---
def _sdk_rewrite_via_messages(client, model, fields, lang):
    import json
    msg = client.messages.create(
        model=model, max_tokens=1500,
        system=_PROSE_SYSTEM.format(lang=lang),
        messages=[{"role": "user", "content": json.dumps(fields, ensure_ascii=False)}],
    )
    text = "".join(getattr(b, "text", "") for b in msg.content)
    return json.loads(text)


def _openai_rewrite(client, model, fields, lang):
    import json
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": _PROSE_SYSTEM.format(lang=lang)},
                  {"role": "user", "content": json.dumps(fields, ensure_ascii=False)}],
    )
    return json.loads(resp.choices[0].message.content)


def _google_rewrite(genai, model, fields, lang):
    import json
    m = genai.GenerativeModel(model, system_instruction=_PROSE_SYSTEM.format(lang=lang))
    resp = m.generate_content(json.dumps(fields, ensure_ascii=False))
    return json.loads(resp.text)
