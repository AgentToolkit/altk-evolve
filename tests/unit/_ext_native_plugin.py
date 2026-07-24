"""Third-party-style NATIVE hook plugins — import NO cpex, no execution engine.

This module deliberately depends ONLY on ``altk_evolve.hooks.plugin`` (and, via
the payloads it receives at runtime, ``altk_evolve.hooks.types``). It proves a
third party can author, ship, and unit-test a hook plugin without cpex on the
path — the engine sits behind an adapter the plugin never sees.

Referenced by ``kind`` from ``test_hooks_native_third_party.py``.
"""

from __future__ import annotations

from typing import Any

from altk_evolve.hooks.plugin import HookContext, HookPluginBase


class TenantTagPlugin(HookPluginBase):
    """Native transform on ``memory_pre_write``: stamps a tenant tag into metadata.

    Returns a replacement payload via ``payload.replace(...)`` — never mutates.
    """

    def memory_pre_write(self, payload: Any, context: HookContext) -> Any | None:
        tenant = self.config.get("tenant", "acme")
        entities = [{**e, "metadata": {**(e.get("metadata") or {}), "tenant": tenant}} for e in payload.entities]
        return payload.replace(entities=entities)


class RejectAllWritesPlugin(HookPluginBase):
    """Native fail-closed policy on ``memory_pre_write``: always raises to halt.

    A raise from a native plugin must surface as ``MemoryPolicyViolation`` with
    nothing stored (fail-closed), exactly like a cpex plugin's crash.
    """

    def memory_pre_write(self, payload: Any, context: HookContext) -> Any | None:
        raise RuntimeError("third-party policy rejects this write")
