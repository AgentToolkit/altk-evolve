"""Hook taxonomy for the altk_evolve memory hook seam.

Defines the hook-type enum and the frozen payload classes that flow through
the CPEX ``PluginManager`` when the optional ``cpex`` package is installed
(``pip install 'altk-evolve[hooks]'``). Without cpex, the payload classes fall
back to plain frozen pydantic models so this module stays importable and the
seam remains a no-op.

Payloads are immutable by design: plugins propose changes by returning a
``model_copy(update={...})`` via ``PluginResult.modified_payload``. Changes
never flow back through mutation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import ConfigDict, Field

try:
    from cpex.framework.hooks.registry import get_hook_registry
    from cpex.framework.models import PluginPayload as _PayloadBase
    from cpex.framework.models import PluginResult

    HAS_CPEX = True
except ImportError:
    HAS_CPEX = False

    from pydantic import BaseModel

    class _PayloadBase(BaseModel):  # type: ignore[no-redef]
        """Frozen stand-in for ``cpex.framework.models.PluginPayload``."""

        model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


class HookType(str, Enum):
    """All altk_evolve hook types.

    Write family (fired in the backend layer so no frontend can bypass them):
      - MEMORY_PRE_WRITE: before conflict resolution in ``update_entities``.
      - MEMORY_PRE_METADATA_PATCH: before ``update_entity_metadata`` applies a patch.
      - MEMORY_PRE_DELETE: before ``delete_entity_by_id`` removes an entity.
      - MEMORY_PRE_NAMESPACE_DELETE: before ``delete_namespace`` drops a namespace.

    Read family (public API reads only; internal reads never fire it):
      - MEMORY_POST_READ: after ``search_entities`` returns results.

    LLM-egress family:
      - LLM_PRE_CALL: immediately before every litellm ``completion`` call.
    """

    MEMORY_PRE_WRITE = "memory_pre_write"
    MEMORY_PRE_METADATA_PATCH = "memory_pre_metadata_patch"
    MEMORY_PRE_DELETE = "memory_pre_delete"
    MEMORY_PRE_NAMESPACE_DELETE = "memory_pre_namespace_delete"
    MEMORY_POST_READ = "memory_post_read"
    LLM_PRE_CALL = "llm_pre_call"


class EvolveBasePayload(_PayloadBase):  # type: ignore[valid-type,misc]
    """Frozen base for all altk_evolve hook payloads.

    ``backend_kind`` names the backend class handling the operation (empty for
    LLM-egress hooks). The live backend object rides in
    ``GlobalContext.state["backend"]`` for plugins that need to call back into
    the store (e.g. access stamping).
    """

    backend_kind: str = ""


class MemoryPreWritePayload(EvolveBasePayload):
    """Entity batch about to be written via ``update_entities``.

    ``entities`` are ``Entity`` dumps (``content``/``type``/``metadata``).
    Fired after namespace validation and BEFORE conflict resolution, so
    transform plugins run before any content is sent to an LLM.
    """

    namespace_id: str = ""
    entities: list[dict] = Field(default_factory=list)
    caller: str = "update_entities"


class MemoryPreMetadataPatchPayload(EvolveBasePayload):
    """Metadata patch about to be merged into an entity."""

    namespace_id: str = ""
    entity_id: str = ""
    metadata_patch: dict = Field(default_factory=dict)


class MemoryPreDeletePayload(EvolveBasePayload):
    """Entity about to be deleted via the public ``delete_entity_by_id``."""

    namespace_id: str = ""
    entity_id: str = ""


class MemoryPreNamespaceDeletePayload(EvolveBasePayload):
    """Namespace about to be deleted."""

    namespace_id: str = ""


class MemoryPostReadPayload(EvolveBasePayload):
    """Results of a public ``search_entities`` call.

    ``entities`` are JSON-mode ``RecordedEntity`` dumps (``id``/``type``/
    ``content``/``created_at``/``metadata``). Transform plugins may filter or
    redact the list before it reaches the caller.
    """

    namespace_id: str = ""
    entities: list[dict] = Field(default_factory=list)
    query: str | None = None
    filters: dict = Field(default_factory=dict)


class LLMPreCallPayload(EvolveBasePayload):
    """Chat messages about to leave the process via litellm ``completion``.

    ``purpose`` names the call site (e.g. ``fact_extraction``,
    ``conflict_resolution``) so plugins can scope redaction policies.
    """

    messages: list[dict] = Field(default_factory=list)
    purpose: str = ""
    model: str | None = None


#: hook type -> payload class, used for registration with the CPEX hook registry.
HOOK_PAYLOADS: dict[HookType, type] = {
    HookType.MEMORY_PRE_WRITE: MemoryPreWritePayload,
    HookType.MEMORY_PRE_METADATA_PATCH: MemoryPreMetadataPatchPayload,
    HookType.MEMORY_PRE_DELETE: MemoryPreDeletePayload,
    HookType.MEMORY_PRE_NAMESPACE_DELETE: MemoryPreNamespaceDeletePayload,
    HookType.MEMORY_POST_READ: MemoryPostReadPayload,
    HookType.LLM_PRE_CALL: LLMPreCallPayload,
}


def register_evolve_hooks() -> None:
    """Register all altk_evolve hook types with the CPEX hook registry.

    Idempotent: already-registered hook types are skipped. No-op when cpex is
    not installed.
    """
    if not HAS_CPEX:
        return
    registry: Any = get_hook_registry()
    for hook_type, payload_cls in HOOK_PAYLOADS.items():
        if not registry.is_registered(hook_type.value):
            registry.register_hook(hook_type.value, payload_cls, PluginResult)
