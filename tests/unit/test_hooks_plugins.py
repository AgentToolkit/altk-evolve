"""Behavior of the in-tree hook plugins (normalizer, access stamp, PII filter).

Requires the optional cpex package (``uv sync --extra hooks``); the PII tests
additionally require cpex-pii-filter (``--extra pii-regex``).
"""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("cpex")

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import dispatch_llm_pre_call, shutdown_hooks
from altk_evolve.hooks.plugin import HookContext
from altk_evolve.schema.core import Entity

NORMALIZER_SPEC = HookPluginSpec(
    name="metadata_normalizer",
    kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
    hooks=["memory_pre_write"],
    mode="transform",
    priority=40,
)
ACCESS_STAMP_SPEC = HookPluginSpec(
    name="access_stamp",
    kind="altk_evolve.hooks.plugins.access_stamp.AccessStampPlugin",
    hooks=["memory_post_read"],
    mode="fire_and_forget",
)
PII_SPEC = HookPluginSpec(
    name="pii_filter_memory",
    kind="altk_evolve.hooks.plugins.pii.PIIFilterMemoryPlugin",
    hooks=["memory_pre_write", "llm_pre_call"],
    mode="transform",
    priority=10,
    config={
        "detect_email": True,
        "detect_ssn": True,
        "detect_phone": True,
        "default_mask_strategy": "redact",
        "redaction_text": "[REDACTED]",
    },
)
SECRETS_SPEC = HookPluginSpec(
    name="secrets_filter_memory",
    kind="altk_evolve.hooks.plugins.secrets.SecretsFilterMemoryPlugin",
    hooks=["memory_pre_write", "llm_pre_call"],
    mode="sequential",
    priority=10,
    # Empty config on purpose: the plugin merges defaults, so the structured
    # detectors (aws_access_key_id, github_token, ...) are ON with no toggles.
    config={},
)


@pytest.fixture(autouse=True)
def clean_hook_state(monkeypatch):
    # Explicit specs always override discovery, but keep the tests hermetic
    # against a stray ./evolve.hooks.yaml or ~/.config/evolve/hooks.yaml.
    monkeypatch.setattr("altk_evolve.hooks.manager.discover_hooks_config_path", lambda: None)
    shutdown_hooks()
    yield
    shutdown_hooks()


def make_client(tmp_path: Path, *specs: HookPluginSpec) -> EvolveClient:
    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=str(tmp_path)),
        hooks=HooksConfig(plugins=list(specs)),
    )
    return EvolveClient(config)


# ── MetadataNormalizerPlugin ─────────────────────────────────────────


@pytest.mark.unit
def test_normalizer_copies_task_id_to_trace_id(tmp_path: Path):
    # Why this plugin exists: the MCP server's save_trajectory stamps task_id
    # while Phoenix sync stamps trace_id — downstream cascade cleanup keys on
    # trace_id, so MCP-saved sessions would otherwise miss it.
    client = make_client(tmp_path, NORMALIZER_SPEC.model_copy(update={"show_in_ui": False, "display_name": "Memory metadata"}))
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="x", type="trajectory", metadata={"task_id": "t-42"})], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["trace_id"] == "t-42"
    assert stored.metadata["task_id"] == "t-42"
    assert "created_at" in stored.metadata


@pytest.mark.unit
def test_normalizer_preserves_existing_values(tmp_path: Path):
    client = make_client(tmp_path, NORMALIZER_SPEC)
    client.create_namespace("ns")
    metadata = {"task_id": "t-1", "trace_id": "existing", "created_at": "2020-01-01T00:00:00+00:00"}
    client.update_entities("ns", [Entity(content="x", type="note", metadata=metadata)], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.metadata["trace_id"] == "existing"
    assert stored.metadata["created_at"] == "2020-01-01T00:00:00+00:00"


# ── AccessStampPlugin ────────────────────────────────────────────────


@pytest.mark.unit
def test_access_stamp_records_last_accessed(tmp_path: Path):
    client = make_client(tmp_path, ACCESS_STAMP_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="x", type="note")], enable_conflict_resolution=False)

    first_read = client.search_entities("ns", limit=1)[0]
    assert "last_accessed" not in first_read.metadata  # fire_and_forget: read result itself is untouched

    re_read = client.get_entity_by_id("ns", first_read.id)
    assert re_read is not None
    assert re_read.metadata.get("last_accessed")


@pytest.mark.unit
def test_access_stamp_does_not_loop_with_write_plugins(tmp_path: Path):
    # Stamping goes through the metadata-patch path (internal read + patch),
    # which fires neither memory_post_read nor memory_pre_write — so combining
    # it with write-hook plugins cannot loop.
    client = make_client(tmp_path, NORMALIZER_SPEC, ACCESS_STAMP_SPEC)
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="x", type="note", metadata={"task_id": "t"})], enable_conflict_resolution=False)

    for _ in range(3):
        results = client.search_entities("ns", limit=10)
        assert len(results) == 1

    stored = client.get_entity_by_id("ns", results[0].id)
    assert stored is not None
    assert stored.metadata["trace_id"] == "t"
    assert stored.metadata.get("last_accessed")


# ── PIIFilterMemoryPlugin ────────────────────────────────────────────


@pytest.mark.unit
def test_pii_plugin_redacts_writes(tmp_path: Path):
    pytest.importorskip("cpex_pii_filter")
    client = make_client(tmp_path, PII_SPEC)
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [Entity(content="email dana@example.com ssn 123-45-6789", type="note")],
        enable_conflict_resolution=False,
    )

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == "email [REDACTED] ssn [REDACTED]"


