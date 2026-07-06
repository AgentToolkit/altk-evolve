"""Thin invoke layer between altk_evolve choke points and the CPEX PluginManager.

Design (mirrors Mellea's plugin wrapper layer):

- Module-level singleton state (``_plugin_manager`` / ``_plugins_enabled``)
  with layered zero-overhead guards: a boolean check first, then
  ``has_hooks_for(hook_type)``, and only then payload construction.
- ``cpex`` is optional. Without it (or with ``hooks.enabled=False``, the
  default) every dispatch function returns its input untouched.
- Payload contents are deep-copied at dispatch: pydantic ``frozen=True`` only
  guards attribute assignment, so a plugin could otherwise mutate the shared
  entity dicts / message dicts in place. Plugins receive copies, and changes
  flow back to the store only through ``PluginResult.modified_payload`` —
  in-place mutation of a payload is discarded.
- CPEX's ``invoke_hook`` is async-only; our call sites are sync. The bridge
  uses ``asyncio.run`` when no loop is running and a dedicated thread when one
  is (the Mellea pattern).
- Fire-and-forget plugin tasks are awaited before the bridge returns:
  at a sync seam the event loop closes immediately after ``invoke_hook``, so
  detached tasks would be cancelled and their side effects silently lost.
- A halting plugin (``continue_processing=False``) raises
  :class:`MemoryPolicyViolation` — writes are never silently dropped.

Singleton caveat — the seam is process-global, not per-client:

- Constructing a second ``EvolveClient`` with ``hooks.enabled=True`` calls
  ``PluginManager.reset()`` and silently REPLACES the first client's plugins.
  For a compliance plugin (e.g. PII redaction) this means redaction can be
  silently disabled by unrelated code constructing its own client.
- A client constructed with ``hooks.enabled=False`` does not reset the
  manager, but it still inherits whatever process-global hooks another client
  enabled: its writes and reads flow through those plugins too.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import copy
import importlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

from altk_evolve.hooks.types import (
    HAS_CPEX,
    HookType,
    LLMPreCallPayload,
    MemoryPostReadPayload,
    MemoryPreDeletePayload,
    MemoryPreMetadataPatchPayload,
    MemoryPreNamespaceDeletePayload,
    MemoryPreWritePayload,
    register_evolve_hooks,
)
from altk_evolve.schema.exceptions import EvolveException

if HAS_CPEX:
    from cpex.framework.manager import PluginManager
    from cpex.framework.models import GlobalContext, OnError, PluginConfig, PluginMode

if TYPE_CHECKING:
    from altk_evolve.backend.base import BaseEntityBackend
    from altk_evolve.config.hooks import HooksConfig
    from altk_evolve.schema.core import Entity, RecordedEntity

logger = logging.getLogger(__name__)

# Module-level singleton state.
_plugin_manager: Any | None = None
_plugins_enabled: bool = False

# Re-entrancy guard: a memory_post_read plugin that triggers another public
# read in the same context must not re-fire memory_post_read.
_in_post_read: contextvars.ContextVar[bool] = contextvars.ContextVar("altk_evolve_in_post_read", default=False)

_CPEX_INSTALL_HINT = "Hooks require the CPEX plugin framework. Install it with: pip install 'altk-evolve[hooks]'"


class MemoryPolicyViolation(EvolveException):
    """Raised when a plugin halts a memory operation or LLM call."""

    def __init__(self, hook_type: str, reason: str, code: str = "", plugin_name: str = ""):
        self.hook_type = hook_type
        self.reason = reason
        self.code = code
        self.plugin_name = plugin_name
        detail = f"[{code}] " if code else ""
        super().__init__(f"Plugin blocked {hook_type}: {detail}{reason}")


# ── lifecycle ────────────────────────────────────────────────────────


def initialize_hooks(config: HooksConfig) -> Any | None:
    """Initialize the CPEX PluginManager from a :class:`HooksConfig`.

    Loads ``plugins_yaml`` (when set) through CPEX's own YAML loader and then
    registers any code-first ``plugins`` specs programmatically. Returns the
    manager, or ``None`` when ``config.enabled`` is False.

    Raises ImportError when hooks were explicitly enabled but cpex is missing:
    misconfiguration must not silently disable a compliance plugin.
    """
    global _plugin_manager, _plugins_enabled

    if not config.enabled:
        return None
    if not HAS_CPEX:
        raise ImportError(_CPEX_INSTALL_HINT)

    register_evolve_hooks()
    PluginManager.reset()
    pm = PluginManager(config.plugins_yaml or "", timeout=config.plugin_timeout)
    _run_sync(pm.initialize())
    for spec in config.plugins:
        _register_spec(pm, spec)
    _plugin_manager = pm
    _plugins_enabled = True
    logger.info("altk_evolve hooks initialized (%d plugins).", pm.plugin_count)
    return pm


def shutdown_hooks() -> None:
    """Shut down the PluginManager and reset all module state."""
    global _plugin_manager, _plugins_enabled

    if _plugin_manager is not None:
        try:
            _run_sync(_plugin_manager.shutdown())
        except Exception:
            logger.warning("Error shutting down hook plugin manager.", exc_info=True)
        if HAS_CPEX:
            PluginManager.reset()
    _plugin_manager = None
    _plugins_enabled = False


def get_plugin_manager() -> Any | None:
    """Return the initialized PluginManager, or None when hooks are off."""
    return _plugin_manager


def hooks_active(hook_type: HookType) -> bool:
    """Fast guard: hooks enabled AND at least one plugin subscribes to ``hook_type``."""
    if not _plugins_enabled or _plugin_manager is None:
        return False
    return bool(_plugin_manager.has_hooks_for(hook_type.value))


def _register_spec(pm: Any, spec: Any) -> None:
    """Instantiate and register one code-first plugin spec (PluginConfig synthesis)."""
    module_path, _, class_name = spec.kind.rpartition(".")
    plugin_cls = getattr(importlib.import_module(module_path), class_name)
    plugin_config = PluginConfig(
        name=spec.name,
        kind=spec.kind,
        hooks=list(spec.hooks),
        mode=PluginMode(spec.mode),
        priority=spec.priority,
        on_error=OnError(spec.on_error),
        config=dict(spec.config),
    )
    pm._registry.register(plugin_cls(plugin_config))
    logger.debug("Registered code-first hook plugin: %s (%s)", spec.name, spec.kind)


# ── sync bridge ──────────────────────────────────────────────────────


def _run_sync(coro: Any) -> Any:
    """Run an async coroutine from sync code.

    Uses ``asyncio.run`` when no event loop is running in this thread;
    otherwise runs the coroutine in a dedicated thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Propagate contextvars into the worker thread so re-entrancy guards
    # (e.g. the memory_post_read guard) survive the bridge.
    ctx = contextvars.copy_context()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(ctx.run, asyncio.run, coro).result()


