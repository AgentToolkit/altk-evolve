"""In-tree hook plugins shipped with altk_evolve.

Most plugins are **native** hook plugins (see
:mod:`altk_evolve.hooks.plugin`): the domain logic is a pure, engine-free
function at the top of the module, and the plugin class subclasses
``HookPluginBase`` (no cpex import) — the execution engine sits behind an
adapter in :mod:`altk_evolve.hooks.manager`. Importing this package — and using
the cores or the native plugins — needs no extra installed; only a plugin's own
detector lib (e.g. READI for semantic PII) does.

- :class:`MetadataNormalizerPlugin` (memory_pre_write, transform): stamps
  canonical metadata (``trace_id``, ``created_at``); core
  :func:`normalize_entities`. Native.
- :class:`AccessStampPlugin` (memory_post_read, fire_and_forget): stamps
  ``last_accessed`` on read entities; core :func:`build_access_stamps`. Native.
- :class:`PIIFilterMemoryPlugin` (memory_pre_write + llm_pre_call, sequential):
  regex PII redaction (the ``[pii-regex]`` method); additionally requires
  ``pip install 'altk-evolve[pii-regex]'`` (``[pii]`` is a back-compat alias).
  The one **raw cpex** plugin (proving dual support): it adapts the external
  cpex-pii-filter ``Plugin`` onto Evolve's hook types, so the cpex coupling is
  its purpose.
- :class:`ReadiSemanticPIIPlugin` (memory_pre_write + llm_pre_call, sequential):
  semantic (NER) PII redaction via IBM READI — the ``[pii-semantic]`` method,
  catching names/locations/orgs that regex cannot; cores
  :func:`redact_entities` / :func:`redact_messages` / :func:`redact_spans`.
  Additionally requires ``pip install 'altk-evolve[pii-semantic]'``. Running both
  methods is the recommended defence-in-depth default.
- :class:`SecretsFilterMemoryPlugin` (memory_pre_write + llm_pre_call, sequential):
  structured **secrets** redaction (credentials/tokens: AWS keys, GitHub/Slack
  tokens, private-key blocks) — a third method, orthogonal to the two PII ones;
  additionally requires ``pip install 'altk-evolve[secrets]'``. Native: like
  ``readi`` wraps IBM READI, it wraps cpex-secrets-detection's framework-free
  Rust core (``py_scan_container``) directly — the packaged cpex plugin is
  mcpgateway-bound and unusable, so ``pii`` stays the ONE raw-cpex plugin.
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
from altk_evolve.hooks.plugins.secrets import SecretsFilterMemoryPlugin

__all__ = [
    "AccessStampPlugin",
    "MetadataNormalizerPlugin",
    "PIIFilterMemoryPlugin",
    "ReadiSemanticPIIPlugin",
    "SecretsFilterMemoryPlugin",
    "build_access_stamps",
    "build_readi_detector",
    "normalize_entities",
    "redact_entities",
    "redact_messages",
    "redact_spans",
    "redact_text",
]