@pytest.mark.unit
def test_pii_plugin_redacts_llm_egress(tmp_path: Path):
    pytest.importorskip("cpex_pii_filter")
    make_client(tmp_path, PII_SPEC)

    messages = dispatch_llm_pre_call([{"role": "user", "content": "reach me at dana@example.com"}], purpose="test")
    assert messages == [{"role": "user", "content": "reach me at [REDACTED]"}]


@pytest.mark.unit
def test_plugin_stubs_raise_without_cpex(monkeypatch):
    import altk_evolve.hooks.plugins.pii as pii_module

    if pii_module._HAS_PII_FILTER:
        pytest.skip("cpex-pii-filter installed; stub not active")
    with pytest.raises(ImportError, match=r"altk-evolve\[pii-regex\]"):
        pii_module.PIIFilterMemoryPlugin()


# ── SecretsFilterMemoryPlugin ────────────────────────────────────────

# Clearly-fake but shape-valid credentials. `# pragma: allowlist secret` keeps
# this repo's own detect-secrets pre-commit hook from tripping on the fixtures.
_FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
_FAKE_GH_TOKEN = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"  # pragma: allowlist secret


@pytest.mark.unit
def test_secrets_plugin_redacts_writes(tmp_path: Path):
    pytest.importorskip("cpex_secrets_detection")
    client = make_client(tmp_path, SECRETS_SPEC)
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [Entity(content=f"aws {_FAKE_AWS_KEY} gh {_FAKE_GH_TOKEN} plus a normal note", type="note")],
        enable_conflict_resolution=False,
    )

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == "aws [REDACTED] gh [REDACTED] plus a normal note"


@pytest.mark.unit
def test_secrets_plugin_redacts_llm_egress(tmp_path: Path):
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)

    messages = dispatch_llm_pre_call(
        [{"role": "user", "content": f"deploy with {_FAKE_AWS_KEY} and {_FAKE_GH_TOKEN}"}],
        purpose="test",
    )
    assert messages == [{"role": "user", "content": "deploy with [REDACTED] and [REDACTED]"}]


# Assembled at runtime: GitHub push protection rejects a Slack-token-shaped literal
# even when it is fake, and it has no inline allowlist pragma.
_FAKE_SLACK_BOT_TOKEN = "-".join(["xoxb", "123456789012", "1234567890123", "AbCdEfGhIjKlMnOpQrStUvWx"])
_FAKE_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"  # pragma: allowlist secret


@pytest.mark.unit
def test_secrets_plugin_redacts_a_whole_slack_token(tmp_path: Path):
    """Every segment of the token is replaced, not just its prefix."""
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)

    messages = dispatch_llm_pre_call([{"role": "user", "content": f"notify {_FAKE_SLACK_BOT_TOKEN} when done"}], purpose="test")
    assert messages == [{"role": "user", "content": "notify [REDACTED] when done"}]


@pytest.mark.unit
def test_secrets_plugin_redacts_only_the_value_of_an_aws_secret_assignment(tmp_path: Path):
    """The key name stays readable; only the secret value is replaced."""
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)

    messages = dispatch_llm_pre_call([{"role": "user", "content": f"aws_secret_access_key = {_FAKE_AWS_SECRET}"}], purpose="test")
    assert messages == [{"role": "user", "content": "aws_secret_access_key = [REDACTED]"}]


@pytest.mark.unit
def test_secrets_default_enabled_matches_the_installed_package_detectors():
    """DEFAULT_ENABLED must configure exactly the installed package's detectors.
    The scanner ignores unknown detector names, so a renamed detector would
    otherwise be switched off by our defaults, and a new one left unconfigured."""
    pytest.importorskip("cpex_secrets_detection")
    from importlib.resources import files

    import yaml as _yaml

    from altk_evolve.hooks.plugins.secrets import DEFAULT_ENABLED

    manifest = _yaml.safe_load((files("cpex_secrets_detection") / "plugin-manifest.yaml").read_text())
    assert set(manifest["default_configs"]["enabled"]) == set(DEFAULT_ENABLED)


def _scaffold_secrets_entry() -> dict:
    """The commented-out secrets entry from the `evolve hooks init` template, uncommented."""
    import yaml as _yaml

    template = Path(__file__).resolve().parents[2] / "altk_evolve" / "cli" / "templates" / "hooks.yaml"
    lines = template.read_text().splitlines()
    start = lines.index("  # - name: secrets_filter_memory")
    block = []
    for line in lines[start:]:
        if not line.startswith("  # "):
            break
        block.append(line[len("  # ") :])
    entry: dict = _yaml.safe_load("\n".join(block))[0]
    return entry


@pytest.mark.unit
def test_secrets_plugin_accepts_every_documented_config_key():
    """The scaffold's config (every documented key) and the remaining documented
    key, min_findings_to_block, are all accepted and reach the scanner config."""
    from altk_evolve.hooks.plugins.secrets import DEFAULT_ENABLED, SecretsFilterMemoryPlugin, build_scan_config

    scaffold_config = _scaffold_secrets_entry()["config"]
    assert set(scaffold_config) == {"redact", "redaction_text", "block_on_detection", "enabled"}
    SecretsFilterMemoryPlugin(scaffold_config)

    config = {
        "redact": False,
        "redaction_text": "***",
        "block_on_detection": True,
        "min_findings_to_block": 2,
        "enabled": {key: True for key in DEFAULT_ENABLED},
    }
    assert build_scan_config(config) == config


@pytest.mark.unit
@pytest.mark.parametrize("key", ["field_allowlist", "field_denylist", "redaction_txt"])
def test_secrets_plugin_rejects_unknown_config_keys(key: str):
    """Fail closed: a top-level key the plugin does not know is an error naming
    the key, never forwarded to the scanner. field_allowlist / field_denylist are
    real scanner keys that narrow which fields are scanned."""
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    with pytest.raises(ValueError, match=rf"Unsupported secrets plugin config key\(s\): '{key}'"):
        SecretsFilterMemoryPlugin({key: ["content"]})


