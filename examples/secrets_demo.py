#!/usr/bin/env python3
"""Structured-secrets redaction demo for the altk_evolve memory hook seam.

A THIRD redaction method alongside the two PII ones: it targets
CREDENTIALS/TOKENS (AWS keys, GitHub/Slack tokens, private-key blocks) that
neither PII path aims at. Backed by cpex-secrets-detection's Rust core, wired as
a native plugin on the seam.

Shows, mirroring examples/hooks_demo.py:
  1. SecretsFilterMemoryPlugin on ``memory_pre_write`` — a fake AWS key and
     GitHub token are scrubbed BEFORE the entity is persisted (read back from
     the filesystem store to prove it).
  2. The same plugin on ``llm_pre_call`` — ``dispatch_llm_pre_call`` egress is
     redacted too, so no completion call site ever sees the raw secret.
  3. A non-secret string ("deploy only on Fridays") survives verbatim.

Guard: skipped with a note when the ``[secrets]`` extra
(cpex-secrets-detection) is not installed.

Run:
    uv sync --extra hooks --extra secrets
    uv run --no-sync python examples/secrets_demo.py
"""

from __future__ import annotations

import importlib.util
import tempfile

from altk_evolve.backend.filesystem import FilesystemSettings
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import dispatch_llm_pre_call, shutdown_hooks
from altk_evolve.schema.core import Entity

FILLER = "[REDACTED]"

# All fake / non-functional — obviously-invalid demo credentials, never real.
FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"  # pragma: allowlist secret
FAKE_GITHUB_TOKEN = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret
NON_SECRET = "deploy only on Fridays"

MEMORY = (
    f"CI notes: the pipeline uses AWS key {FAKE_AWS_KEY} and GitHub token "  # pragma: allowlist secret
    f"{FAKE_GITHUB_TOKEN}; policy is to {NON_SECRET}."
)


def main() -> int:
    if importlib.util.find_spec("cpex_secrets_detection") is None:
        print("This demo needs the [secrets] extra (cpex-secrets-detection). Try:")
        print("    uv sync --extra hooks --extra secrets")
        print("    uv run --no-sync python examples/secrets_demo.py")
        return 0

    plugins = [
        HookPluginSpec(
            name="secrets_filter_memory",
            kind="altk_evolve.hooks.plugins.secrets.SecretsFilterMemoryPlugin",
            hooks=["memory_pre_write", "llm_pre_call"],
            mode="sequential",  # sequential (not transform) so it can BLOCK, not just redact
            priority=10,
            on_error="fail",  # fail-closed: never pass content with secrets through
            config={"redact": True, "redaction_text": FILLER},
        )
    ]

    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=tempfile.mkdtemp(prefix="evolve_secrets_demo_")),
        hooks=HooksConfig(plugins=plugins),
    )
    client = EvolveClient(config)
    client.create_namespace("demo")

    print("Structured-secrets redaction on the memory hook seam\n")
    print("  IN (agent tried to remember):")
    print(f"    {MEMORY}\n")

    # 1: write the secret-bearing memory; redaction fires at memory_pre_write.
    client.update_entities(
        "demo",
        [Entity(content=MEMORY, type="guideline")],
        enable_conflict_resolution=False,
    )
    stored = client.search_entities("demo", limit=10)[0]
    print("  OUT (what actually got stored):")
    print(f"    {stored.content}\n")

    # 2: LLM egress redaction — what any completion call site would see.
    messages = dispatch_llm_pre_call(
        [{"role": "user", "content": f"Use {FAKE_AWS_KEY} to list buckets"}],  # pragma: allowlist secret
        purpose="demo",
    )
    print("  LLM egress (dispatch_llm_pre_call):")
    print(f"    {messages[0]['content']}\n")

    # Assertions: secrets gone from BOTH surfaces; the non-secret policy survives.
    ok = True
    if FAKE_AWS_KEY in stored.content or FAKE_GITHUB_TOKEN in stored.content:
        print("FAIL — a secret leaked into storage.")
        ok = False
    if FAKE_AWS_KEY in messages[0]["content"]:
        print("FAIL — a secret leaked to LLM egress.")
        ok = False
    if NON_SECRET not in stored.content:
        print(f"FAIL — the non-secret string '{NON_SECRET}' was over-redacted.")
        ok = False

    if ok:
        print("OK  — the AWS key and GitHub token were redacted on write AND on LLM egress;")
        print(f"      the non-secret policy ('{NON_SECRET}') was preserved verbatim.")

    shutdown_hooks()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
