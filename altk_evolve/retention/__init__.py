"""Data retention for altk-evolve (issue #275).

Age- and disuse-based retention of entities, plus session (trajectory)
retention with provenance-based cascade deletion of the memories derived from
a session. See ``docs/guides/retention.md``.
"""

from altk_evolve.retention.engine import (
    NO_ACCESS_SIGNAL_HINT,
    RetentionEngine,
    RetentionItem,
    RetentionReport,
)
from altk_evolve.retention.policy import RetentionPolicy, RetentionRule

__all__ = [
    "NO_ACCESS_SIGNAL_HINT",
    "RetentionEngine",
    "RetentionItem",
    "RetentionPolicy",
    "RetentionReport",
    "RetentionRule",
]
