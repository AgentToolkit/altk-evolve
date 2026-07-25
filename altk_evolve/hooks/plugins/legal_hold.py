"""Legal-hold policy for memory deletion."""

from __future__ import annotations

from typing import Any

from altk_evolve.hooks.plugin import HookContext, HookPluginBase, HookPolicyViolation


class LegalHoldMemoryPlugin(HookPluginBase):
    """Block deletion when stored metadata contains ``legal_hold: true``."""

    def memory_pre_delete(self, payload: Any, context: HookContext) -> None:
        if (payload.metadata or {}).get("legal_hold") is True:
            raise HookPolicyViolation(
                "entity is under legal hold",
                code="LEGAL_HOLD",
                details={"entity_id": payload.entity_id},
            )