@pytest.mark.unit
def test_secrets_plugin_rejects_unknown_detector_names():
    """The scanner silently ignores a detector name it does not know, so a typo
    under `enabled` would leave the intended detector at its default."""
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    with pytest.raises(ValueError, match=r"Unknown secrets detector\(s\) under 'enabled': 'jwt_lik'"):
        SecretsFilterMemoryPlugin({"enabled": {"jwt_lik": True}})


@pytest.mark.unit
def test_secrets_plugin_field_denylist_fails_engine_init_instead_of_skipping_content(tmp_path: Path):
    """A field_denylist that would stop `content` from being scanned fails engine
    init; it does not let a token reach the LLM unredacted."""
    pytest.importorskip("cpex_secrets_detection")
    spec = SECRETS_SPEC.model_copy(update={"config": {"field_denylist": ["content"]}})
    with pytest.raises(ValueError, match=r"'field_denylist'"):
        make_client(tmp_path, spec)

    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    with pytest.raises(ValueError, match=r"'field_denylist'"):
        SecretsFilterMemoryPlugin({"field_denylist": ["content"]})._redact_items([{"role": "user", "content": f"token {_FAKE_GH_TOKEN}"}])


@pytest.mark.unit
def test_secrets_plugin_leaves_non_secret_untouched(tmp_path: Path):
    pytest.importorskip("cpex_secrets_detection")
    client = make_client(tmp_path, SECRETS_SPEC)
    client.create_namespace("ns")
    # A 32-char hex digest is NOT a secret with the entropy detectors off.
    benign = "digest deadbeefdeadbeefdeadbeefdeadbeef and just plain words"
    client.update_entities("ns", [Entity(content=benign, type="note")], enable_conflict_resolution=False)

    stored = client.search_entities("ns", limit=1)[0]
    assert stored.content == benign


@pytest.mark.unit
def test_secrets_plugin_is_native_not_raw_cpex():
    """Routing guard: secrets is a NATIVE plugin (wrapped by the seam's native
    adapter in `_register_spec`), NOT a raw-cpex plugin like pii — so it must
    NOT subclass cpex's `Plugin`. If it did, the manager would register it
    directly on the raw-cpex path instead of through the native adapter."""
    from cpex.framework import Plugin as CpexPlugin

    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    assert not issubclass(SecretsFilterMemoryPlugin, CpexPlugin)


@pytest.mark.unit
def test_secrets_plugin_fails_closed_at_init_without_extra(tmp_path: Path, monkeypatch):
    """A missing [secrets] extra fails CLOSED at engine init (via startup_validate),
    naming the extra — not lazily on the first write. Simulated by making the
    lazy scanner build raise, then registering the plugin through the seam:
    initialization must surface the extra-naming ImportError."""
    import altk_evolve.hooks.plugins.secrets as secrets_module

    def _raise() -> None:
        raise ImportError(
            "Structured secrets redaction requires cpex-secrets-detection. Install it with: pip install 'altk-evolve[secrets]'"
        )

    monkeypatch.setattr(secrets_module, "build_secrets_scanner", _raise)
    with pytest.raises(ImportError, match=r"altk-evolve\[secrets\]"):
        make_client(tmp_path, SECRETS_SPEC)


@pytest.mark.unit
def test_secrets_plugin_raises_without_cpex_secrets_detection():
    """Without the [secrets] extra the plugin's lazy scanner import fails with a
    named install hint. Construction is cheap (the Rust core loads lazily); the
    extra-naming ImportError trips on `startup_validate` (engine init)."""
    import importlib.util

    if importlib.util.find_spec("cpex_secrets_detection") is not None:
        pytest.skip("cpex-secrets-detection installed; the degradation path is not active")
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    plugin = SecretsFilterMemoryPlugin()  # construction is cheap; scanner loads lazily
    with pytest.raises(ImportError, match=r"altk-evolve\[secrets\]"):
        plugin.startup_validate()


# ── ReadiSemanticPIIPlugin ───────────────────────────────────────────


class _StubContext:
    """Minimal cpex-style plugin context for the raw cpex pii plugin."""

    class _GC:
        state: dict = {}

    global_context = _GC()


def _pii_plugin():
    from altk_evolve.hooks.plugins.pii import PIIFilterMemoryPlugin

    return PIIFilterMemoryPlugin()


def fake_name_detector(text: str):
    """Stand-in for READI: finds one fixed 'name' so the shim is testable fast.

    Loading a real transformer NER pipeline costs a ~460MB download and seconds
    per test; the detection engine is covered by READI itself and by
    ``examples/pii_benchmark.py``. What these tests must pin is the *shim*:
    config parsing, hook wiring and the modified_payload contract.
    """
    start = text.find("Dana Whitfield")
    return [(start, start + len("Dana Whitfield"))] if start != -1 else []


@pytest.mark.unit
def test_readi_native_redacts_names_regex_cannot(tmp_path: Path):
    """The point of the semantic plugin: a NAME the regex filter leaves untouched.

    The regex plugin is a RAW CPEX plugin (async, PluginResult); the semantic
    plugin is a NATIVE plugin (sync, returns a plain payload) — this test proves
    both flavors work side by side.
    """
    pytest.importorskip("cpex_pii_filter")
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import MemoryPreWritePayload

    text = "Dana Whitfield can be reached at dana@example.com"
    payload = MemoryPreWritePayload(namespace_id="ns", entities=[{"content": text, "type": "note"}])

    regex_plugin = _pii_plugin()  # raw cpex plugin: async, returns PluginResult
    regex_out = asyncio.run(regex_plugin.memory_pre_write(payload, _StubContext()))
    assert "Dana Whitfield" in regex_out.modified_payload.entities[0]["content"]  # regex has no NER

    readi_plugin = ReadiSemanticPIIPlugin()  # native plugin: sync, returns a plain payload
    readi_plugin._detector = fake_name_detector
    readi_out = readi_plugin.memory_pre_write(payload, HookContext())
    assert readi_out is not None
    assert readi_out.entities[0]["content"] == "[REDACTED] can be reached at dana@example.com"


