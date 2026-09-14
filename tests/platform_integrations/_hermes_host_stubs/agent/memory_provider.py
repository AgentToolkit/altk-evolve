"""Stub of hermes-agent's agent/memory_provider.py.

Deliberately NOT an ABC: instantiating the real provider must not require
implementing members these tests do not exercise. The real base class defines
the recall/capture hooks Hermes calls; the provider overrides the ones it uses,
and the tests call those overrides directly.
"""


class MemoryProvider:
    pass
