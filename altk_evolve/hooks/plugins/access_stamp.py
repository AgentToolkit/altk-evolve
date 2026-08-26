"""Access stamp plugin: records ``last_accessed`` on entities returned by reads.

Core/plugin split: the stamping decision + formatting logic lives in
:func:`build_access_stamps`, a pure function with no engine imports —
importable and testable without any extra. The :class:`AccessStampPlugin`
below is a **native** hook plugin (no cpex import): it applies the returned
patches through the live backend riding in the :class:`HookContext`.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from typing import Any

from altk_evolve.hooks.plugin import HookContext, HookPluginBase

logger = logging.getLogger(__name__)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def build_access_stamps(
    entities: list[dict],
    *,
    now: Callable[[], datetime.datetime] = _utc_now,
) -> list[tuple[str, dict]]:
    """Return ``(entity_id, metadata_patch)`` pairs stamping ``last_accessed``.

    - One shared ISO-8601 UTC timestamp per batch (all entities of a read get
      the same stamp).
    - Entities without a truthy ``id`` are skipped; ids are coerced to ``str``.
    - Pure: the caller applies each patch (via the metadata-patch path).

    ``now`` is an injectable clock, for deterministic tests.
    """
    stamp = now().isoformat()
    return [(str(entity["id"]), {"last_accessed": stamp}) for entity in entities if entity.get("id")]


class AccessStampPlugin(HookPluginBase):
    """Native plugin: applies :func:`build_access_stamps` patches on ``memory_post_read``.

    Registered in ``fire_and_forget`` mode: it cannot modify or block the read,
    only record the access via the metadata-patch path (returns ``None``).

    Read cost: fire-and-forget tasks are awaited before the sync bridge
    returns (see ``altk_evolve.hooks.manager``), so the stamp is not free
    for the reader — every public read pays one metadata write per
    returned entity before ``search_entities`` returns (~3.7 ms vs
    ~0.1 ms for a 10-entity read on the filesystem backend; N extra
    store round trips per read on milvus/postgres). Enable only where
    access audit trails are worth that latency.

    Recursion safety: ``update_entity_metadata`` fires
    ``memory_pre_metadata_patch`` (not ``memory_post_read``), and its base
    implementation reads through the internal ``_search_entities_impl``
    seam — so stamping can never re-trigger this plugin. It also does not
    subscribe to any write hook, so normalizer/PII plugins cannot loop
    through it.
    """

    def memory_post_read(self, payload: Any, context: HookContext) -> Any | None:
        backend = context.backend
        if backend is None:
            logger.debug("AccessStampPlugin: no backend in hook context; skipping.")
            return None

        for entity_id, patch in build_access_stamps(payload.entities):
            try:
                backend.update_entity_metadata(payload.namespace_id, entity_id, patch)
            except Exception:
                logger.debug("AccessStampPlugin: failed to stamp entity %s.", entity_id, exc_info=True)
        return None