@pytest.mark.unit
def test_readi_native_redacts_llm_egress():
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import LLMPreCallPayload

    plugin = ReadiSemanticPIIPlugin()
    plugin._detector = fake_name_detector
    payload = LLMPreCallPayload(messages=[{"role": "user", "content": "summarize Dana Whitfield's notes"}], purpose="test")
    result = plugin.llm_pre_call(payload, HookContext())
    assert result.messages == [{"role": "user", "content": "summarize [REDACTED]'s notes"}]


@pytest.mark.unit
def test_readi_native_reads_config_keys():
    """The plain config dict drives the mask and the metadata opt-in — no code change needed."""
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import MemoryPreWritePayload

    plugin = ReadiSemanticPIIPlugin({"redaction_text": "<PERSON>", "redact_metadata": True})
    plugin._detector = fake_name_detector
    payload = MemoryPreWritePayload(
        namespace_id="ns",
        entities=[{"content": "hi Dana Whitfield", "metadata": {"author": "Dana Whitfield"}}],
    )
    entity = plugin.memory_pre_write(payload, HookContext()).entities[0]
    assert entity["content"] == "hi <PERSON>"
    assert entity["metadata"] == {"author": "<PERSON>"}


@pytest.mark.unit
def test_readi_native_redacts_metadata_by_default():
    """Default (no config) redacts metadata, matching the regex plugin's behaviour."""
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import MemoryPreWritePayload

    plugin = ReadiSemanticPIIPlugin()
    plugin._detector = fake_name_detector
    payload = MemoryPreWritePayload(
        namespace_id="ns",
        entities=[{"content": "note", "metadata": {"author": "Dana Whitfield"}}],
    )
    entity = plugin.memory_pre_write(payload, HookContext()).entities[0]
    assert entity["metadata"] == {"author": "[REDACTED]"}


@pytest.mark.unit
def test_readi_native_redacts_pii_in_tool_call_arguments():
    """Egress-leak regression at the plugin level: tool_call arguments are redacted."""
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import LLMPreCallPayload

    plugin = ReadiSemanticPIIPlugin()
    plugin._detector = fake_name_detector
    payload = LLMPreCallPayload(
        messages=[
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "save", "arguments": '{"who": "Dana Whitfield"}'}}],
            }
        ],
        purpose="test",
    )
    result = plugin.llm_pre_call(payload, HookContext())
    call = result.messages[0]["tool_calls"][0]
    assert "Dana Whitfield" not in call["function"]["arguments"]
    assert call["function"]["name"] == "save"


@pytest.mark.unit
def test_readi_scaffold_yaml_can_block_and_fails_closed():
    """Mode/on_error are load-bearing, not cosmetic (see docs/guides/memory-hooks.md).

    They now live in the scaffold YAML / HookPluginSpec, not a cpex
    ``_default_config`` on the plugin. CPEX downgrades continue_processing=False
    -> True in transform/audit mode, so a redactor that must be able to halt has
    to register `sequential`; and a compliance plugin must fail closed so a
    crashing NER model never silently passes PII through. Pin the shipped
    scaffold's READI entry sets both.
    """
    from pathlib import Path as _Path

    import yaml as _yaml

    template = _Path(__file__).resolve().parents[2] / "altk_evolve" / "cli" / "templates" / "hooks.yaml"
    entries = _yaml.safe_load(template.read_text())["plugins"]
    readi = next(e for e in entries if e["name"] == "readi_semantic_pii")
    assert readi["mode"] == "sequential"
    assert readi["on_error"] == "fail"
    assert set(readi["hooks"]) == {"memory_pre_write", "llm_pre_call"}


@pytest.mark.unit
def test_readi_native_raises_without_readi():
    """Without the [pii-semantic] extra the plugin fails with a named install hint.

    Construction is cheap (READI loads lazily); the extra-naming ImportError
    trips on first detection (or ``startup_validate`` at engine init)."""
    import importlib.util

    if importlib.util.find_spec("risk_assessment") is not None:
        pytest.skip("readi-privacy installed; the degradation path is not active")
    from altk_evolve.hooks.plugins.readi import ReadiSemanticPIIPlugin
    from altk_evolve.hooks.types import LLMPreCallPayload

    plugin = ReadiSemanticPIIPlugin()  # construction is cheap; READI loads lazily
    with pytest.raises(ImportError, match=r"altk-evolve\[pii-semantic\]"):
        plugin.llm_pre_call(LLMPreCallPayload(messages=[{"role": "user", "content": "x"}]), HookContext())


@pytest.mark.unit
def test_pii_redacts_free_text_without_changing_email_subject_or_provenance(tmp_path):
    pytest.importorskip("cpex_pii_filter")
    client = make_client(tmp_path, PII_SPEC, NORMALIZER_SPEC)
    client.ensure_namespace("identity-test")
    metadata = {
        key: "alice@example.com" for key in ("user_id", "owner_id", "agent_id", "thread_id", "session_id", "task_id", "source_task_id")
    }
    metadata["title"] = "Contact alice@example.com"
    updates = client.update_entities(
        "identity-test", [Entity(type="fact", content="Email alice@example.com", metadata=metadata)], enable_conflict_resolution=False
    )
    entity = client.get_entity_by_id("identity-test", updates[0].id)
    for key in metadata.keys() - {"title"}:
        assert entity.metadata[key] == metadata[key]
    assert entity.metadata["trace_id"] == "alice@example.com"
    assert "alice@example.com" not in entity.content
    assert "alice@example.com" not in entity.metadata["title"]


