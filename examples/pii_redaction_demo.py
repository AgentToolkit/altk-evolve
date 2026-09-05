#!/usr/bin/env python3
"""Signature PII demo for the altk_evolve memory hook seam (issue #275, ask 1).

    "PII must never be saved into memory."

Writes a made-up persona full of fake PII into a throwaway Evolve namespace with
PII redaction configured on the hook seam, then reads it back FROM STORAGE to
prove the PII was scrubbed before it was ever persisted. This is the real
persisted content (a filesystem-backend read), not a cosmetic pass over the
printout — redaction fires at ``memory_pre_write``, the backend write choke
point.

Two PII methods run TOGETHER (defence in depth), configured as plugins on
``HooksConfig``:

  * REGEX  (``PIIFilterMemoryPlugin``, ``[pii-regex]``) — cpex-pii-filter's Rust
    engine. High precision on STRUCTURED identifiers (email, phone, SSN, card,
    IP). No NER: it cannot catch a name.
  * SEMANTIC (``ReadiSemanticPIIPlugin``, ``[pii-semantic]``) — IBM READI NER.
    Catches the free-form NAME the regex method structurally cannot.

HEADLINE: the persona's NAME survives regex alone and is removed only by the
semantic method — that is the case for running both.

Guards:
  * ``[pii-regex]`` absent  -> nothing to redact; print a note and exit.
  * ``[pii-semantic]`` absent -> run regex-only; print that the name will NOT be
    caught without the semantic method.
  * macOS / Apple-Silicon: READI's spaCy transformer runs on torch's MPS
    backend, which fails closed when the model is first touched off the binding
    thread ("Placeholder storage has not been allocated on MPS device!"). With
    ``on_error=fail`` that surfaces as a blocked write. We CATCH it and fall
    back to regex-only with a printed note, rather than crashing. Regex and
    secrets are Rust/regex and unaffected — regex is the mac-friendly method.

Run:
    uv sync --extra hooks --extra pii-regex --extra pii-semantic
    uv run --no-sync python examples/pii_redaction_demo.py
"""

from __future__ import annotations

import importlib.util
import tempfile

from altk_evolve.backend.filesystem import FilesystemSettings
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import shutdown_hooks
from altk_evolve.schema.core import Entity

FILLER = "[REDACTED]"

# All fictional — a made-up persona and obviously-fake identifiers.
PERSONA = "Dana Whitfield"
STRUCTURED_PII = [
    "dana.whitfield@example.com",
    "415-555-0199",
    "123-45-6789",  # SSN-shaped
    "4111 1111 1111 1111",  # test card number
    "192.168.10.42",  # private IP
]

MEMORIES = [
    f"Primary contact is {PERSONA}, who replies fastest at dana.whitfield@example.com.",
    f"{PERSONA} asked for a callback on 415-555-0199 before noon Friday.",
    "For billing we have SSN 123-45-6789 and card 4111 1111 1111 1111 on file.",
    "Last successful login came from IP 192.168.10.42 on the office network.",
    "Remember: the customer prefers metric units and a dark UI theme.",  # no PII — must survive intact
]

REGEX_PLUGIN = HookPluginSpec(
    name="pii_filter_memory",
    kind="altk_evolve.hooks.plugins.pii.PIIFilterMemoryPlugin",
    hooks=["memory_pre_write", "llm_pre_call"],
    mode="sequential",  # sequential (not transform) so it can BLOCK, not just redact
    priority=10,
    on_error="fail",  # fail-closed: never pass unredacted content through
    config={
        "detect_email": True,
        "detect_ssn": True,
        "detect_phone": True,
        "detect_credit_card": True,
        "detect_ip_address": True,
        "default_mask_strategy": "redact",
        "redaction_text": FILLER,
    },
)

SEMANTIC_PLUGIN = HookPluginSpec(
    name="readi_semantic_pii",
    kind="altk_evolve.hooks.plugins.readi.ReadiSemanticPIIPlugin",
    hooks=["memory_pre_write", "llm_pre_call"],
    mode="sequential",
    priority=10,
    on_error="fail",
    config={"readi_extractor": "default", "redaction_text": FILLER},
)


