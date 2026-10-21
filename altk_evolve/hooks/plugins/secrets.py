"""Structured-secrets redaction plugin, backed by cpex-secrets-detection's Rust core.

A THIRD redaction method alongside the two PII ones (``[pii-regex]`` regex
identifiers, ``[pii-semantic]`` NER names): it targets *credentials/tokens*
(AWS keys, Google API keys, GitHub/Slack tokens, Stripe secrets, private-key
blocks) that neither PII path aims at. It composes with a PII method rather than
replacing it, and chains the same way. Requires
``pip install 'altk-evolve[secrets]'``.

**Native, not raw cpex.** ``cpex-pii-filter`` ships a genuine cpex ``Plugin``
(``PIIFilterPlugin``), so :mod:`altk_evolve.hooks.plugins.pii` reuses it as a
raw-cpex plugin — that is the single deliberate raw-cpex exception.
``cpex-secrets-detection`` does *not* give us an equivalent to reuse, for two
reasons discovered by introspecting the installed package:

* Its ``SecretsDetectionPlugin`` subclasses *mcpgateway*'s ``Plugin`` (a
  different framework — cpex forks it), and its async hook methods
  (``prompt_pre_fetch`` / ``tool_post_invoke`` / ``resource_post_fetch``)
  construct ``mcpgateway`` result types and mutate a *frozen* cpex payload in
  place — so they are unusable outside an mcpgateway host.
* The one framework-free surface the package exposes is
  ``py_scan_container(container, config)`` — a plain Rust *function*, a library,
  not a cpex plugin.

Because the only usable surface is that library function, the correct shape is
a **native** :class:`~altk_evolve.hooks.plugin.HookPluginBase` that wraps the
Rust core directly — exactly like :mod:`altk_evolve.hooks.plugins.readi` wraps
IBM READI. So ``pii`` remains the ONE raw-cpex plugin; ``secrets`` (like
``readi``, ``normalizer``, ``access_stamp``) is native, runs through the seam's
native adapter, and imports no cpex at module top. The Rust core is imported
lazily inside :func:`build_secrets_scanner`, so a missing ``[secrets]`` extra
fails CLOSED at engine init via :meth:`SecretsFilterMemoryPlugin.startup_validate`,
not lazily on the first write.

The Rust scanner **blind-walks** a plain ``dict``/``list`` container and redacts
string leaves into a copy (it never mutates the input). In the native hook
contract entities and messages already arrive as ``list[dict]``, so they are
passed straight through — no pydantic ``model_dump``/``model_validate`` dance.
"""

from __future__ import annotations

import re

from collections.abc import Callable
from typing import Any, cast

from altk_evolve.hooks.plugin import HookContext, HookPluginBase
from altk_evolve.hooks.plugins.identity import restore_merged_from_entries, without_merged_from_entries

DEFAULT_REDACTION_TEXT = "[REDACTED]"

#: Structured / high-precision detectors ON; entropy / JWT-heuristic detectors
#: OFF. The latter (``generic_api_key_assignment``, ``jwt_like``,
#: ``hex_secret_32``, ``base64_24``) OVER-REDACT a memory corpus — which
#: legitimately holds base64 blobs, hex digests, content hashes and JWT-shaped
#: ids — so they are opt-in only. These are the package's real detector keys
#: (verified against ``cpex_secrets_detection``'s ``plugin-manifest.yaml``).
DEFAULT_ENABLED = {
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
}

#: Defaults merged under any user ``config`` (real cpex-secrets-detection keys).
DEFAULT_SCAN_CONFIG: dict[str, Any] = {
    "redact": True,
    "redaction_text": DEFAULT_REDACTION_TEXT,
    # We redact-and-continue; sequential mode still lets a non-redacting config
    # (``redact: false``) block on detection (see :meth:`_redact_items`).
    "block_on_detection": False,
    "min_findings_to_block": 1,
}

#: The framework-free Rust scanner. ``list`` container + config dict in ->
#: ``(count, redacted_container, findings)`` out.
Scanner = Callable[[list, dict], "tuple[int, Any, list]"]


#: The top-level config keys this plugin accepts. The scanner honours more keys
#: than these (cpex-secrets-detection 0.3 added ``field_allowlist`` /
#: ``field_denylist``, which narrow which fields are scanned), so anything not
#: listed here is rejected rather than forwarded: a key that only narrows the
#: scan must never take effect by accident.
SUPPORTED_CONFIG_KEYS = frozenset({"enabled", *DEFAULT_SCAN_CONFIG})


