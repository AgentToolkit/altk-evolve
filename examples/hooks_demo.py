"""End-to-end demo of the altk_evolve memory hook seam (filesystem backend).

Shows:
  1. MetadataNormalizerPlugin stamping trace_id (from task_id) and created_at
     on write.
  2. AccessStampPlugin stamping last_accessed on read.
  3. PIIFilterMemoryPlugin redacting a fake email/SSN on write and on LLM
     egress (skipped when cpex-pii-filter is not installed).

Run:
    uv sync --extra hooks --extra pii
    uv run --no-sync python examples/hooks_demo.py
"""

import tempfile

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import dispatch_llm_pre_call, shutdown_hooks
from altk_evolve.schema.core import Entity


def main() -> None:
    try:
        import cpex_pii_filter  # noqa: F401

        has_pii_filter = True
    except ImportError:
        has_pii_filter = False

    plugins = [
        HookPluginSpec(
            name="metadata_normalizer",
            kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
            hooks=["memory_pre_write"],
            mode="transform",
            priority=40,
        ),
        HookPluginSpec(
            name="access_stamp",
            kind="altk_evolve.hooks.plugins.access_stamp.AccessStampPlugin",
            hooks=["memory_post_read"],
            mode="fire_and_forget",
        ),
    ]
    if has_pii_filter:
        plugins.append(
            HookPluginSpec(
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
        )

    # Equivalent YAML-driven setup: HooksConfig(enabled=True, plugins_yaml="examples/hooks_plugins.yaml")
    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=tempfile.mkdtemp(prefix="evolve_hooks_demo_")),
        hooks=HooksConfig(enabled=True, plugins=plugins),
    )
    client = EvolveClient(config)
    client.create_namespace("demo")

    # 1 + 3: write an entity carrying PII and an MCP-style task_id.
    entity = Entity(
        content="Customer Dana Whitfield, email dana.whitfield@example.com, SSN 123-45-6789.",
        type="note",
        metadata={"task_id": "task-0042"},
    )
    client.update_entities("demo", [entity], enable_conflict_resolution=False)

    stored = client.search_entities("demo", limit=10)[0]
    print("stored content:  ", stored.content)
    print("stored metadata: ", {k: stored.metadata[k] for k in sorted(stored.metadata)})

    # 2: the read above fired memory_post_read -> last_accessed was stamped.
    re_read = client.get_entity_by_id("demo", stored.id)
    assert re_read is not None
    print("last_accessed:   ", re_read.metadata.get("last_accessed"))

    # 3: LLM egress redaction (what any litellm completion call site sees).
    messages = dispatch_llm_pre_call(
        [{"role": "user", "content": "Summarize: call 415-555-0199 or mail dana.whitfield@example.com"}],
        purpose="demo",
    )
    print("llm egress:      ", messages[0]["content"])

    shutdown_hooks()


if __name__ == "__main__":
    main()
