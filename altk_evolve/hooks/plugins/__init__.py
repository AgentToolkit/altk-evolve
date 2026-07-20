"""In-tree hook plugins shipped with altk_evolve.

Each plugin follows a core/shim split: the domain logic is a pure, engine-free
function at the top of its module (importable and tested without any extra),
and the cpex ``Plugin`` subclass is a thin shim that adapts it to the shipped
CPEX execution engine. Constructing a shim class without the optional ``cpex`` package
(``pip install 'altk-evolve[hooks]'``) raises ImportError; importing this
package — and using the cores — always works.

- :class:`MetadataNormalizerPlugin` (memory_pre_write, transform): stamps
  canonical metadata (``trace_id``, ``created_at``); core
  :func:`normalize_entities`.
- :class:`AccessStampPlugin` (memory_post_read, fire_and_forget): stamps
  ``last_accessed`` on read entities; core :func:`build_access_stamps`.
- :class:`PIIFilterMemoryPlugin` (memory_pre_write + llm_pre_call, transform):
  regex PII redaction; additionally requires ``pip install 'altk-evolve[pii]'``.
  Deliberately core-less: it is an adapter for the external cpex-pii-filter
  plugin, so the cpex coupling is its purpose.
- :class:`ReadiSemanticPIIPlugin` (memory_pre_write + llm_pre_call, sequential):
  semantic (NER) PII redaction via IBM READI — catches names/locations/orgs
  that regex cannot; cores :func:`redact_entities` / :func:`redact_messages` /
  :func:`redact_spans`. Additionally requires ``pip install 'altk-evolve[readi]'``.
"""

from altk_evolve.hooks.plugins.access_stamp import AccessStampPlugin, build_access_stamps
from altk_evolve.hooks.plugins.normalizer import MetadataNormalizerPlugin, normalize_entities
from altk_evolve.hooks.plugins.pii import PIIFilterMemoryPlugin
from altk_evolve.hooks.plugins.readi import (
    ReadiSemanticPIIPlugin,
    build_readi_detector,
    redact_entities,
    redact_messages,
    redact_spans,
    redact_text,
)

__all__ = [
    "AccessStampPlugin",
    "MetadataNormalizerPlugin",
    "PIIFilterMemoryPlugin",
    "ReadiSemanticPIIPlugin",
    "build_access_stamps",
    "build_readi_detector",
    "normalize_entities",
    "redact_entities",
    "redact_messages",
    "redact_spans",
    "redact_text",
]