def build_scan_config(config: dict[str, Any]) -> dict[str, Any]:
    """Merge a user ``config`` over the defaults so an empty config still gets
    the structured detectors ON. Top-level keys override; ``enabled`` merges
    per-detector, so a user can flip individual detectors without having to
    respecify the whole map.

    Fails closed on keys it does not know: an unknown top-level key, or an
    unknown detector name under ``enabled``, raises ``ValueError`` naming the
    key, instead of being forwarded to (or silently ignored by) the scanner."""
    unknown = sorted(set(config) - SUPPORTED_CONFIG_KEYS)
    if unknown:
        raise ValueError(
            f"Unsupported secrets plugin config key(s): {', '.join(map(repr, unknown))}. "
            f"Supported keys: {', '.join(sorted(SUPPORTED_CONFIG_KEYS))}."
        )
    unknown_detectors = sorted(set(config.get("enabled") or {}) - set(DEFAULT_ENABLED))
    if unknown_detectors:
        raise ValueError(
            f"Unknown secrets detector(s) under 'enabled': {', '.join(map(repr, unknown_detectors))}. "
            f"Known detectors: {', '.join(sorted(DEFAULT_ENABLED))}."
        )
    merged = {**DEFAULT_SCAN_CONFIG, **{k: v for k, v in config.items() if k != "enabled"}}
    merged["enabled"] = {**DEFAULT_ENABLED, **(config.get("enabled") or {})}
    return merged


def build_secrets_scanner() -> Scanner:
    """Return the Rust scanner ``py_scan_container``. Imports ``cpex_secrets_detection``.

    Narrow guard (mirrors :func:`readi.build_readi_detector`): only a genuinely-
    absent ``cpex_secrets_detection`` gets the ``[secrets]`` install hint. A
    name-less ImportError, or one naming an unrelated module, means a broken
    install rather than a missing extra and must surface as-is — masking it
    would silently disable a compliance plugin.
    """
    try:
        from cpex_secrets_detection.secrets_detection_rust import py_scan_container
    except ModuleNotFoundError as exc:
        if exc.name == "cpex_secrets_detection" or (exc.name or "").startswith("cpex_secrets_detection."):
            raise ImportError(
                "Structured secrets redaction requires cpex-secrets-detection. Install it with: pip install 'altk-evolve[secrets]'"
            ) from exc
        raise
    return cast(Scanner, py_scan_container)


# Supplement the pinned scanner's narrow structured patterns. Keep these under
# its existing detector switches; no entropy heuristics or new defaults.
_PRIVATE_KEY_MARKERS = re.compile(
    r"-----(?P<edge>BEGIN|END) (?P<kind>(?:(?:RSA|DSA|EC|OPENSSH|ENCRYPTED) )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"
)
_KEY_BODY_LINES = re.compile(r"(?:\r?\n[A-Za-z0-9+/=]+(?=\r?\n|\Z))*")
_SUPPLEMENTARY_PATTERNS = {
    "aws_secret_access_key": re.compile(
        r"(?i)(\baws[ \t]+)((?:AKIA|ASIA)[A-Z0-9]{16})"
        r"([ \t]+secret(?:_access_key)?[ \t]*[=:][ \t]*)(?P<quote>['\"]?)"
        r"(?P<secret>[A-Za-z0-9/+=]{40})(?P=quote)(?![A-Za-z0-9/+=])"
    ),
    "aws_access_key_id": re.compile(r"\bASIA[A-Z0-9]{16}\b"),
    "slack_token": re.compile(
        r"\b(?:xoxs|xapp)-[A-Za-z0-9-]{10,80}(?![A-Za-z0-9-])"
        r"|https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+"
    ),
}


def _mask_private_keys(text: str, replacement: str) -> tuple[int, str]:
    """Mask matching armor pairs, or only the header and adjacent base64 lines.

    Delimiter pairing stops at the next marker, so an unmatched header cannot
    consume prose or another key. Each intervening region is visited once.
    """
    markers = list(_PRIVATE_KEY_MARKERS.finditer(text))
    pieces: list[str] = []
    previous = count = 0
    for index, marker in enumerate(markers):
        if marker.group("edge") != "BEGIN":
            continue
        following = markers[index + 1] if index + 1 < len(markers) else None
        if following is not None and following.group("edge") == "END" and following.group("kind") == marker.group("kind"):
            end = following.end()
        else:
            body = _KEY_BODY_LINES.match(text, marker.end())
            assert body is not None
            end = body.end()
        pieces.extend((text[previous : marker.start()], replacement))
        previous = end
        count += 1
    pieces.append(text[previous:])
    return count, "".join(pieces)


