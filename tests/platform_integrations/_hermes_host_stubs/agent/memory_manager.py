"""Stub of hermes-agent's ``agent.memory_manager.sanitize_context``.

Both the provider and the trajectory adapter import this symbol behind a
``try/except`` that falls back to a pass-through, so without a stub the tests
could not tell "wired to the host sanitizer" apart from "silently degraded".
The stub therefore does two things:

- strips ``<memory-context>`` blocks and stray fence tags, mirroring the real
  regexes (hermes-agent ``agent/memory_manager.py:202-218``). The real function
  also strips an injected ``[System note: ...]`` line, which nothing in this
  bundle ever produces, so it is left out.
- records every call in ``CALLS`` so a test can prove the call site survives.

Deliberately not a prefix/marker transformation: the provider sanitizes the
*whole* formatted recall block, so a prefix would land in front of the recall
header that the provider's own output contract pins.
"""

import re

_INTERNAL_CONTEXT_RE = re.compile(r"<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>", re.IGNORECASE)
_FENCE_TAG_RE = re.compile(r"</?\s*memory-context\s*>", re.IGNORECASE)

#: Every text passed to ``sanitize_context``, oldest first. Tests clear it.
CALLS = []


def sanitize_context(text: str) -> str:
    CALLS.append(text)
    return _FENCE_TAG_RE.sub("", _INTERNAL_CONTEXT_RE.sub("", text))
