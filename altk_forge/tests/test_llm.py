import pytest

from altk_forge import llm as settings
from altk_forge.core.errors import InputError


def test_llm_config_from_the_environment_then_saved_settings(data_dir, monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://env:4000")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    cfg = settings.llm_config()
    assert (cfg["base_url"], cfg["model"], cfg["max_tokens"]) == ("http://env:4000", "env-model", 32000)
    settings.save_settings({"llm": {"model": "saved-model", "api_key": "sk-123456", "max_tokens": "1000"}})  # pragma: allowlist secret
    cfg = settings.llm_config()
    assert (cfg["base_url"], cfg["model"], cfg["max_tokens"]) == ("http://env:4000", "saved-model", 1000)
    public = settings.public_settings()["llm"]
    assert public["api_key_hint"] == "…3456" and "api_key" not in public
    assert public["from_env"] == ["base_url"] and public["configured"] is True
    assert (data_dir / "settings.json").stat().st_mode & 0o777 == 0o600


def test_an_empty_key_keeps_the_stored_one(data_dir):
    settings.save_settings({"llm": {"base_url": "http://x", "model": "m", "api_key": "sk-keep"}})  # pragma: allowlist secret
    settings.save_settings({"llm": {"api_key": ""}})
    assert settings.llm_config()["api_key"] == "sk-keep"
    settings.save_settings({"llm": {"clear_api_key": True}})
    assert settings.llm_config()["api_key"] is None


@pytest.mark.parametrize(
    "llm, message",
    [
        ({"base_url": "ftp://x", "model": "m"}, "http"),
        ({"base_url": "http://x", "model": "m", "temperature": "hot"}, "must be a number"),
        ({"base_url": "http://x"}, "Set the model"),
    ],
)
def test_save_settings_rejects_bad_input(data_dir, llm, message):
    with pytest.raises(InputError, match=message):
        settings.save_settings({"llm": llm})


def test_without_an_llm_there_is_no_client(data_dir):
    with pytest.raises(settings.LLMError, match="No LLM configured"):
        settings.client()
    assert settings.test_llm()["ok"] is False
