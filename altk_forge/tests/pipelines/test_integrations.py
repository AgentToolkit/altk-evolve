import pytest

from altk_forge.core.errors import InputError
from altk_forge.pipelines.integrations import delete_integration, get_integration, list_integrations, save_integration, seed_from_env


def test_integration_secrets_are_masked_and_private(data_dir):
    i = save_integration({"kind": "langfuse", "name": "LF", "host": "http://lf", "public_key": "pk", "secret_key": "sk-abcdef"})
    assert i["id"] == "lf" and "secret_key" not in i and i["secret_key_hint"] == "…cdef"
    assert (data_dir / "integrations.json").stat().st_mode & 0o777 == 0o600
    # An empty secret on edit keeps the stored one.
    save_integration({"kind": "langfuse", "name": "LF", "host": "http://lf2", "public_key": "pk", "secret_key": ""}, "lf")
    stored = get_integration("lf")
    assert (stored["host"], stored["secret_key"]) == ("http://lf2", "sk-abcdef")


@pytest.mark.parametrize(
    "data, message",
    [
        ({"kind": "nope"}, "Unknown kind"),
        ({"kind": "langfuse", "host": "http://lf"}, "Missing public_key, secret_key"),
        ({"kind": "filesystem", "path": "relative/path"}, "absolute path"),
        ({"kind": "filesystem", "path": "/no/such/folder/here"}, "No folder"),
    ],
)
def test_save_integration_rejects_bad_input(data_dir, data, message):
    with pytest.raises(InputError, match=message):
        save_integration(data)


def test_an_integration_in_use_cannot_be_deleted(pipeline):
    with pytest.raises(InputError, match="Used by pipeline qa-bot"):
        delete_integration(pipeline["inputs"][0]["integration_id"])


def test_seed_from_env_creates_the_langfuse_integration(data_dir, monkeypatch):
    monkeypatch.setenv("LANGFUSE_HOST", "http://lf")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    seed_from_env()
    assert [i["kind"] for i in list_integrations()] == ["langfuse"]
    seed_from_env()  # only on the first run
    assert len(list_integrations()) == 1