async def _ainvoke(hook_type: HookType, payload: Any, global_context: Any) -> Any:
    assert _plugin_manager is not None  # guarded by hooks_active() at every dispatch site
    result, _ = await _plugin_manager.invoke_hook(
        hook_type=hook_type.value,
        payload=payload,
        global_context=global_context,
    )
    # Await fire-and-forget tasks: this loop closes when the sync bridge
    # returns, and pending tasks would be cancelled with it.
    if result is not None and result.background_tasks:
        await asyncio.gather(*result.background_tasks, return_exceptions=True)
    return result


def _invoke(hook_type: HookType, payload: Any, backend: BaseEntityBackend | None = None) -> Any:
    """Invoke a hook synchronously; return the final ``modified_payload``,
    or ``None`` when no plugin returned one.

    Returning ``None`` (rather than the payload itself) is the immutability
    enforcement point: a plugin that mutated the payload's contents in place
    without returning a ``modified_payload`` has its mutation discarded —
    dispatch helpers fall back to the caller's original, untouched input.

    Raises :class:`MemoryPolicyViolation` when a plugin halts the pipeline.
    """
    state: dict[str, Any] = {}
    if backend is not None:
        state["backend"] = backend
        state["backend_kind"] = type(backend).__name__
    global_context = GlobalContext(request_id=uuid.uuid4().hex, state=state)

    result = _run_sync(_ainvoke(hook_type, payload, global_context))

    if result is not None and not result.continue_processing:
        violation = result.violation
        raise MemoryPolicyViolation(
            hook_type=hook_type.value,
            reason=violation.reason if violation else "Blocked by plugin",
            code=(violation.code or "") if violation else "",
            plugin_name=(violation.plugin_name or "") if violation else "",
        )
    if result is not None and result.modified_payload is not None:
        return result.modified_payload
    return None


# ── dispatch helpers (one per hook type) ─────────────────────────────
#
# Each helper takes/returns domain objects so call sites stay clean, and
# constructs the payload only after the zero-overhead guards pass.
#
# Immutability: mutable payload contents (entity dicts, metadata patches,
# message lists) are deep-copied at payload construction — after the guards,
# so the disabled/no-subscriber fast path never pays for a copy. Plugins
# therefore cannot reach the store (or the caller's objects) by mutating a
# payload in place; changes flow back only via ``modified_payload``.


