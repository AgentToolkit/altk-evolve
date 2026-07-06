"""Access stamp plugin: records ``last_accessed`` on entities returned by reads."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from altk_evolve.hooks.types import HAS_CPEX, HookType

logger = logging.getLogger(__name__)

if HAS_CPEX:
    from cpex.framework import Plugin
    from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult

    def _default_config() -> PluginConfig:
        return PluginConfig(
            name="access_stamp",
            kind="altk_evolve.hooks.plugins.access_stamp.AccessStampPlugin",
            hooks=[HookType.MEMORY_POST_READ.value],
            mode=PluginMode.FIRE_AND_FORGET,
            priority=50,
            on_error=OnError.IGNORE,
        )

    class AccessStampPlugin(Plugin):
        """Stamps ``last_accessed`` (ISO-8601 UTC) on entities returned by public reads.

        Runs in fire_and_forget mode: it cannot modify or block the read, only
        record the access via the metadata-patch path.

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

        def __init__(self, config: PluginConfig | None = None) -> None:
            super().__init__(config or _default_config())

        async def memory_post_read(self, payload: Any, context: Any) -> Any:
            backend = context.global_context.state.get("backend") if context.global_context.state else None
            if backend is None:
                logger.debug("AccessStampPlugin: no backend in hook context; skipping.")
                return PluginResult(continue_processing=True)

            stamp = datetime.datetime.now(datetime.UTC).isoformat()
            for entity in payload.entities:
                entity_id = entity.get("id")
                if not entity_id:
                    continue
                try:
                    backend.update_entity_metadata(payload.namespace_id, str(entity_id), {"last_accessed": stamp})
                except Exception:
                    logger.debug("AccessStampPlugin: failed to stamp entity %s.", entity_id, exc_info=True)
            return PluginResult(continue_processing=True)

else:

    class AccessStampPlugin:  # type: ignore[no-redef]
        """Stub — install 'altk-evolve[hooks]' for hook plugin support."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("AccessStampPlugin requires the CPEX plugin framework. Install it with: pip install 'altk-evolve[hooks]'")