@pytest.mark.unit
def test_pii_keeps_source_identities_on_consolidated_guidelines(tmp_path):
    # Consolidation hands pre-built sources to memory_pre_write. Their identity fields are
    # what deletion receipts match on, so redacting them would leave the merged guideline
    # uncollectible; any other field of a source record is still redacted.
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    pytest.importorskip("cpex_pii_filter")
    client = make_client(tmp_path, PII_SPEC)
    client.ensure_namespace("ns")
    subject = "alice@example.com"
    members = [
        Entity(
            type="guideline",
            content=content,
            metadata={"task_description": "t", "category": "strategy", "user_id": subject, "agent_id": subject, "thread_id": thread},
        )
        for content, thread in [("A", subject), ("B", "t2")]
    ]
    client.update_entities("ns", members, enable_conflict_resolution=False)
    note = {"conversation_id": "t3", "task_id": subject, "user_id": subject, "agent_id": subject, "status": "supporting"}
    client.update_entities(
        "ns",
        [Entity(type="fact", content="x", metadata={"sources": [{**note, "note": f"Contact {subject}"}]})],
        enable_conflict_resolution=False,
    )
    body = {"guidelines": [{"content": "merged", "rationale": "", "category": "strategy", "trigger": "", "source_indices": [0, 1]}]}
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])
    with (
        patch("altk_evolve.llm.guidelines.clustering.cluster_entities", side_effect=lambda entities, threshold, **_: [entities]),
        patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[]),
        patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False),
        patch("altk_evolve.llm.guidelines.clustering.completion", return_value=reply),
    ):
        assert client.consolidate_guidelines("ns").clusters_found == 1

    (merged,) = client.scan_entities("ns", filters={"type": "guideline"})
    assert merged.content == "merged"
    assert {(s["user_id"], s["agent_id"], s["conversation_id"]) for s in merged.metadata["sources"]} == {
        (subject, subject, subject),
        (subject, subject, "t2"),
    }
    assert merged.metadata["provenance_incomplete"] is False
    (fact,) = client.scan_entities("ns", filters={"type": "fact"})
    (source,) = fact.metadata["sources"]
    assert {key: source[key] for key in note} == note
    assert source["note"] == "Contact [REDACTED]"


# ── LegalHoldMemoryPlugin and consolidation ──────────────────────────

LEGAL_HOLD_SPEC = HookPluginSpec(
    name="legal_hold",
    kind="altk_evolve.hooks.plugins.legal_hold.LegalHoldMemoryPlugin",
    hooks=["memory_pre_delete"],
    mode="sequential",
    priority=5,
    on_error="fail",
)


def _consolidate_everything(client: EvolveClient) -> None:
    """One consolidate run that clusters all guidelines together and merges the cluster into one."""
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    # Every cluster here has two members.
    body = {"guidelines": [{"content": "merged", "rationale": "", "category": "strategy", "trigger": "", "source_indices": [0, 1]}]}
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(body)))])

    with (
        patch(
            "altk_evolve.llm.guidelines.clustering.cluster_entities",
            side_effect=lambda entities, threshold, **_: [entities] if len(entities) > 1 else [],
        ),
        patch("altk_evolve.llm.guidelines.clustering.get_supported_openai_params", return_value=[]),
        patch("altk_evolve.llm.guidelines.clustering.supports_response_schema", return_value=False),
        patch("altk_evolve.llm.guidelines.clustering.completion", return_value=reply),
    ):
        client.consolidate_guidelines("ns")


@pytest.mark.unit
def test_a_held_original_is_not_merged_again_by_later_consolidate_runs(tmp_path):
    client = make_client(tmp_path, LEGAL_HOLD_SPEC)
    client.ensure_namespace("ns")
    client.update_entities(
        "ns",
        [
            Entity(type="guideline", content=content, metadata={"task_description": "t", "category": "strategy", **extra})
            for content, extra in [("Check the balance first", {"legal_hold": True}), ("Check the balance before paying", {})]
        ],
        enable_conflict_resolution=False,
    )

    supports = []
    for _ in range(3):
        _consolidate_everything(client)
        merged = [e for e in client.scan_entities("ns", filters={"type": "guideline"}) if e.content == "merged"]
        supports.append([e.metadata["support"] for e in merged])

    # The held original survives every run; each run used to merge it again (2, 3, 4).
    assert supports == [[2], [2], [2]]
    held = next(e for e in client.scan_entities("ns", filters={"type": "guideline"}) if e.content != "merged")
    assert held.metadata["legal_hold"] is True

    # Once the hold is lifted, the next run finishes the delete without merging anything.
    client.patch_entity_metadata("ns", held.id, {"legal_hold": False})
    _consolidate_everything(client)
    (merged,) = client.scan_entities("ns", filters={"type": "guideline"})
    assert (merged.content, merged.metadata["support"]) == ("merged", 2)


@pytest.mark.unit
def test_pii_redaction_leaves_merged_from_intact(tmp_path):
    # Entity IDs are numbers, and a ten-digit one reads as a phone number to a redactor.
    pytest.importorskip("cpex_pii_filter")
    client = make_client(tmp_path, PII_SPEC)
    client.ensure_namespace("ns")
    merged_from = [{"id": "5551234567", "content_sha256": "0" * 64}]
    client.update_entities(
        "ns", [Entity(type="guideline", content="g", metadata={"merged_from": merged_from})], enable_conflict_resolution=False
    )

    (stored,) = client.scan_entities("ns")
    assert stored.metadata["merged_from"] == merged_from


