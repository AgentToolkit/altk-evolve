"""Thin execution-engine layer between the altk_evolve choke points and the
plugin runner — currently the CPEX ``PluginManager``.

This module is the only engine-specific layer of the hook seam: hook types,
payload classes, and plugin cores do not depend on it, so swapping engines
means reimplementing this dispatch layer, not the seam or the plugins.

Design of the CPEX integration (mirrors Mellea's plugin wrapper layer):

- Module-level singleton state (``_plugin_manager`` / ``_plugins_enabled``)
  with layered zero-overhead guards: a boolean check first, then
  ``has_hooks_for(hook_type)``, and only then payload construction.
- ``cpex`` is optional. When no plugins are configured every dispatch function
  returns its input untouched and cpex is never imported.
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

- Constructing a second ``EvolveClient`` whose config resolves plugins calls
  ``PluginManager.reset()`` and REPLACES the first client's plugins. For a
  compliance plugin (e.g. PII redaction) this means redaction can be disabled
  by unrelated code constructing its own client — ``initialize_hooks`` emits a
  loud ``logger.warning`` when a reset is about to discard already-registered
  plugins, but the last configured client still wins.
- A client that resolves NO plugins calls ``shutdown_hooks()`` so it does NOT
  inherit another client's process-global plugins: no configured plugins truly
  means a no-op.

Deferred cpex import: ``cpex.framework`` is a ~400ms import (it pulls
fastapi/mcp/prometheus), so it is imported lazily inside the functions that
need it rather than at module load. Importing this module — and any backend
that imports the hook seam — therefore stays cheap when hooks are disabled.
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

from altk_evolve.config.hooks import discover_hooks_config_path
from altk_evolve.hooks.types import (
    HAS_CPEX,
    HookType,
    active_payload_cls,
    register_evolve_hooks,
)
from altk_evolve.schema.exceptions import EvolveException

# NOTE: cpex.framework is NOT imported at module load — it is a ~400ms import
# that also pulls fastapi/mcp/prometheus. Every symbol from it is imported
# lazily inside the functions that need it, all of which are reached only after
# plugins are configured. This keeps ``import altk_evolve.backend.base`` (and
# every backend) cheap when no plugins are configured — the no-op path.

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

# Re-entrancy guard for the WHOLE write family (pre_write, pre_metadata_patch,
# pre_delete, pre_namespace_delete): a write-hook plugin that re-enters any
# write API (e.g. a memory_pre_write plugin calling backend.update_entity_metadata)
# must not re-dispatch a write hook and recurse infinitely. Propagated into the
# sync-bridge worker thread via the copy_context() path in _run_sync.
_in_write: contextvars.ContextVar[bool] = contextvars.ContextVar("altk_evolve_in_write", default=False)

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


# Raw cpex exceptions that can escape ``invoke_hook`` under on_error="fail":
# a plugin crash (PluginError), a timeout (PluginTimeoutError), or an explicit
# violation surfaced as an exception (PluginViolationError). Resolved lazily
# (the ``except`` clause evaluates the expression at raise time) so the module
# import never touches cpex.framework; empty tuple when cpex is absent, which
# makes the ``except ()`` clause inert on the no-op path.
_CPEX_PLUGIN_ERRORS_CACHE: tuple[type[BaseException], ...] | None = None


def _cpex_plugin_errors() -> tuple[type[BaseException], ...]:
    global _CPEX_PLUGIN_ERRORS_CACHE
    if _CPEX_PLUGIN_ERRORS_CACHE is None:
        if HAS_CPEX:
            from cpex.framework.errors import PluginError, PluginViolationError
            from cpex.framework.manager import PluginTimeoutError

            _CPEX_PLUGIN_ERRORS_CACHE = (PluginError, PluginTimeoutError, PluginViolationError)
        else:
            _CPEX_PLUGIN_ERRORS_CACHE = ()
    return _CPEX_PLUGIN_ERRORS_CACHE


def _describe_cpex_error(exc: BaseException) -> tuple[str, str, str]:
    """Extract (plugin_name, reason, code) from an escaping cpex plugin error."""
    if HAS_CPEX:
        from cpex.framework.errors import PluginError, PluginViolationError
        from cpex.framework.manager import PluginTimeoutError

        if isinstance(exc, PluginError):
            return (getattr(exc.error, "plugin_name", "") or "", exc.error.message, "PLUGIN_ERROR")
        if isinstance(exc, PluginViolationError):
            violation = exc.violation
            return (
                (violation.plugin_name or "") if violation else "",
                (violation.reason if violation else None) or exc.message or "Blocked by plugin",
                (violation.code or "") if violation else "",
            )
        if isinstance(exc, PluginTimeoutError):
            return ("", str(exc) or "Plugin timed out", "PLUGIN_TIMEOUT")
    return ("", str(exc) or "Plugin error", "PLUGIN_ERROR")


# ── lifecycle ────────────────────────────────────────────────────────


def initialize_hooks(config: HooksConfig) -> Any | None:
    """Initialize the CPEX PluginManager from a :class:`HooksConfig`.

    The hook seam is **always live** — there is no enable/disable switch.
    Behavior is decided entirely by which plugins resolve:

    - **No plugins resolved** (empty ``plugins_yaml`` + empty code-first
      ``plugins`` + nothing auto-discovered) → the seam stays a zero-cost no-op.
      ``cpex`` is NOT required and is not imported; any prior client's
      process-global plugins are torn down; returns ``None``.
    - **Plugins ARE configured but the engine isn't importable** → raises
      ``ImportError`` (``pip install 'altk-evolve[hooks]'``). Configured plugins
      must never silently no-op — that is the fail-open bug this guards against.
    - **A configured plugin's detector lib is missing** (e.g. READI without
      ``[pii-semantic]``, or the regex filter without ``[pii-regex]``) → the
      plugin's own ImportError is surfaced at init (fail-closed), not deferred
      to the first write.

    When ``plugins_yaml`` is unset it is auto-discovered via
    :func:`~altk_evolve.config.hooks.discover_hooks_config_path`. An explicit
    ``plugins_yaml`` or any code-first ``plugins`` overrides discovery.
    """
    yaml_path = config.plugins_yaml
    if not yaml_path and not config.plugins:
        yaml_path = discover_hooks_config_path()
    has_plugins = bool(yaml_path) or bool(config.plugins)

    if not has_plugins:
        # No plugins resolved: the seam is a no-op. Do NOT touch cpex (preserve
        # the deferred-import guarantee). Tear down any prior client's
        # process-global plugins so a "no plugins" client does not inherit them.
        shutdown_hooks()
        return None

    # Plugins ARE configured. Fail CLOSED if the engine is missing: never let a
    # configured compliance plugin silently degrade to a no-op.
    if not HAS_CPEX:
        raise ImportError(_CPEX_INSTALL_HINT)

    return _initialize_manager(yaml_path or "", config.plugins, config.plugin_timeout)


def _parse_plugins_yaml(yaml_path: str) -> list[Any]:
    """Parse a hooks ``plugins.yaml`` into ``HookPluginSpec``s ourselves.

    altk_evolve owns config parsing now — cpex is only the executor. We do NOT
    hand the YAML to cpex's loader (which would instantiate every ``kind`` as a
    cpex plugin and break native plugins). Uses ``yaml`` only, so this stays off
    the cpex import path. Unknown keys (e.g. ``description``) are dropped
    cleanly; ``$EVOLVE_HOOKS_CONFIG`` typos surface here as a clear read error.
    """
    from pathlib import Path

    import yaml

    from altk_evolve.config.hooks import HookPluginSpec

    data = yaml.safe_load(Path(yaml_path).read_text()) or {}
    entries = data.get("plugins", []) or []
    specs: list[Any] = []
    for entry in entries:
        specs.append(HookPluginSpec(**{k: entry[k] for k in HookPluginSpec.model_fields if k in entry}))
    return specs


def _initialize_manager(plugins_yaml: str, specs: list[Any], timeout: int) -> Any:
    """Build and install the process-global PluginManager (cpex required).

    Parses ``plugins_yaml`` into specs ourselves, concatenates the code-first
    ``specs``, then registers EVERY spec through :func:`_register_spec` — the
    single controlled instantiation point that adapts native plugins and
    registers raw cpex plugins directly. cpex loads NOTHING from YAML (we build
    ``PluginManager("")``). Finally eagerly validates every plugin's
    dependencies so a missing detector lib fails at startup rather than on the
    first write. Leaves the module guards OFF if anything raises.
    """
    global _plugin_manager, _plugins_enabled

    from cpex.framework.manager import PluginManager

    # altk_evolve owns YAML parsing; cpex only executes. YAML-derived specs run
    # first, then code-first specs (both flow through the same registration).
    all_specs = (_parse_plugins_yaml(plugins_yaml) if plugins_yaml else []) + list(specs)

    register_evolve_hooks()
    # The PluginManager is a process-global (Borg) singleton, so reset() here
    # DISCARDS any plugins an earlier client registered. Warn loudly when that
    # earlier setup had plugins — a silent wipe can drop a compliance plugin
    # (e.g. PII redaction) that unrelated code was relying on. We warn rather
    # than refuse, so legitimate re-initialization (e.g. between tests) works.
    if _plugin_manager is not None and getattr(_plugin_manager, "plugin_count", 0) > 0:
        logger.warning(
            "Re-initializing altk_evolve hooks: PluginManager.reset() will discard %d already-registered "
            "plugin(s) from a prior client (including any PII redaction / compliance plugins). The hook "
            "seam is process-global, not per-client — the last configured client's plugins win.",
            _plugin_manager.plugin_count,
        )
    PluginManager.reset()
    try:
        # Empty path: cpex loads no plugins from YAML — we register every spec
        # (native + raw cpex) ourselves via _register_spec.
        pm = PluginManager("", timeout=timeout)
        _run_sync(pm.initialize())
        for spec in all_specs:
            _register_spec(pm, spec)
        # Fail-closed at STARTUP: surface a configured plugin's missing detector
        # lib (its ImportError naming the extra) now, not lazily on first write.
        _validate_plugin_dependencies(pm)
    except Exception:
        # A failed init must leave the guards OFF (and the singleton clean) so
        # the seam does not half-activate.
        _plugin_manager = None
        _plugins_enabled = False
        PluginManager.reset()
        raise
    _plugin_manager = pm
    _plugins_enabled = True
    logger.info("altk_evolve hooks initialized (%d plugins).", pm.plugin_count)
    return pm


def _validate_plugin_dependencies(pm: Any) -> None:
    """Eagerly trip any configured plugin's missing-dependency ImportError.

    Plugin stubs raise a clear ``ImportError`` naming the extra from their
    constructor, so a plugin instantiated at all already validated itself. Some
    plugins (e.g. READI) instantiate cheaply and only import their heavy
    detector lib lazily; those expose ``startup_validate()`` which we call here
    so the extra-naming error surfaces at engine init, not on the first write.
    """
    for ref in pm._registry.get_all_plugins():
        plugin = getattr(ref, "plugin", None)
        validate = getattr(plugin, "startup_validate", None)
        if callable(validate):
            validate()


def shutdown_hooks() -> None:
    """Shut down the PluginManager and reset all module state."""
    global _plugin_manager, _plugins_enabled

    if _plugin_manager is not None:
        try:
            # Clear any runtime-disabled plugins so a transient on_error="disable"
            # trip does not wedge a plugin out for the whole process across a
            # shutdown/reinit cycle.
            _run_sync(_plugin_manager.executor.reset_runtime_disabled())
        except Exception:
            logger.warning("Error resetting runtime-disabled plugins.", exc_info=True)
        try:
            _run_sync(_plugin_manager.shutdown())
        except Exception:
            logger.warning("Error shutting down hook plugin manager.", exc_info=True)
        if HAS_CPEX:
            from cpex.framework.manager import PluginManager

            PluginManager.reset()
    _plugin_manager = None
    _plugins_enabled = False


def get_plugin_manager() -> Any | None:
    """Return the initialized PluginManager, or None when hooks are off."""
    return _plugin_manager


def hooks_active(hook_type: HookType) -> bool:
    """Fast guard: the engine is live AND at least one plugin subscribes to ``hook_type``."""
    if not _plugins_enabled or _plugin_manager is None:
        return False
    return bool(_plugin_manager.has_hooks_for(hook_type.value))


def _register_spec(pm: Any, spec: Any) -> None:
    """Instantiate and register ONE plugin spec — native or raw cpex.

    The single controlled instantiation point for both plugin flavors:

    - A cpex ``Plugin`` subclass (e.g. the regex ``pii`` plugin, which wraps
      ``cpex-pii-filter``) is instantiated with a synthesized ``PluginConfig``
      and registered directly — the raw cpex path, unchanged.
    - Anything else is a NATIVE plugin: instantiated with the plain
      ``spec.config`` dict (never a cpex ``PluginConfig``) and wrapped in a cpex
      ``Plugin`` adapter. A native plugin imports no cpex.

    Fail-closed: a native plugin whose missing-extra stub raises ``ImportError``
    from its constructor (e.g. ``pii`` without the extra, whose stub is a plain
    class → native path) trips here, at init.
    """
    from cpex.framework import Plugin as _CpexPlugin
    from cpex.framework.models import OnError, PluginConfig, PluginMode

    module_path, _, class_name = spec.kind.rpartition(".")
    plugin_cls = getattr(importlib.import_module(module_path), class_name)
    cpex_config = PluginConfig(
        name=spec.name,
        kind=spec.kind,
        hooks=list(spec.hooks),
        mode=PluginMode(spec.mode),
        priority=spec.priority,
        on_error=OnError(spec.on_error),
        config=dict(spec.config),
    )
    if isinstance(plugin_cls, type) and issubclass(plugin_cls, _CpexPlugin):
        pm._registry.register(plugin_cls(cpex_config))
        logger.debug("Registered raw cpex hook plugin: %s (%s)", spec.name, spec.kind)
    else:
        native = plugin_cls(spec.config or {})  # native: plain config dict; missing-extra stub raises here
        pm._registry.register(_build_native_adapter(native, cpex_config))
        logger.debug("Registered native hook plugin (adapted): %s (%s)", spec.name, spec.kind)


# Cache the adapter class so it is built once (on first native registration),
# not per spec. Built lazily to keep cpex.framework off the module import path.
_NATIVE_ADAPTER_CLS: Any | None = None


def _native_adapter_cls() -> Any:
    """Build (once) and return the cpex ``Plugin`` subclass that wraps a native plugin."""
    global _NATIVE_ADAPTER_CLS
    if _NATIVE_ADAPTER_CLS is not None:
        return _NATIVE_ADAPTER_CLS

    from cpex.framework import Plugin
    from cpex.framework.models import PluginResult

    from altk_evolve.hooks.plugin import HookContext
    from altk_evolve.hooks.types import HOOK_PAYLOADS, active_payload_cls

    class _NativePluginAdapter(Plugin):
        """cpex ``Plugin`` wrapping a native plugin.

        Exposes one async method per :class:`HookType`; cpex only wires those
        named in ``cpex_config.hooks``. Each method converts the cpex payload to
        the plain payload, calls the native (sync) method, and wraps the result.

        Invariants preserved:

        - **Fail-closed** — a native raise propagates through cpex ``on_error``
          and surfaces as ``MemoryPolicyViolation`` (unchanged).
        - **No in-place leak** — ``model_dump()``/``model_validate`` create fresh
          dicts for the native call; ``None`` → cpex falls back to the caller's
          original payload.
        - **Native never sees a cpex type** — plain payload in, plain payload
          out; ``HookContext`` is ours.
        - Deliberately defines no ``invoke_hook`` (else cpex treats it as an
          external/MCP plugin).
        """

        def __init__(self, native: Any, cpex_config: Any) -> None:
            super().__init__(cpex_config)
            self._native = native

        def startup_validate(self) -> None:
            # Forwarded so _validate_plugin_dependencies trips a native plugin's
            # missing-detector ImportError at init (fail-closed).
            validate = getattr(self._native, "startup_validate", None)
            if callable(validate):
                validate()

        async def _run(self, hook_type: HookType, cpex_payload: Any, context: Any) -> Any:
            method = getattr(self._native, hook_type.value, None)
            if method is None:
                return PluginResult(continue_processing=True)
            plain_cls: Any = HOOK_PAYLOADS[hook_type]
            # Read each declared field via getattr, NOT model_dump(): cpex wraps
            # mutable fields (entities/messages) in a CopyOnWriteList in some
            # execution modes (fire_and_forget), and model_dump() returns the
            # stale pydantic default ([]) for those rather than the live value.
            # model_validate then coerces into a fresh plain payload; a native
            # in-place mutation that isn't returned is still discarded (the
            # manager falls back to the caller's original on None).
            plain = plain_cls.model_validate({f: _to_plain(getattr(cpex_payload, f)) for f in plain_cls.model_fields})
            gc = getattr(context, "global_context", None)
            state = dict(getattr(gc, "state", None) or {})
            hook_ctx = HookContext(
                backend=state.get("backend"),
                state=state,
                request_id=getattr(gc, "request_id", "") if gc else "",
            )
            out = method(plain, hook_ctx)  # native, sync; raises to halt (propagates -> on_error)
            if out is None:
                return PluginResult(continue_processing=True)
            engine_cls: Any = active_payload_cls(hook_type)
            return PluginResult(continue_processing=True, modified_payload=engine_cls.model_validate(out.model_dump()))

        async def memory_pre_write(self, payload: Any, context: Any) -> Any:
            return await self._run(HookType.MEMORY_PRE_WRITE, payload, context)

        async def memory_pre_metadata_patch(self, payload: Any, context: Any) -> Any:
            return await self._run(HookType.MEMORY_PRE_METADATA_PATCH, payload, context)

        async def memory_pre_delete(self, payload: Any, context: Any) -> Any:
            return await self._run(HookType.MEMORY_PRE_DELETE, payload, context)

        async def memory_pre_namespace_delete(self, payload: Any, context: Any) -> Any:
            return await self._run(HookType.MEMORY_PRE_NAMESPACE_DELETE, payload, context)

        async def memory_post_read(self, payload: Any, context: Any) -> Any:
            return await self._run(HookType.MEMORY_POST_READ, payload, context)

        async def llm_pre_call(self, payload: Any, context: Any) -> Any:
            return await self._run(HookType.LLM_PRE_CALL, payload, context)

    _NATIVE_ADAPTER_CLS = _NativePluginAdapter
    return _NATIVE_ADAPTER_CLS


def _build_native_adapter(native: Any, cpex_config: Any) -> Any:
    """Wrap a native plugin in a cpex ``Plugin`` adapter instance."""
    return _native_adapter_cls()(native, cpex_config)


def _to_plain(value: Any) -> Any:
    """Recursively materialize cpex copy-on-write containers into plain Python.

    cpex wraps a payload's mutable fields (entities/messages, and nested
    dicts/lists) in copy-on-write ``list``/``dict`` subclasses whose backing
    storage is empty until iterated — so pydantic ``model_validate`` reads them
    as empty. Rebuilding via iteration forces materialization AND yields fresh
    dicts/lists, isolating the native call from the engine payload.
    """
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


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
    from cpex.framework.models import GlobalContext

    state: dict[str, Any] = {}
    if backend is not None:
        state["backend"] = backend
        state["backend_kind"] = type(backend).__name__
    global_context = GlobalContext(request_id=uuid.uuid4().hex, state=state)

    try:
        result = _run_sync(_ainvoke(hook_type, payload, global_context))
    except _cpex_plugin_errors() as exc:
        # With on_error="fail" (the fail-closed default) cpex raises a raw
        # PluginError / PluginTimeoutError / PluginViolationError that isn't
        # part of this seam's contract. Re-raise as MemoryPolicyViolation so
        # the documented "halting raises MemoryPolicyViolation" contract holds
        # for crashes/timeouts too — a compliance plugin that dies must fail
        # closed, not pass data through. The original is preserved as __cause__.
        plugin_name, reason, code = _describe_cpex_error(exc)
        raise MemoryPolicyViolation(
            hook_type=hook_type.value,
            reason=reason,
            code=code,
            plugin_name=plugin_name,
        ) from exc

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
    if _in_write.get():
        return entities
    from altk_evolve.schema.core import Entity as EntityCls

    token = _in_write.set(True)
    try:
        payload = active_payload_cls(HookType.MEMORY_PRE_WRITE)(
            namespace_id=namespace_id,
            entities=copy.deepcopy([e.model_dump() for e in entities]),
            backend_kind=type(backend).__name__,
        )
        modified = _invoke(HookType.MEMORY_PRE_WRITE, payload, backend=backend)
    finally:
        _in_write.reset(token)
    if modified is None:
        return entities
    return [EntityCls.model_validate(d) for d in modified.entities]


def dispatch_memory_pre_metadata_patch(backend: BaseEntityBackend, namespace_id: str, entity_id: str, metadata_patch: dict) -> dict:
    """Fire memory_pre_metadata_patch; return the (possibly transformed) patch."""
    if not hooks_active(HookType.MEMORY_PRE_METADATA_PATCH):
        return metadata_patch
    if _in_write.get():
        return metadata_patch
    token = _in_write.set(True)
    try:
        payload = active_payload_cls(HookType.MEMORY_PRE_METADATA_PATCH)(
            namespace_id=namespace_id,
            entity_id=entity_id,
            metadata_patch=copy.deepcopy(metadata_patch),
            backend_kind=type(backend).__name__,
        )
        modified = _invoke(HookType.MEMORY_PRE_METADATA_PATCH, payload, backend=backend)
    finally:
        _in_write.reset(token)
    if modified is None:
        return metadata_patch
    return dict(modified.metadata_patch)


def dispatch_memory_pre_delete(backend: BaseEntityBackend, namespace_id: str, entity_id: str, metadata: dict | None = None) -> None:
    """Fire memory_pre_delete (halting only — no payload transform applies).

    ``metadata`` is the stored entity's metadata when the caller could resolve
    it (``None`` otherwise) so policy plugins can key on fields like
    ``legal_hold``.
    """
    if not hooks_active(HookType.MEMORY_PRE_DELETE):
        return
    if _in_write.get():
        return
    token = _in_write.set(True)
    try:
        payload = active_payload_cls(HookType.MEMORY_PRE_DELETE)(
            namespace_id=namespace_id,
            entity_id=entity_id,
            metadata=copy.deepcopy(metadata),
            backend_kind=type(backend).__name__,
        )
        _invoke(HookType.MEMORY_PRE_DELETE, payload, backend=backend)
    finally:
        _in_write.reset(token)


def dispatch_memory_pre_namespace_delete(backend: BaseEntityBackend, namespace_id: str) -> None:
    """Fire memory_pre_namespace_delete (halting only)."""
    if not hooks_active(HookType.MEMORY_PRE_NAMESPACE_DELETE):
        return
    if _in_write.get():
        return
    token = _in_write.set(True)
    try:
        payload = active_payload_cls(HookType.MEMORY_PRE_NAMESPACE_DELETE)(namespace_id=namespace_id, backend_kind=type(backend).__name__)
        _invoke(HookType.MEMORY_PRE_NAMESPACE_DELETE, payload, backend=backend)
    finally:
        _in_write.reset(token)


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
        payload = active_payload_cls(HookType.MEMORY_POST_READ)(
            namespace_id=namespace_id,
            entities=copy.deepcopy([e.model_dump(mode="json") for e in entities]),
            query=query,
            filters=filters or {},
            backend_kind=type(backend).__name__,
        )
        try:
            modified = _invoke(HookType.MEMORY_POST_READ, payload, backend=backend)
        except MemoryPolicyViolation:
            # post_read is a read-side transform; a plugin crash/halt here (e.g.
            # the fire-and-forget access stamp) must NOT fail the read it rode
            # in on. Only the write family and llm_pre_call surface violations.
            logger.warning("A memory_post_read plugin failed; returning untransformed results.", exc_info=True)
            return entities
        if modified is None:
            return entities
        return [RecordedEntityCls.model_validate(d) for d in modified.entities]
    finally:
        _in_post_read.reset(token)


def dispatch_llm_pre_call(messages: list[dict], purpose: str, model: str | None = None) -> list[dict]:
    """Fire llm_pre_call just before a litellm completion; return the (possibly redacted) messages."""
    if not hooks_active(HookType.LLM_PRE_CALL):
        return messages
    payload = active_payload_cls(HookType.LLM_PRE_CALL)(messages=copy.deepcopy(messages), purpose=purpose, model=model)
    modified = _invoke(HookType.LLM_PRE_CALL, payload)
    if modified is None:
        return messages
    return list(modified.messages)
