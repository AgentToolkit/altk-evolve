"""Metadata normalizer plugin: stamps canonical metadata on every write."""

from __future__ import annotations

import datetime
from typing import Any

from altk_evolve.hooks.types import HAS_CPEX, HookType

if HAS_CPEX:
    from cpex.framework import Plugin
    from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult

    def _default_config() -> PluginConfig:
        return PluginConfig(
            name="metadata_normalizer",
            kind="altk_evolve.hooks.plugins.normalizer.MetadataNormalizerPlugin",
            hooks=[HookType.MEMORY_PRE_WRITE.value],
            mode=PluginMode.TRANSFORM,
            priority=40,
            on_error=OnError.IGNORE,
        )

    class MetadataNormalizerPlugin(Plugin):
        """Stamps canonical metadata on entities entering ``update_entities``.

        Why: the MCP server's ``save_trajectory`` writes ``task_id`` metadata
        while Phoenix sync writes ``trace_id`` for the same concept — and
        downstream consumers (e.g. session -> derived-entity cascade cleanup)
        key on ``trace_id``, so MCP-saved sessions currently miss it. This
        plugin copies ``task_id`` into ``trace_id`` when only the former is
        present, and stamps ``created_at`` (ISO-8601 UTC) when absent.

        Config keys (all optional):
          - ``stamp_trace_id`` (bool, default True)
          - ``stamp_created_at`` (bool, default True)
        """

        def __init__(self, config: PluginConfig | None = None) -> None:
            super().__init__(config or _default_config())

        async def memory_pre_write(self, payload: Any, context: Any) -> Any:
            cfg = self._config.config or {}
            stamp_trace_id = cfg.get("stamp_trace_id", True)
            stamp_created_at = cfg.get("stamp_created_at", True)

            changed = False
            normalized: list[dict] = []
            for entity in payload.entities:
                metadata = dict(entity.get("metadata") or {})
                if stamp_trace_id and "trace_id" not in metadata and "task_id" in metadata:
                    metadata["trace_id"] = metadata["task_id"]
                    changed = True
                if stamp_created_at and "created_at" not in metadata:
                    metadata["created_at"] = datetime.datetime.now(datetime.UTC).isoformat()
                    changed = True
                normalized.append({**entity, "metadata": metadata})

            if not changed:
                return PluginResult(continue_processing=True)
            return PluginResult(
                continue_processing=True,
                modified_payload=payload.model_copy(update={"entities": normalized}),
            )

else:

    class MetadataNormalizerPlugin:  # type: ignore[no-redef]
        """Stub — install 'altk-evolve[hooks]' for hook plugin support."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "MetadataNormalizerPlugin requires the CPEX plugin framework. Install it with: pip install 'altk-evolve[hooks]'"
            )