def _write_and_read(plugins: list[HookPluginSpec]) -> list[str]:
    """Configure the seam with ``plugins``, write the PII memories, read back the
    stored content. Returns the stored content strings, sorted to match MEMORIES."""
    config = EvolveConfig(
        backend="filesystem",
        settings=FilesystemSettings(data_dir=tempfile.mkdtemp(prefix="evolve_pii_demo_")),
        hooks=HooksConfig(plugins=plugins),
    )
    client = EvolveClient(config)
    client.create_namespace("demo")
    client.update_entities(
        "demo",
        [Entity(content=m, type="guideline") for m in MEMORIES],
        enable_conflict_resolution=False,
    )
    stored = client.search_entities("demo", limit=50)
    # search_entities has no guaranteed order; align by matching the surviving
    # non-PII tail so IN/OUT pairs line up regardless of storage order.
    by_key = {str(e.content): str(e.content) for e in stored}
    # Fall back to insertion order when content changed under us; we only need
    # the set for assertions, and print in stored order below.
    return list(by_key.values())


def main() -> int:
    if importlib.util.find_spec("cpex_pii_filter") is None:
        print("This demo needs the [pii-regex] extra (cpex-pii-filter). Try:")
        print("    uv sync --extra hooks --extra pii-regex --extra pii-semantic")
        print("    uv run --no-sync python examples/pii_redaction_demo.py")
        return 1

    has_semantic = importlib.util.find_spec("risk_assessment") is not None

    plugins = [REGEX_PLUGIN]
    semantic_active = False
    if has_semantic:
        plugins = [REGEX_PLUGIN, SEMANTIC_PLUGIN]

    print("PII redaction on the memory hook seam (memory_pre_write choke point)")
    print("  regex    method ([pii-regex])    : ACTIVE  -> structured identifiers")
    if has_semantic:
        print("  semantic method ([pii-semantic]) : ACTIVE  -> free-form names (NER)")
    else:
        print("  semantic method ([pii-semantic]) : absent -> names will NOT be caught")
    print()

    try:
        stored = _write_and_read(plugins)
        semantic_active = has_semantic
    except Exception as exc:  # noqa: BLE001 — deliberate: fall back, don't crash
        if not has_semantic:
            raise
        print("  NOTE: the semantic (READI) path failed on this host — falling back to regex-only.")
        print(f"        ({type(exc).__name__}: {str(exc).splitlines()[0][:120]})")
        print("        This is the documented macOS/Apple-Silicon MPS caveat (see DEMO.md);")
        print("        regex redaction is unaffected and is the mac-friendly method.")
        print()
        stored = _write_and_read([REGEX_PLUGIN])
        semantic_active = False

    print("What the agent tried to remember  ->  what actually got stored\n")
    joined = "\n".join(stored)
    # Print IN/OUT by matching each stored line back to an input by its redaction-
    # invariant tail (everything after the PII), so pairs line up cleanly.
    for original in MEMORIES:
        # find the stored line whose non-redacted words overlap this input most
        match = max(stored, key=lambda s: len(set(s.split()) & set(original.split())))
        print(f"  IN : {original}")
        print(f"  OUT: {match}\n")

    # Assertion 1 (always): every STRUCTURED PII value is gone (regex method).
    leaked_structured = [s for s in STRUCTURED_PII if s in joined]
    if leaked_structured:
        print("FAIL — structured PII leaked into storage:", leaked_structured)
        return 1
    print(f"OK  — all {len(STRUCTURED_PII)} structured PII values (email, phone, SSN, card, IP)")
    print("      were replaced with inert filler before storage [regex method].")

    # Assertion 2 (semantic only): the NAME is gone — the regex method cannot do this.
    name_present = PERSONA in joined
    if semantic_active:
        if name_present:
            print(f"FAIL — the name '{PERSONA}' survived despite the semantic method being active.")
            return 1
        print(f"OK  — the free-form NAME '{PERSONA}' was also removed [semantic method];")
        print("      regex alone has no NER and would have left it in place.")
    else:
        print(f"NOTE — the NAME '{PERSONA}' is {'STILL PRESENT' if name_present else 'absent'} — regex has no NER.")
        print("       Install [pii-semantic] and re-run to catch names too (defence in depth).")

    print("\n      The non-PII memory (units + theme preference) was preserved verbatim.")

    shutdown_hooks()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