@pytest.mark.unit
def test_a_still_held_original_stays_recorded_when_its_output_merges_again(tmp_path):
    client = make_client(tmp_path, LEGAL_HOLD_SPEC)
    client.ensure_namespace("ns")

    def add(content, **extra):
        metadata = {"task_description": "t", "category": "strategy", **extra}
        client.update_entities("ns", [Entity(type="guideline", content=content, metadata=metadata)], enable_conflict_resolution=False)

    add("Check the balance first", legal_hold=True)
    add("Check the balance before paying")
    _consolidate_everything(client)
    add("Check the balance twice")

    supports = []
    for _ in range(2):
        _consolidate_everything(client)
        supports.append([e.metadata["support"] for e in client.scan_entities("ns", filters={"type": "guideline"}) if e.content == "merged"])

    # The vetoed retry keeps the held original on the new output's record; losing it
    # would let the third run merge the original in again (support 4).
    assert supports == [[3], [3]]


_MERGED_FROM = [{"id": "5551234567", "content_sha256": "0123456789abcdef" * 4}]


@pytest.mark.unit
@pytest.mark.parametrize("redact", [True, False])
def test_the_secrets_hex_detector_leaves_merged_from_intact(tmp_path, redact):
    pytest.importorskip("cpex_secrets_detection")
    spec = SECRETS_SPEC.model_copy(update={"config": {"enabled": {"hex_secret_32": True}}, "on_error": "fail"})
    if not redact:
        spec.config.update(redact=False, block_on_detection=True)
    client = make_client(tmp_path, spec)
    client.ensure_namespace("ns")
    client.update_entities(
        "ns", [Entity(type="guideline", content="g", metadata={"merged_from": _MERGED_FROM})], enable_conflict_resolution=False
    )

    (stored,) = client.scan_entities("ns")
    assert stored.metadata["merged_from"] == _MERGED_FROM


@pytest.mark.unit
def test_pii_redaction_still_reaches_free_text_under_merged_from(tmp_path):
    pytest.importorskip("cpex_pii_filter")
    client = make_client(tmp_path, PII_SPEC)
    client.ensure_namespace("ns")
    forged = [{**_MERGED_FROM[0], "contact": "jane.doe@example.com"}, "call 555-123-4567"]
    client.update_entities(
        "ns",
        [Entity(type="guideline", content="g", metadata={"merged_from": [*_MERGED_FROM, *forged]})],
        enable_conflict_resolution=False,
    )

    (stored,) = client.scan_entities("ns")
    merged_from = stored.metadata["merged_from"]
    assert merged_from[0] == _MERGED_FROM[0]
    assert "jane.doe@example.com" not in str(merged_from[1:]) and "555-123-4567" not in str(merged_from[1:])


@pytest.mark.unit
@pytest.mark.parametrize(
    "kind",
    [
        "RSA PRIVATE KEY",
        "DSA PRIVATE KEY",
        "EC PRIVATE KEY",
        "OPENSSH PRIVATE KEY",
        "PRIVATE KEY",
        "ENCRYPTED PRIVATE KEY",
        "PGP PRIVATE KEY BLOCK",
    ],
)
def test_secrets_private_key_bodies_are_fully_redacted(tmp_path, kind):
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    block = f"-----BEGIN {kind}-----\nZmFrZS1rZXktYm9keQ==\n-----END {kind}-----"
    original = [{"role": "user", "content": f"before {block} after"}]
    assert dispatch_llm_pre_call(original, purpose="test") == [{"role": "user", "content": "before [REDACTED] after"}]
    assert block in original[0]["content"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "credential",
    [
        "ASIA" + "A" * 16,
        "-".join(["xoxs", "123456789", "FakeToken"]),
        "-".join(["xapp", "1", "A123456", "123456", "fake"]),
        "https://hooks.slack.com/" + "services/T123/B456/FakeValue",
    ],
)
def test_secrets_missing_token_shapes(tmp_path, credential):
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    assert dispatch_llm_pre_call([{"role": "user", "content": f"before {credential} after"}], purpose="test") == [
        {"role": "user", "content": "before [REDACTED] after"}
    ]


@pytest.mark.unit
@pytest.mark.parametrize("key", [_FAKE_AWS_KEY, "ASIA" + "A" * 16])
def test_secrets_aws_secret_after_access_key(tmp_path, key):
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    text = f"aws {key} secret={_FAKE_AWS_SECRET}"
    assert dispatch_llm_pre_call([{"role": "user", "content": text}], purpose="test") == [
        {"role": "user", "content": "aws [REDACTED] secret=[REDACTED]"}
    ]


@pytest.mark.unit
def test_supplementary_detectors_honor_switches_and_block_threshold():
    pytest.importorskip("cpex_secrets_detection")
    from altk_evolve.hooks.plugins.secrets import DEFAULT_ENABLED, SecretsFilterMemoryPlugin

    text = "-----BEGIN PRIVATE KEY-----\nfake body\n-----END PRIVATE KEY-----"  # pragma: allowlist secret
    items = [{"content": text}]
    disabled = SecretsFilterMemoryPlugin({"enabled": {key: False for key in DEFAULT_ENABLED}})
    assert disabled._redact_items(items) is None
    plugin = SecretsFilterMemoryPlugin({"redaction_text": "MASK"})
    assert plugin._redact_items(items) == [{"content": "MASK"}]
    block = SecretsFilterMemoryPlugin({"redact": False, "block_on_detection": True, "min_findings_to_block": 2})
    assert block._redact_items(items) is None
    with pytest.raises(RuntimeError, match="2 secret"):
        block._redact_items(items * 2)
    assert items == [{"content": text}]


