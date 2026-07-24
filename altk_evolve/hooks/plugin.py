"""Engine-agnostic hook plugin contract — cpex is one executor behind an adapter.

A hook plugin is **not** required to be a cpex ``Plugin``. It is any object that
implements one sync method per :class:`~altk_evolve.hooks.types.HookType` it
serves, over the plain frozen payloads in :mod:`altk_evolve.hooks.types`. This
module imports **no** ``cpex`` — a third party (and every in-tree plugin) can
write, import, and unit-test a plugin with only ``altk_evolve.hooks.plugin`` and
``altk_evolve.hooks.types`` on the path. The execution engine (CPEX today) is
hidden behind an adapter built in :mod:`altk_evolve.hooks.manager`; swapping
engines never touches a plugin.

Contract:

- **One method per hook, named EXACTLY for the ``HookType`` value** it serves:
  ``memory_pre_write``, ``memory_pre_metadata_patch``, ``memory_pre_delete``,
  ``memory_pre_namespace_delete``, ``memory_post_read``, ``llm_pre_call``.
  Implement only the hooks you serve.
- Each method is **sync** (the engine's async detail is hidden) and takes
  ``(self, payload, context)`` where ``payload`` is the plain payload for that
  hook and ``context`` is a :class:`HookContext`.
- ``return None`` → unchanged. ``return payload.replace(...)`` → the returned
  payload replaces the input. **Raising halts the operation (fail-closed):** on
  a write / ``llm_pre_call`` hook it surfaces as
  :class:`~altk_evolve.hooks.manager.MemoryPolicyViolation`, and nothing is
  stored or sent.
- Never mutate the payload (or its nested dicts/lists) in place — that is
  discarded and can leak across the plugin chain. Propose changes via
  ``payload.replace(...)`` only.

``mode`` / ``on_error`` / ``priority`` are engine-level knobs carried on the
:class:`~altk_evolve.config.hooks.HookPluginSpec` (or the YAML entry); a plugin
never reads them. The per-plugin ``config`` a plugin receives is the plain
``spec.config`` dict, never a cpex ``PluginConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class HookContext:
    """Engine-agnostic execution context handed to a native plugin.

    - ``backend`` — the live entity backend for plugins that call back into the
      store (e.g. access stamping); ``None`` for hooks with no backend (LLM
      egress) or when unavailable.
    - ``state`` — the raw context state dict (``backend``, ``backend_kind``, …).
    - ``request_id`` — the per-invocation id, for correl/logging.
    """

    backend: Any = None
    state: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""


@runtime_checkable
class HookPlugin(Protocol):
    """Structural contract for a native hook plugin.

    A plugin implements ONLY the hooks it serves — each method named for the
    corresponding :class:`~altk_evolve.hooks.types.HookType` value, sync, taking
    ``(payload, context)`` and returning the (possibly replaced) payload or
    ``None``. Because this is a ``runtime_checkable`` ``Protocol`` a plugin need
    not subclass anything; :class:`HookPluginBase` is an optional convenience.
    """

    def memory_pre_write(self, payload: Any, context: HookContext) -> Any | None: ...

    def memory_pre_metadata_patch(self, payload: Any, context: HookContext) -> Any | None: ...

    def memory_pre_delete(self, payload: Any, context: HookContext) -> Any | None: ...

    def memory_pre_namespace_delete(self, payload: Any, context: HookContext) -> Any | None: ...

    def memory_post_read(self, payload: Any, context: HookContext) -> Any | None: ...

    def llm_pre_call(self, payload: Any, context: HookContext) -> Any | None: ...


class HookPluginBase:
    """Optional base for native plugins: config storage + no-op hook defaults.

    Subclasses override only the hooks they serve; unoverridden hooks are
    no-ops (return ``None`` = unchanged). Not a cpex type — importing this
    pulls no ``cpex``.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})

    def startup_validate(self) -> None:
        """Called once at engine init so a plugin can fail closed early (e.g.
        surface a missing detector lib). No-op by default."""

    def memory_pre_write(self, payload: Any, context: HookContext) -> Any | None:
        return None

    def memory_pre_metadata_patch(self, payload: Any, context: HookContext) -> Any | None:
        return None

    def memory_pre_delete(self, payload: Any, context: HookContext) -> Any | None:
        return None

    def memory_pre_namespace_delete(self, payload: Any, context: HookContext) -> Any | None:
        return None

    def memory_post_read(self, payload: Any, context: HookContext) -> Any | None:
        return None

    def llm_pre_call(self, payload: Any, context: HookContext) -> Any | None:
        return None


__all__ = ["HookContext", "HookPlugin", "HookPluginBase"]
