"""Metadata normalizer plugin: stamps canonical metadata on every write.

Core/shim split: the domain logic lives in :func:`normalize_entities`, a pure
function with no cpex imports — importable and testable without the
``[hooks]`` extra. The cpex ``Plugin`` subclass below is a thin shim that
parses plugin config, calls the core, and wraps the result in a
``PluginResult``.
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

from altk_evolve.hooks.types import HAS_CPEX, HookType


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def normalize_entities(
    entities: list[dict],
    *,
    stamp_trace_id: bool = True,
    stamp_created_at: bool = True,
    now: Callable[[], datetime.datetime] = _utc_now,
) -> list[dict] | None:
    """Return normalized copies of ``entities``, or ``None`` when nothing changed.

    Why: the MCP server's ``save_trajectory`` writes ``task_id`` metadata
    while Phoenix sync writes ``trace_id`` for the same concept — and
    downstream consumers (e.g. session -> derived-entity cascade cleanup)
    key on ``trace_id``, so MCP-saved sessions currently miss it.

    - ``stamp_trace_id``: copy ``task_id`` into ``trace_id`` when only the
      former is present in an entity's metadata.
    - ``stamp_created_at``: stamp ``created_at`` (ISO-8601 UTC) when absent.
    - ``now``: injectable clock, for deterministic tests.

    Pure: input dicts are never mutated; the returned list holds fresh copies.
    """
    changed = False
    normalized: list[dict] = []
    for entity in entities:
        metadata = dict(entity.get("metadata") or {})
        if stamp_trace_id and "trace_id" not in metadata and "task_id" in metadata:
            metadata["trace_id"] = metadata["task_id"]
            changed = True
        if stamp_created_at and "created_at" not in metadata:
            metadata["created_at"] = now().isoformat()
            changed = True
        normalized.append({**entity, "metadata": metadata})
    return normalized if changed else None


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
            # Fail-closed: a normalization crash halts the write rather than
            # persisting un-normalized (and potentially non-compliant) metadata.
            on_error=OnError.FAIL,
        )

    class MetadataNormalizerPlugin(Plugin):
        """Thin cpex shim: :func:`normalize_entities` on ``memory_pre_write``.

        Config keys (all optional):
          - ``stamp_trace_id`` (bool, default True)
          - ``stamp_created_at`` (bool, default True)
        """

        def __init__(self, config: PluginConfig | None = None) -> None:
            super().__init__(config or _default_config())

        async def memory_pre_write(self, payload: Any, context: Any) -> Any:
            cfg = self._config.config or {}
            normalized = normalize_entities(
                payload.entities,
                stamp_trace_id=cfg.get("stamp_trace_id", True),
                stamp_created_at=cfg.get("stamp_created_at", True),
            )
            if normalized is None:
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
