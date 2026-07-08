"""In-tree hook plugins shipped with altk_evolve.

Each plugin follows a core/shim split: the domain logic is a pure, cpex-free
function at the top of its module (importable and tested without any extra),
and the cpex ``Plugin`` subclass is a thin shim that adapts it to the hook
seam. Constructing a shim class without the optional ``cpex`` package
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
"""

from altk_evolve.hooks.plugins.access_stamp import AccessStampPlugin, build_access_stamps
from altk_evolve.hooks.plugins.normalizer import MetadataNormalizerPlugin, normalize_entities
from altk_evolve.hooks.plugins.pii import PIIFilterMemoryPlugin

__all__ = [
    "AccessStampPlugin",
    "MetadataNormalizerPlugin",
    "PIIFilterMemoryPlugin",
    "build_access_stamps",
    "normalize_entities",
]