def dispatch_memory_pre_write(backend: BaseEntityBackend, namespace_id: str, entities: list[Entity]) -> list[Entity]:
    """Fire memory_pre_write; return the (possibly transformed) entity batch."""
    if not hooks_active(HookType.MEMORY_PRE_WRITE) or not entities:
        return entities
    from altk_evolve.schema.core import Entity as EntityCls

    payload = MemoryPreWritePayload(
        namespace_id=namespace_id,
        entities=copy.deepcopy([e.model_dump() for e in entities]),
        backend_kind=type(backend).__name__,
    )
    modified = _invoke(HookType.MEMORY_PRE_WRITE, payload, backend=backend)
    if modified is None:
        return entities
    return [EntityCls.model_validate(d) for d in modified.entities]


def dispatch_memory_pre_metadata_patch(backend: BaseEntityBackend, namespace_id: str, entity_id: str, metadata_patch: dict) -> dict:
    """Fire memory_pre_metadata_patch; return the (possibly transformed) patch."""
    if not hooks_active(HookType.MEMORY_PRE_METADATA_PATCH):
        return metadata_patch
    payload = MemoryPreMetadataPatchPayload(
        namespace_id=namespace_id,
        entity_id=entity_id,
        metadata_patch=copy.deepcopy(metadata_patch),
        backend_kind=type(backend).__name__,
    )
    modified = _invoke(HookType.MEMORY_PRE_METADATA_PATCH, payload, backend=backend)
    if modified is None:
        return metadata_patch
    return dict(modified.metadata_patch)


def dispatch_memory_pre_delete(backend: BaseEntityBackend, namespace_id: str, entity_id: str) -> None:
    """Fire memory_pre_delete (halting only — no payload transform applies)."""
    if not hooks_active(HookType.MEMORY_PRE_DELETE):
        return
    payload = MemoryPreDeletePayload(namespace_id=namespace_id, entity_id=entity_id, backend_kind=type(backend).__name__)
    _invoke(HookType.MEMORY_PRE_DELETE, payload, backend=backend)


def dispatch_memory_pre_namespace_delete(backend: BaseEntityBackend, namespace_id: str) -> None:
    """Fire memory_pre_namespace_delete (halting only)."""
    if not hooks_active(HookType.MEMORY_PRE_NAMESPACE_DELETE):
        return
    payload = MemoryPreNamespaceDeletePayload(namespace_id=namespace_id, backend_kind=type(backend).__name__)
    _invoke(HookType.MEMORY_PRE_NAMESPACE_DELETE, payload, backend=backend)


def dispatch_memory_post_read(
    backend: BaseEntityBackend,
    namespace_id: str,
    entities: list[RecordedEntity],
    query: str | None = None,
    filters: dict | None = None,
) -> list[RecordedEntity]:
    """Fire memory_post_read on public search results; return the (possibly filtered) list.

    Re-entrancy safe: reads triggered from inside a memory_post_read plugin
    (in the same context) do not re-fire the hook.
    """
    if not hooks_active(HookType.MEMORY_POST_READ) or not entities:
        return entities
    if _in_post_read.get():
        return entities
    from altk_evolve.schema.core import RecordedEntity as RecordedEntityCls

    token = _in_post_read.set(True)
    try:
        payload = MemoryPostReadPayload(
            namespace_id=namespace_id,
            entities=copy.deepcopy([e.model_dump(mode="json") for e in entities]),
            query=query,
            filters=filters or {},
            backend_kind=type(backend).__name__,
        )
        modified = _invoke(HookType.MEMORY_POST_READ, payload, backend=backend)
        if modified is None:
            return entities
        return [RecordedEntityCls.model_validate(d) for d in modified.entities]
    finally:
        _in_post_read.reset(token)


def dispatch_llm_pre_call(messages: list[dict], purpose: str, model: str | None = None) -> list[dict]:
    """Fire llm_pre_call just before a litellm completion; return the (possibly redacted) messages."""
    if not hooks_active(HookType.LLM_PRE_CALL):
        return messages
    payload = LLMPreCallPayload(messages=copy.deepcopy(messages), purpose=purpose, model=model)
    modified = _invoke(HookType.LLM_PRE_CALL, payload)
    if modified is None:
        return messages
    return list(modified.messages)
