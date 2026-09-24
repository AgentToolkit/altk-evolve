"""Minimal stand-ins for the hermes-agent modules the provider hard-imports.

The provider needs exactly two host symbols at module scope:
``agent.memory_provider.MemoryProvider`` and ``tools.registry.tool_error``.
Everything else it touches is lazy or guarded by try/except — including
``agent.memory_manager.sanitize_context``, which is stubbed anyway so that the
tests can distinguish a live sanitizer from the silent pass-through fallback.
Together these let the bundle be imported and unit-tested outside a Hermes
process.

Underscore-prefixed so pytest does not collect it (cf. tests/unit/_ext_native_plugin.py).
This directory is placed on ``sys.path`` for the duration of an import only —
see ``load_module`` in _hermes_loader.py.
"""
