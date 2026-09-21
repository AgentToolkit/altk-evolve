"""Metadata normalizer plugin: stamps canonical metadata on every write.

Core/plugin split: the domain logic lives in :func:`normalize_entities`, a pure
function with no engine imports — importable and testable without any extra. The
:class:`MetadataNormalizerPlugin` below is a **native** hook plugin (no cpex
import, no engine coupling): it subclasses
:class:`~altk_evolve.hooks.plugin.HookPluginBase`, reads its plain ``config``
dict, calls the core, and returns a replacement payload (or ``None``).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

from altk_evolve.hooks.plugin import HookContext, HookPluginBase


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


class MetadataNormalizerPlugin(HookPluginBase):
    """Native plugin: :func:`normalize_entities` on ``memory_pre_write``.

    Config keys (all optional):
      - ``stamp_trace_id`` (bool, default True)
      - ``stamp_created_at`` (bool, default True)

    Fail-closed by convention (spec ``on_error: fail``): a normalization crash
    halts the write rather than persisting un-normalized metadata.
    """

    def memory_pre_write(self, payload: Any, context: HookContext) -> Any | None:
        normalized = normalize_entities(
            payload.entities,
            stamp_trace_id=self.config.get("stamp_trace_id", True),
            stamp_created_at=self.config.get("stamp_created_at", True),
        )
        if normalized is None:
            return None
        return payload.replace(entities=normalized)
