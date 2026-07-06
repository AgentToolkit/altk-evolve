"""In-tree hook plugins shipped with altk_evolve.

All plugins here require the optional ``cpex`` package
(``pip install 'altk-evolve[hooks]'``); importing this package without cpex
yields stub classes that raise ImportError on construction.

- :class:`MetadataNormalizerPlugin` (memory_pre_write, transform): stamps
  canonical metadata (``trace_id``, ``created_at``).
- :class:`AccessStampPlugin` (memory_post_read, fire_and_forget): stamps
  ``last_accessed`` on read entities.
- :class:`PIIFilterMemoryPlugin` (memory_pre_write + llm_pre_call, transform):
  regex PII redaction; additionally requires ``pip install 'altk-evolve[pii]'``.
"""

from altk_evolve.hooks.plugins.access_stamp import AccessStampPlugin
from altk_evolve.hooks.plugins.normalizer import MetadataNormalizerPlugin
from altk_evolve.hooks.plugins.pii import PIIFilterMemoryPlugin

__all__ = ["AccessStampPlugin", "MetadataNormalizerPlugin", "PIIFilterMemoryPlugin"]