@pytest.mark.unit
@pytest.mark.parametrize(
    "suffix",
    [
        ". Keep the output instructions.",
        "\nZmFrZQ==\nKeep this instruction.",
        "\nZmFrZQ==\n-----END EC PRIVATE KEY-----\nKeep this instruction.",
    ],
)
def test_incomplete_private_key_does_not_erase_unrelated_text(tmp_path, suffix):
    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    text = "Never commit -----BEGIN RSA PRIVATE KEY-----" + suffix  # pragma: allowlist secret
    result = dispatch_llm_pre_call([{"role": "user", "content": text}], purpose="test")[0]["content"]
    assert "PRIVATE KEY-----" not in result.split("\n")[0]
    assert "ZmFrZQ==" not in result
    assert suffix.split("\n")[-1] in result


@pytest.mark.unit
def test_slack_like_words_do_not_change_content_or_agent_identity(tmp_path):
    pytest.importorskip("cpex_secrets_detection")
    client = make_client(tmp_path, SECRETS_SPEC)
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [Entity(content="Use the xapp-config-loader and xoxs-prefix-loader", type="note", metadata={"agent_id": "xapp-config-loader"})],
        enable_conflict_resolution=False,
    )
    entity = client.search_entities("ns", limit=1)[0]
    assert entity.content == "Use the xapp-config-loader and xoxs-prefix-loader"
    assert entity.metadata["agent_id"] == "xapp-config-loader"


@pytest.mark.unit
def test_secrets_private_keys_inside_tuples_are_redacted():
    pytest.importorskip("cpex_secrets_detection")
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    block = "-----BEGIN RSA PRIVATE KEY-----\nZmFrZQ==\n-----END RSA PRIVATE KEY-----"  # pragma: allowlist secret
    items = [{"metadata": {"files": (block,)}}]
    result = SecretsFilterMemoryPlugin()._redact_items(items)
    assert result[0]["metadata"]["files"][0] == "[REDACTED]"
    assert items[0]["metadata"]["files"] == (block,)


@pytest.mark.unit
@pytest.mark.parametrize("assignment", ["secret={}", "secret: {}", 'secret="{}"', "secret_access_key={}"])
@pytest.mark.parametrize("key", [_FAKE_AWS_KEY, "ASIA" + "A" * 16])
def test_aws_pair_is_one_finding_and_masks_both_values(assignment, key):
    pytest.importorskip("cpex_secrets_detection")
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    text = f"aws {key} " + assignment.format(_FAKE_AWS_SECRET)
    items = [{"content": text}]
    redacted = SecretsFilterMemoryPlugin()._redact_items(items)[0]["content"]
    assert key not in redacted and _FAKE_AWS_SECRET not in redacted
    plugin = SecretsFilterMemoryPlugin({"redact": False, "block_on_detection": True, "min_findings_to_block": 2})
    assert plugin._redact_items(items) is None
    with pytest.raises(RuntimeError, match="2 secret"):
        plugin._redact_items(items * 2)
    assert items == [{"content": text}]


@pytest.mark.unit
def test_real_pii_conflict_hook_preserves_colliding_values_and_json_instructions(tmp_path, monkeypatch):
    import json
    from datetime import datetime
    from types import SimpleNamespace
    from altk_evolve.llm.conflict_resolution import conflict_resolution as cr
    from altk_evolve.schema.core import RecordedEntity

    pytest.importorskip("cpex_pii_filter")
    make_client(tmp_path, PII_SPEC)
    content = {"alice@example.com": "first", "bob@example.com": "second"}
    entity = RecordedEntity(id="7", type="fact", content=content, created_at=datetime.now())
    instructions = '{"contact": "admin@example.com"}'
    calls = []

    def complete(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        calls.append(prompt)
        assert '{"contact": "[REDACTED]"}' in prompt
        assert "@example.com" not in prompt
        incoming = json.loads(prompt.split("Below is a list of entities which I have collected up until now:", 1)[1].split("```", 2)[1])
        value = incoming[0]["content"]
        assert isinstance(value, str) and '"first"' in value and '"second"' in value
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"entities": [{**incoming[0], "event": "ADD"}]})), finish_reason="stop"
                )
            ]
        )

    monkeypatch.setattr(cr, "completion", complete)
    assert cr.resolve_conflicts([], [entity], custom_update_entities_prompt=instructions)[0].id == "7"
    assert len(calls) == 1
    assert entity.content == content


@pytest.mark.unit
@pytest.mark.parametrize("access_enabled,secret_enabled", [(False, False), (True, False), (False, True), (True, True)])
def test_aws_pair_respects_independent_detector_switches(access_enabled, secret_enabled):
    pytest.importorskip("cpex_secrets_detection")
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    text = f"aws {_FAKE_AWS_KEY} secret={_FAKE_AWS_SECRET}"
    items = [{"content": text}]
    plugin = SecretsFilterMemoryPlugin({"enabled": {"aws_access_key_id": access_enabled, "aws_secret_access_key": secret_enabled}})
    result = plugin._redact_items(items)
    if not access_enabled and not secret_enabled:
        assert result is None
    else:
        expected_key = "[REDACTED]" if access_enabled else _FAKE_AWS_KEY
        expected_secret = "[REDACTED]" if secret_enabled else _FAKE_AWS_SECRET
        assert result == [{"content": f"aws {expected_key} secret={expected_secret}"}]
    assert items == [{"content": text}]


