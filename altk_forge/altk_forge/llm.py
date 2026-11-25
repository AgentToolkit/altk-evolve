"""The LLM Forge itself calls (today: to write the converters that turn traces into trajectories), and its settings,
kept in .data/settings.json (private, 0600, git-ignored).

  llm   any OpenAI-compatible endpoint; the intended one is a LiteLLM proxy, which routes the model name to a provider.
        {base_url, api_key, model, temperature, max_tokens, timeout}

Empty fields fall back to LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in the environment or .env."""

import os

from .core.errors import InputError
from .core.paths import DATA, load_env
from .core.store import LOCK, read_json, write_json

SETTINGS = DATA / "settings.json"


LLM_FIELDS = ("base_url", "api_key", "model", "temperature", "max_tokens", "timeout")


LLM_DEFAULTS = {"temperature": 0.0, "max_tokens": 32000, "timeout": 600}


class LLMError(Exception):
    """The model is not configured, unreachable, or answered something unusable."""


def llm_config():
    """The effective LLM settings: saved values, then the environment, then defaults."""
    load_env()
    saved = read_json(SETTINGS, {}).get("llm") or {}
    env = {"base_url": os.environ.get("LLM_BASE_URL"), "api_key": os.environ.get("LLM_API_KEY"), "model": os.environ.get("LLM_MODEL")}
    out = {}
    for f in LLM_FIELDS:
        v = saved.get(f)
        out[f] = v if v not in (None, "") else env.get(f) or LLM_DEFAULTS.get(f)
    return out


def public_settings():
    """The settings for the console: the key shows only its last 4 characters; `from_env` names fields read from the environment."""
    saved, cfg = read_json(SETTINGS, {}).get("llm") or {}, llm_config()
    llm = {f: cfg[f] for f in LLM_FIELDS if f != "api_key"}
    llm["api_key_hint"] = f"…{cfg['api_key'][-4:]}" if cfg.get("api_key") else None
    llm["from_env"] = [f for f in ("base_url", "api_key", "model") if not saved.get(f) and cfg.get(f)]
    llm["configured"] = bool(cfg.get("base_url") and cfg.get("model"))
    return {"llm": llm}


def save_settings(data):
    llm_in = data.get("llm") or {}
    with LOCK:
        current = read_json(SETTINGS, {})
        llm = dict(current.get("llm") or {})
        for f in LLM_FIELDS:
            if f not in llm_in:
                continue
            v = llm_in[f]
            v = v.strip() if isinstance(v, str) else v
            if f == "api_key" and not v:
                continue  # an empty key keeps the stored one; clear_api_key removes it
            if f in ("temperature", "max_tokens", "timeout") and v not in (None, ""):
                try:
                    v = float(v) if f == "temperature" else int(v)
                except (TypeError, ValueError):
                    raise InputError(f"{f} must be a number") from None
            llm[f] = v if v not in ("",) else None
        if llm_in.get("clear_api_key"):
            llm.pop("api_key", None)
        if llm.get("base_url") and not str(llm["base_url"]).startswith(("http://", "https://")):
            raise InputError("The endpoint is an http(s) URL, e.g. http://localhost:4000")
        load_env()
        missing = [
            label
            for f, env, label in (("base_url", "LLM_BASE_URL", "the endpoint"), ("model", "LLM_MODEL", "the model"))
            if not llm.get(f) and not os.environ.get(env)
        ]
        if missing:
            raise InputError(f"Set {' and '.join(missing)}: without {'them' if len(missing) > 1 else 'it'} no converter can be written")
        current["llm"] = {k: v for k, v in llm.items() if v is not None}
        write_json(SETTINGS, current, private=True)
    return public_settings()


def client(override=None):
    """An OpenAI client for the configured LLM; `override` (unsaved form values) wins over the saved settings."""
    cfg = llm_config()
    for k, v in (override or {}).items():
        if k in LLM_FIELDS and v not in (None, ""):
            cfg[k] = v
    if not (cfg.get("base_url") and cfg.get("model")):
        raise LLMError("No LLM configured: set the endpoint and the model in Settings")
    from openai import OpenAI

    base = cfg["base_url"].rstrip("/")
    # A LiteLLM proxy serves the OpenAI routes both at / and at /v1; the SDK appends /chat/completions.
    return OpenAI(
        base_url=base if base.endswith("/v1") else f"{base}/v1",
        api_key=cfg.get("api_key") or "none",
        timeout=float(cfg["timeout"]),
        max_retries=2,
    ), cfg


def chat(messages, max_tokens=None, override=None):
    """One chat completion; returns (text, usage dict)."""
    import openai

    c, cfg = client(override)
    try:
        r = c.chat.completions.create(
            model=cfg["model"], messages=messages, temperature=float(cfg["temperature"]), max_tokens=int(max_tokens or cfg["max_tokens"])
        )
    except openai.APIError as e:
        raise LLMError(f"{cfg['model']} at {cfg['base_url']}: {e}") from None
    text = (r.choices[0].message.content or "") if r.choices else ""
    usage = r.usage.model_dump() if r.usage else {}
    return text, {"model": r.model, **{k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}}


def test_llm(override=None):
    """Call the model once, with the saved settings or, when given, the values in the form (not saved yet)."""
    try:
        text, usage = chat([{"role": "user", "content": "Reply with the single word OK."}], max_tokens=16, override=override)
        return {"ok": True, "message": f"{usage.get('model')} answered “{text.strip()[:40]}”"}
    except LLMError as e:
        return {"ok": False, "message": str(e)}
