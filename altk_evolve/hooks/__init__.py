"""General-purpose pluggable memory hook seam for altk_evolve.

The seam (hook types, frozen payloads, dispatch points, veto semantics) and
plugin cores are engine-agnostic; plugin execution is provided by an engine
integration — currently the optional ``cpex`` package — behind the thin
dispatch layer in :mod:`altk_evolve.hooks.manager`.

Public surface:

- :class:`~altk_evolve.hooks.types.HookType` and the frozen payload classes
- :func:`~altk_evolve.hooks.manager.initialize_hooks` /
  :func:`~altk_evolve.hooks.manager.shutdown_hooks`
- :class:`~altk_evolve.hooks.manager.MemoryPolicyViolation`
- ``dispatch_*`` helpers used at the backend / LLM choke points
- :func:`~altk_evolve.hooks.types.engine_available` to probe whether a plugin
  execution engine is installed

Everything is a fast no-op unless ``EvolveConfig.hooks.enabled`` is True and
the execution engine is installed (``pip install 'altk-evolve[hooks]'``).
"""

from altk_evolve.hooks.manager import (
    MemoryPolicyViolation,
    dispatch_llm_pre_call,
    dispatch_memory_post_read,
    dispatch_memory_pre_delete,
    dispatch_memory_pre_metadata_patch,
    dispatch_memory_pre_namespace_delete,
    dispatch_memory_pre_write,
    get_plugin_manager,
    hooks_active,
    initialize_hooks,
    shutdown_hooks,
)
from altk_evolve.hooks.types import (
    HookType,
    LLMPreCallPayload,
    MemoryPostReadPayload,
    MemoryPreDeletePayload,
    MemoryPreMetadataPatchPayload,
    MemoryPreNamespaceDeletePayload,
    MemoryPreWritePayload,
    engine_available,
    register_evolve_hooks,
)

__all__ = [
    "HookType",
    "LLMPreCallPayload",
    "MemoryPolicyViolation",
    "MemoryPostReadPayload",
    "MemoryPreDeletePayload",
    "MemoryPreMetadataPatchPayload",
    "MemoryPreNamespaceDeletePayload",
    "MemoryPreWritePayload",
    "dispatch_llm_pre_call",
    "dispatch_memory_post_read",
    "dispatch_memory_pre_delete",
    "dispatch_memory_pre_metadata_patch",
    "dispatch_memory_pre_namespace_delete",
    "dispatch_memory_pre_write",
    "engine_available",
    "get_plugin_manager",
    "hooks_active",
    "initialize_hooks",
    "register_evolve_hooks",
    "shutdown_hooks",
]