@pytest.mark.unit
@pytest.mark.parametrize("container", [dict, list])
@pytest.mark.parametrize("complete", [False, True])
def test_real_secrets_conflict_preserves_structured_neighbors(tmp_path, monkeypatch, container, complete):
    import json
    from datetime import datetime
    from types import SimpleNamespace
    from altk_evolve.llm.conflict_resolution import conflict_resolution as cr
    from altk_evolve.schema.core import RecordedEntity

    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    block = "-----BEGIN RSA PRIVATE KEY-----\nZmFrZQ=="  # pragma: allowlist secret
    if complete:
        block += "\n-----END RSA PRIVATE KEY-----"
    content = {"key": block, "note": "Keep this instruction.", "count": 7} if container is dict else [block, "Keep this instruction.", 7]
    entity = RecordedEntity(id="7", type="fact", content=content, created_at=datetime.now())
    calls = []

    def complete_model(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        calls.append(prompt)
        incoming = json.loads(prompt.split("Below is a list of entities which I have collected up until now:", 1)[1].split("```", 2)[1])
        value = incoming[0]["content"]
        expected = (
            {"key": "[REDACTED]", "note": "Keep this instruction.", "count": 7}
            if container is dict
            else ["[REDACTED]", "Keep this instruction.", 7]
        )
        assert value == expected
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps({"entities": [{**incoming[0], "event": "ADD"}]})), finish_reason="stop"
                )
            ]
        )

    monkeypatch.setattr(cr, "completion", complete_model)
    assert cr.resolve_conflicts([], [entity])[0].id == "7"
    assert len(calls) == 1
    assert entity.content == content


@pytest.mark.unit
@pytest.mark.parametrize("as_json", [False, True])
def test_private_key_markers_do_not_consume_quoted_intervening_evidence(as_json):
    import json
    from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

    pytest.importorskip("cpex_secrets_detection")
    content = {
        "start": "-----BEGIN RSA PRIVATE KEY-----",  # pragma: allowlist secret
        "instruction": "Keep all evidence",
        "end": "-----END RSA PRIVATE KEY-----",
    }
    text = json.dumps(content) if as_json else str(content)
    result = SecretsFilterMemoryPlugin()._redact_items([{"content": text}])[0]["content"]
    assert "Keep all evidence" in result
    assert "BEGIN RSA" not in result
    if as_json:
        assert json.loads(result) == {**content, "start": "[REDACTED]"}


@pytest.mark.unit
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_real_secrets_fact_hook_redacts_structured_key_without_losing_following_turn(tmp_path, monkeypatch, newline):
    from copy import deepcopy
    from types import SimpleNamespace
    from altk_evolve.llm.fact_extraction import fact_extraction as facts

    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    block = "-----BEGIN RSA PRIVATE KEY-----" + newline + "ZmFrZQ=="  # pragma: allowlist secret
    messages = [
        {"role": "user", "content": {"key": block, "instruction": "Keep this instruction."}},
        {"role": "user", "content": "Keep this later turn."},
    ]
    original = deepcopy(messages)
    prompts = []

    def complete(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        assert "ZmFrZQ==" not in prompt and "BEGIN RSA" not in prompt
        assert "Keep this instruction." in prompt and "Keep this later turn." in prompt
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"facts": []}'))])

    monkeypatch.setattr(facts, "completion", complete)
    assert facts.extract_facts_from_messages(messages, False) == []
    assert len(prompts) == 1
    assert messages == original


@pytest.mark.unit
def test_real_secrets_conflict_custom_json_keeps_duplicate_instruction_values(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from altk_evolve.llm.conflict_resolution import conflict_resolution as cr

    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    instructions = r'{"key": "-----BEGIN RSA PRIVATE KEY-----\nZmFrZQ==", "note": "first", "note": "second"}'  # pragma: allowlist secret
    prompts = []

    def complete(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        prompts.append(prompt)
        assert '{"key": "[REDACTED]", "note": "first", "note": "second"}' in prompt
        assert "ZmFrZQ==" not in prompt
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"entities": []}'), finish_reason="stop")])

    monkeypatch.setattr(cr, "completion", complete)
    assert cr.resolve_conflicts([], [], custom_update_entities_prompt=instructions) == []
    assert len(prompts) == 1


@pytest.mark.unit
@pytest.mark.parametrize("serialize", ["plain", "json", "repr"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_pgp_quoted_armor_headers_are_redacted(tmp_path, serialize, newline):
    import json

    pytest.importorskip("cpex_secrets_detection")
    make_client(tmp_path, SECRETS_SPEC)
    block = newline.join(
        [
            "-----BEGIN PGP PRIVATE KEY BLOCK-----",  # pragma: allowlist secret
            "Version: Test",
            'Comment: Alice\'s "private" key',
            "",
            "ZmFrZVByaXZhdGVLZXlNYXRlcmlhbA==",
            "=abcd",
            "-----END PGP PRIVATE KEY BLOCK-----",
        ]
    )
    value = {"key": block, "instruction": "Keep this evidence"}
    content = block if serialize == "plain" else (json.dumps(value) if serialize == "json" else str(value))
    result = dispatch_llm_pre_call([{"role": "user", "content": content}], purpose="test")[0]["content"]
    assert "ZmFrZVByaXZhdGVLZXlNYXRlcmlhbA" not in result
    assert "Alice" not in result
    if serialize != "plain":
        assert "Keep this evidence" in result


@pytest.mark.unit
def test_secrets_preserve_structural_metadata_and_scan_free_text(tmp_path):
    pytest.importorskip("cpex_secrets_detection")
    subject = "ASIA" + "A" * 16
    client = make_client(tmp_path, SECRETS_SPEC)
    client.create_namespace("ns")
    client.update_entities(
        "ns",
        [
            Entity(
                type="note",
                content="safe",
                metadata={
                    "agent_id": subject,
                    "sources": [{"conversation_id": subject, "agent_id": subject, "note": subject}],
                    "note": subject,
                },
            )
        ],
        enable_conflict_resolution=False,
    )
    (entity,) = client.scan_entities("ns", filters={"agent_id": subject})
    assert entity.metadata["agent_id"] == subject
    assert entity.metadata["note"] == "[REDACTED]"
    (source,) = entity.metadata["sources"]
    assert {key: source[key] for key in ("conversation_id", "agent_id", "note")} == {
        "conversation_id": subject,
        "agent_id": subject,
        "note": "[REDACTED]",
    }