def _supplementary_scan(items: list[dict], config: dict) -> tuple[int, list[dict]]:
    """Scan string leaves before Rust can mask just a credential's prefix.

    Each private-key block or AWS credential pair is one finding. This always
    masks matched spans in a copy, even for detection-only calls, so the Rust
    pass cannot count them a second time. The copy is discarded in that mode.
    """
    count = 0

    def visit(value: Any) -> Any:
        nonlocal count
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return tuple(visit(item) for item in value)
        if not isinstance(value, str):
            return value
        replacement = cast(str, config["redaction_text"]) if config["redact"] else DEFAULT_REDACTION_TEXT
        if config["enabled"]["private_key_block"]:
            private_count, value = _mask_private_keys(value, replacement)
            count += private_count
        for name, pattern in _SUPPLEMENTARY_PATTERNS.items():
            if not config["enabled"][name]:
                continue

            def replace(match: re.Match[str]) -> str:
                nonlocal count
                count += 1
                if name == "aws_secret_access_key":
                    key = replacement if config["enabled"]["aws_access_key_id"] else match.group(2)
                    quote = match.group("quote")
                    return match.group(1) + key + match.group(3) + quote + replacement + quote
                return replacement

            value = pattern.sub(replace, value)
        return value

    result = visit(items)
    return count, result


class SecretsFilterMemoryPlugin(HookPluginBase):
    """Native plugin: structured-secrets redaction on writes and LLM egress.

    No cpex import — a native :class:`~altk_evolve.hooks.plugin.HookPluginBase`
    wrapping ``cpex-secrets-detection``'s Rust core (``py_scan_container``),
    imported lazily so a missing ``[secrets]`` extra fails CLOSED at engine init
    via :meth:`startup_validate`, not lazily on the first write.

    Config keys (all optional; real cpex-secrets-detection keys, merged over
    :data:`DEFAULT_SCAN_CONFIG`):
      - ``enabled``: per-detector bool map (structured ON, entropy/JWT OFF by
        default — the latter over-redact a memory corpus)
      - ``redact``: redact findings in place (default True)
      - ``redaction_text``: mask string (default ``[REDACTED]``)
      - ``block_on_detection`` / ``min_findings_to_block``: with ``redact:
        false``, a finding halts the operation (see below)

    Any other key (including the scanner's ``field_allowlist`` /
    ``field_denylist``) is rejected at construction; see :func:`build_scan_config`.

    Registered ``mode: sequential`` + ``on_error: fail`` (see the scaffold YAML /
    docs): sequential so it can BLOCK, not just redact, and fail-closed so a
    crashing scanner halts the operation rather than passing content through.
    """

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config)
        self._scanner: Scanner | None = None
        self._scan_config: dict[str, Any] = build_scan_config(self.config)

    def startup_validate(self) -> None:
        """Build the scanner at engine init so a missing ``[secrets]`` extra
        fails CLOSED here (with the extra-naming ImportError) rather than lazily
        on the first write."""
        self._get_scanner()

    def _get_scanner(self) -> Scanner:
        if self._scanner is None:
            self._scanner = build_secrets_scanner()
        return self._scanner

    def _redact_items(self, items: list[dict]) -> list[dict] | None:
        """Scan ``items`` (entity dumps or chat messages) through the Rust core.

        Returns the redacted list, or ``None`` when nothing was found (the
        unchanged contract). When redaction is disabled (``redact: false``) a
        finding halts the operation by RAISING — native plugins block by
        raising (the seam converts it to a fail-closed ``MemoryPolicyViolation``
        under ``on_error: fail``), never by returning a payload.
        """
        supplementary_count, prepared = _supplementary_scan(items, self._scan_config)
        count, redacted, _findings = self._get_scanner()(prepared, self._scan_config)
        count += supplementary_count
        if count == 0:
            return None
        if not self._scan_config.get("redact", False):
            if self._scan_config.get("block_on_detection", True) and count >= int(self._scan_config.get("min_findings_to_block", 1)):
                raise RuntimeError(f"{count} secret(s) detected; blocked (redact disabled).")
            return None
        return cast("list[dict]", redacted)

    def memory_pre_write(self, payload: Any, context: HookContext) -> Any | None:
        # The scanner sees every other metadata value, but not the merged_from
        # entries consolidation keys on: a content digest reads as a hex secret.
        originals = payload.entities
        views = [
            {**entity, "metadata": without_merged_from_entries(entity["metadata"])} if entity.get("metadata") else entity
            for entity in originals
        ]
        redacted = self._redact_items(views)
        if redacted is not None:
            if len(redacted) != len(originals):
                raise ValueError("Secrets filter changed the entity count")
            redacted = [
                {**entity, "metadata": restore_merged_from_entries(entity.get("metadata") or {}, original["metadata"])}
                if original.get("metadata")
                else entity
                for original, entity in zip(originals, redacted, strict=True)
            ]
        # Contract: changes travel back as a replacement payload; the input
        # payload is never mutated in place.
        return None if redacted is None else payload.replace(entities=redacted)

    def llm_pre_call(self, payload: Any, context: HookContext) -> Any | None:
        redacted = self._redact_items(payload.messages)
        return None if redacted is None else payload.replace(messages=redacted)


__all__ = [
    "DEFAULT_ENABLED",
    "SUPPORTED_CONFIG_KEYS",
    "SecretsFilterMemoryPlugin",
    "build_scan_config",
    "build_secrets_scanner",
]
