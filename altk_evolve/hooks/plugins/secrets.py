"""Secrets redaction plugin: aliases cpex-secrets-detection onto the altk_evolve hook types.

``cpex_secrets_detection.secrets_detection.SecretsDetectionPlugin`` is a CPEX
plugin, but (like cpex-pii-filter) its handlers are only the built-in hook names
(``tool_post_invoke`` etc.), so attaching it to our custom hooks needs this thin
aliasing subclass that exposes ``memory_pre_write`` and ``llm_pre_call``.

This is the **structured-secrets** redaction method — a third method alongside
the two PII ones (``[pii-regex]`` regex identifiers, ``[pii-semantic]`` NER
names). It targets *credentials/tokens* (AWS keys, GitHub/Slack tokens, Stripe
secrets, private-key blocks, ...) that neither PII path aims at. Requires
``pip install 'altk-evolve[secrets]'`` (cpex + cpex-secrets-detection).

Like ``pii`` this module has no engine-agnostic core: adapting an external cpex
redactor onto our hook types IS its domain logic, so the cpex coupling is the
point (the redaction logic itself lives — and is tested — in the
cpex-secrets-detection package). It is the second deliberate **raw cpex**
exception, for the same reason: reuse a maintained Rust redactor instead of
reimplementing credential regexes.

Two implementation differences from ``pii`` are forced by the packaged plugin,
NOT by choice, and are noted here so future maintainers do not "fix" them back
toward the pii shape:

* ``SecretsDetectionPlugin`` exposes ``prompt_pre_fetch`` / ``tool_post_invoke``
  / ``resource_post_fetch`` (there is no ``tool_pre_invoke``), and those async
  wrappers construct ``mcpgateway`` result types and mutate a *frozen* cpex
  payload in place — so they are unusable outside an mcpgateway host. We
  therefore bridge through the plugin's public, framework-free Rust entry point
  ``py_scan_container(container, config)`` instead of round-tripping a payload
  through a native hook method.
* That Rust scanner walks plain ``dict``/``list`` containers but does NOT walk
  pydantic models (unlike cpex-pii-filter, which redacts ``Entity.content`` in
  place). So the memory path dumps each entity to a dict, scans, and reloads it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from altk_evolve.hooks.types import HookType

try:
    from cpex.framework import Plugin as _CpexPlugin
    from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult, PluginViolation
    from cpex_secrets_detection.secrets_detection import SecretsDetectionPlugin as _SecretsDetectionPlugin
    from cpex_secrets_detection.secrets_detection_rust import py_scan_container

    _HAS_SECRETS_DETECTION = True
except ImportError as exc:
    # Fall back to the stub ONLY when the optional dependency itself (cpex or
    # cpex-secrets-detection, or a submodule of either) is genuinely MISSING.
    # Anything else must propagate:
    #   * A name-less ImportError (``exc.name is None``) is NOT a missing
    #     optional dep — it typically comes from a broken import inside an
    #     installed package, and masking it as "install 'altk-evolve[secrets]'"
    #     would hide a real bug and silently disable a compliance plugin.
    #   * An ImportError naming an unrelated module means a broken transitive,
    #     which must also surface.
    # We therefore require a ``ModuleNotFoundError`` whose missing module is
    # cpex / cpex_secrets_detection (or a submodule) before falling back.
    _missing = exc.name if isinstance(exc, ModuleNotFoundError) else None
    _is_cpex_dep = _missing is not None and (
        _missing in ("cpex", "cpex_secrets_detection") or _missing.startswith("cpex.") or _missing.startswith("cpex_secrets_detection.")
    )
    if _is_cpex_dep:
        _HAS_SECRETS_DETECTION = False
    else:
        raise

if _HAS_SECRETS_DETECTION:

    def _default_config() -> PluginConfig:
        return PluginConfig(
            name="secrets_filter_memory",
            kind="altk_evolve.hooks.plugins.secrets.SecretsFilterMemoryPlugin",
            hooks=[HookType.MEMORY_PRE_WRITE.value, HookType.LLM_PRE_CALL.value],
            # SEQUENTIAL, not TRANSFORM: CPEX silently downgrades
            # continue_processing=False -> True in TRANSFORM/AUDIT modes, so a
            # redactor in transform mode can redact but can NEVER block. Only
            # sequential preserves BOTH redaction-chaining and the ability to
            # halt on an unredacted finding.
            mode=PluginMode.SEQUENTIAL,
            priority=10,
            # Fail-closed: if the scanner crashes it must halt the operation,
            # never pass content with secrets through.
            on_error=OnError.FAIL,
            config={
                # Per-detector enable map (cpex-secrets-detection's real schema).
                # Structured / high-precision detectors ON; entropy/JWT-heuristic
                # detectors OFF — they OVER-REDACT a memory corpus (legit base64,
                # hex digests, hashes) and are opt-in only.
                "enabled": {
                    "aws_access_key_id": True,
                    "aws_secret_access_key": True,
                    "google_api_key": True,
                    "github_token": True,
                    "stripe_secret_key": True,
                    "slack_token": True,
                    "private_key_block": True,
                    "generic_api_key_assignment": False,
                    "jwt_like": False,
                    "hex_secret_32": False,
                    "base64_24": False,
                },
                "redact": True,
                "redaction_text": "[REDACTED]",
                # We redact rather than hard-block; sequential mode still lets a
                # non-redacting config (redact: false) block on detection.
                "block_on_detection": False,
                "min_findings_to_block": 1,
            },
        )

    class SecretsFilterMemoryPlugin(_SecretsDetectionPlugin, _CpexPlugin):
        """Structured-secrets redaction on memory writes and LLM egress.

        Accepts the same ``config`` keys as cpex-secrets-detection: an
        ``enabled`` per-detector map, plus ``redact`` / ``redaction_text`` /
        ``block_on_detection`` / ``min_findings_to_block``.

        A THIRD difference from ``pii`` (see the module docstring for the first
        two): ``cpex_pii_filter.PIIFilterPlugin`` subclasses cpex's ``Plugin``,
        but ``cpex_secrets_detection.SecretsDetectionPlugin`` subclasses
        *mcpgateway*'s ``Plugin`` (a different framework — cpex forks it). The
        hook manager routes a plugin down the raw-cpex path only when it is a
        cpex ``Plugin`` subclass, and cpex's dispatcher reads state that only
        cpex's ``Plugin.__init__`` sets. So we also inherit cpex ``Plugin`` and
        run BOTH constructors explicitly: ``SecretsDetectionPlugin.__init__``
        (its stub base never chains to cpex) does not initialize cpex's
        dispatch state, and cpex's ``Plugin.__init__`` does not build the Rust
        core.
        """

        def __init__(self, config: PluginConfig | None = None) -> None:
            resolved = config or _default_config()
            # SecretsDetectionPlugin.__init__ builds its Rust core and, via its
            # stub Plugin base, sets self._config — but never runs cpex's
            # Plugin.__init__ (different base class), which is what cpex's
            # dispatcher relies on. Run cpex's explicitly afterward.
            _SecretsDetectionPlugin.__init__(self, resolved)
            _CpexPlugin.__init__(self, resolved)
            # Own handle on the scanner config dict; we drive the scan through
            # the framework-free py_scan_container entry point, not the mcpgateway
            # -bound hook methods.
            self._scan_config: dict[str, Any] = dict(resolved.config or {})

        async def _delegate(self, payload: Any, context: Any, field: str) -> Any:
            """Scan one payload field's items through the Rust secrets scanner.

            ``entities`` items are pydantic models; ``messages`` items are plain
            dicts. The Rust scanner only walks dict/list containers, so pydantic
            items are dumped to dicts for the scan and reloaded afterwards.
            """
            value = getattr(payload, field)
            if not isinstance(value, list):
                return PluginResult(continue_processing=True)

            walkable = [item.model_dump() if isinstance(item, BaseModel) else item for item in value]
            count, redacted, _findings = py_scan_container(walkable, self._scan_config)
            if count == 0:
                return PluginResult(continue_processing=True)

            # If configured not to redact, a finding blocks (fail-closed).
            if not self._scan_config.get("redact", False) and self._scan_config.get("block_on_detection", True):
                if count >= int(self._scan_config.get("min_findings_to_block", 1)):
                    return PluginResult(
                        continue_processing=False,
                        violation=PluginViolation(
                            reason="secret detected",
                            description=f"{count} secret(s) detected in {field}; blocked (redact disabled).",
                            code="SECRET_DETECTED",
                        ),
                    )

            rebuilt = [type(orig).model_validate(red) if isinstance(orig, BaseModel) else red for orig, red in zip(value, redacted)]
            return PluginResult(
                continue_processing=True,
                modified_payload=payload.model_copy(update={field: rebuilt}),
            )

        async def memory_pre_write(self, payload: Any, context: Any) -> Any:
            return await self._delegate(payload, context, "entities")

        async def llm_pre_call(self, payload: Any, context: Any) -> Any:
            return await self._delegate(payload, context, "messages")

else:

    class SecretsFilterMemoryPlugin:  # type: ignore[no-redef]
        """Stub — install 'altk-evolve[secrets]' for secrets redaction support."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "SecretsFilterMemoryPlugin requires cpex and cpex-secrets-detection. Install them with: pip install 'altk-evolve[secrets]'"
            )
