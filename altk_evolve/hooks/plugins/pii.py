"""PII redaction plugin: aliases cpex-pii-filter onto the altk_evolve hook types.

``cpex_pii_filter.pii_filter.PIIFilterPlugin`` is a native CPEX plugin, but its
handlers are only the four built-in hook names (``tool_pre_invoke`` etc.).
CPEX discovers handlers by method name == hook-type string, so attaching it to
our custom hooks needs this thin aliasing subclass that exposes
``memory_pre_write`` and ``llm_pre_call`` and delegates to the native handler.

Requires ``pip install 'altk-evolve[pii]'`` (cpex + cpex-pii-filter).

Unlike ``normalizer``/``access_stamp`` this module has no engine-agnostic
core: adapting cpex-pii-filter onto our hook types IS its domain logic, so
the cpex coupling is the point (the redaction logic itself lives — and is
tested — in the cpex-pii-filter package).
"""

from __future__ import annotations

from typing import Any

from altk_evolve.hooks.types import HookType

try:
    from cpex.framework.hooks.tools import ToolPreInvokePayload
    from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult
    from cpex_pii_filter.pii_filter import PIIFilterPlugin as _PIIFilterPlugin

    _HAS_PII_FILTER = True
except ImportError:
    _HAS_PII_FILTER = False

if _HAS_PII_FILTER:

    def _default_config() -> PluginConfig:
        return PluginConfig(
            name="pii_filter_memory",
            kind="altk_evolve.hooks.plugins.pii.PIIFilterMemoryPlugin",
            hooks=[HookType.MEMORY_PRE_WRITE.value, HookType.LLM_PRE_CALL.value],
            mode=PluginMode.TRANSFORM,
            priority=10,
            on_error=OnError.IGNORE,
            config={
                "detect_email": True,
                "detect_ssn": True,
                "detect_phone": True,
                "default_mask_strategy": "redact",
                "redaction_text": "[REDACTED]",
            },
        )

    class PIIFilterMemoryPlugin(_PIIFilterPlugin):
        """Regex PII redaction on memory writes and LLM egress.

        Accepts the same ``config`` keys as cpex-pii-filter (``detect_email``,
        ``detect_ssn``, ``detect_phone``, ``custom_patterns``,
        ``default_mask_strategy``, ``redaction_text``, ...).
        """

        def __init__(self, config: PluginConfig | None = None) -> None:
            super().__init__(config or _default_config())

        async def _delegate(self, payload: Any, context: Any, field: str) -> Any:
            """Round-trip one payload field through the native tool_pre_invoke handler."""
            tool_payload = ToolPreInvokePayload(name=f"altk_evolve.{field}", args={field: getattr(payload, field)})
            result = await self.tool_pre_invoke(tool_payload, context)
            if result is None:
                return PluginResult(continue_processing=True)
            modified = None
            if result.modified_payload is not None:
                modified = payload.model_copy(update={field: result.modified_payload.args[field]})
            return PluginResult(
                continue_processing=result.continue_processing,
                violation=result.violation,
                modified_payload=modified,
            )

        async def memory_pre_write(self, payload: Any, context: Any) -> Any:
            return await self._delegate(payload, context, "entities")

        async def llm_pre_call(self, payload: Any, context: Any) -> Any:
            return await self._delegate(payload, context, "messages")

else:

    class PIIFilterMemoryPlugin:  # type: ignore[no-redef]
        """Stub — install 'altk-evolve[pii]' for PII redaction support."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("PIIFilterMemoryPlugin requires cpex and cpex-pii-filter. Install them with: pip install 'altk-evolve[pii]'")
