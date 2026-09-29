"""Stub of hermes-agent's ``tools.threat_patterns.scan_for_threats``.

The provider imports this symbol behind a ``try/except`` that falls back to a
no-op returning ``[]``, so without a stub a test could not tell "wired to the
host scanner" apart from "silently screening nothing" — the failure mode that
matters, since a dead screen looks exactly like a clean store.

Three of the real patterns are reproduced, one per scope tier, so a test can
pin that the scope argument reaches the scanner and that ``strict`` is broader
than ``context`` (hermes-agent ``tools/threat_patterns.py``, ``a792d0794f``):
the real set is ~60 patterns plus invisible-unicode detection, none of which
this bundle's behaviour depends on. Pattern ids match the real ones so an
assertion here would still read true against the host.

``CALLS`` records ``(content, scope)`` per call, oldest first. Tests clear it.
"""

import re
from typing import List, Optional, Tuple

# (compiled pattern, id, narrowest scope that includes it)
_PATTERNS = (
    (re.compile(r"ignore\s+(previous|all|above|prior)\s+instructions", re.IGNORECASE), "prompt_injection", "all"),
    (re.compile(r"pretend\s+(you\s+are|to\s+be)\s+", re.IGNORECASE), "role_pretend", "context"),
    (re.compile(r"authorized_keys"), "ssh_backdoor", "strict"),
)

# A pattern lands in its own tier and every broader one, as upstream's
# ``_compile`` does: "all" is in all three sets, "strict" in strict only.
_TIERS = {"all": ("all",), "context": ("all", "context"), "strict": ("all", "context", "strict")}

#: Every ``(content, scope)`` passed to ``scan_for_threats``, oldest first.
CALLS: List[Tuple[str, str]] = []


def scan_for_threats(content: str, scope: str = "context") -> List[str]:
    CALLS.append((content, scope))
    if not content:
        return []
    if scope not in _TIERS:
        raise ValueError(f"scan_for_threats: unknown scope {scope!r}")
    active = _TIERS[scope]
    return [pid for pattern, pid, tier in _PATTERNS if tier in active and pattern.search(content)]


def first_threat_message(content: str, scope: str = "strict") -> Optional[str]:
    findings = scan_for_threats(content, scope)
    return f"threat pattern {findings[0]} detected" if findings else None
