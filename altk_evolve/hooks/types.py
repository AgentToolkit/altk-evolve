"""Hook taxonomy for the altk_evolve memory hook seam.

Defines the hook-type enum and the frozen payload classes. Both are
engine-agnostic; when the optional ``cpex`` package — the shipped execution
engine — is installed (``pip install 'altk-evolve[hooks]'``), the payloads
subclass cpex's ``PluginPayload`` so they flow through its ``PluginManager``.
Without cpex, the payload classes fall back to plain frozen pydantic models so
this module stays importable and the seam remains a no-op.

Payloads are immutable by design: plugins propose changes by returning a
``model_copy(update={...})`` via ``PluginResult.modified_payload``. Changes
never flow back through mutation.
"""

from __future__ import annotations

import importlib.util
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# Cheap availability probe: ``find_spec`` locates the top-level ``cpex`` package
# WITHOUT importing ``cpex.framework`` — a ~400ms import that also pulls in
# fastapi/mcp/prometheus. The heavy import is deferred to engine initialization
# (``initialize_hooks``/``register_evolve_hooks``), so importing this module —
# and any backend that imports the hook seam — stays cheap when hooks are off.
def _cpex_installed() -> bool:
    # find_spec may raise (ImportError from a hostile/blocking finder,
    # ModuleNotFoundError/ValueError on some edge cases) rather than return
    # None; any failure to locate cpex means "not installed".
    try:
        return importlib.util.find_spec("cpex") is not None
    except (ImportError, ValueError):
        return False


HAS_CPEX = _cpex_installed()


class _PayloadBase(BaseModel):
    """Frozen, engine-agnostic base for hook payloads.

    Structurally identical to ``cpex.framework.models.PluginPayload`` (a frozen
    pydantic model), so payloads are importable and constructible WITHOUT cpex.
    When the cpex engine initializes, ``bind_engine_payloads()`` rebuilds each
    payload as a ``PluginPayload`` subclass (see ``active_payload_cls``); until
    then the seam is a no-op and no payload is ever dispatched through cpex.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


def engine_available() -> bool:
    """Whether a plugin execution engine is installed.

    Currently true when the optional ``cpex`` package is importable
    (``pip install 'altk-evolve[hooks]'``). Uses a cheap ``find_spec`` probe
    that does not trigger the heavy ``cpex.framework`` import.
    """
    return HAS_CPEX


class HookType(str, Enum):
    """All altk_evolve hook types.

    Write family (fired in the backend layer so no frontend can bypass them):
      - MEMORY_PRE_WRITE: before conflict resolution in ``update_entities``.
      - MEMORY_PRE_METADATA_PATCH: before ``update_entity_metadata`` applies a patch.
      - MEMORY_PRE_DELETE: before any entity delete — the public
        ``delete_entity_by_id`` AND conflict-resolution DELETE verdicts
        inside ``update_entities``.
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


class EvolveBasePayload(_PayloadBase):
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
    """Entity about to be deleted.

    Fires on BOTH delete paths: the public ``delete_entity_by_id`` and
    conflict-resolution DELETE verdicts inside ``update_entities`` (all
    routed through ``BaseEntityBackend._guarded_delete``).

    ``metadata`` carries the stored entity's metadata when it could be
    resolved (``None`` when the entity was not found), so policy plugins can
    key on fields like ``legal_hold``.
    """

    namespace_id: str = ""
    entity_id: str = ""
    metadata: dict | None = None


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


# Engine-backed payload classes, bound lazily when the cpex engine initializes.
# cpex's PluginManager requires every dispatched payload to be a
# ``PluginPayload`` instance (an isinstance check), but importing
# ``PluginPayload`` at module load would defeat the deferred-import goal. So the
# module-level payload classes stay plain frozen models (importable and
# unit-testable without cpex) and gain a cpex-backed twin here — via multiple
# inheritance, so the twin keeps every field/config AND satisfies
# ``isinstance(payload, PluginPayload)``.
_engine_payloads: dict[HookType, type] = {}


def bind_engine_payloads() -> None:
    """Bind the cpex-backed payload subclasses (idempotent; no-op without cpex).

    Called from ``register_evolve_hooks`` during engine initialization — the
    first point at which the heavy ``cpex.framework`` import is unavoidable.
    """
    if _engine_payloads or not HAS_CPEX:
        return
    from cpex.framework.models import PluginPayload

    for hook_type, base_cls in HOOK_PAYLOADS.items():
        _engine_payloads[hook_type] = type(
            base_cls.__name__,
            (PluginPayload, base_cls),
            {"__module__": __name__, "__doc__": base_cls.__doc__},
        )


def active_payload_cls(hook_type: HookType) -> type:
    """Payload class to construct at a dispatch site.

    Returns the cpex-backed subclass once the engine has initialized, else the
    plain frozen model. Dispatch only ever runs after ``initialize_hooks`` has
    bound the engine payloads, so the plain fallback is never handed to cpex.
    """
    return _engine_payloads.get(hook_type, HOOK_PAYLOADS[hook_type])


def register_evolve_hooks() -> None:
    """Register all altk_evolve hook types with the CPEX hook registry.

    Binds the cpex-backed payload classes and registers them. Idempotent:
    already-registered hook types are skipped. No-op when cpex is not installed.
    """
    if not HAS_CPEX:
        return
    from cpex.framework.hooks.registry import get_hook_registry
    from cpex.framework.models import PluginResult

    bind_engine_payloads()
    registry: Any = get_hook_registry()
    for hook_type in HOOK_PAYLOADS:
        if not registry.is_registered(hook_type.value):
            registry.register_hook(hook_type.value, _engine_payloads[hook_type], PluginResult)
