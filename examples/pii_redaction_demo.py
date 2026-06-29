#!/usr/bin/env python3
"""Demo: PII never lands in memory (issue #275).

Writes several made-up "memories" containing fake PII into a throwaway Evolve
namespace with redaction enabled, then reads them back from the store to show
the PII has been replaced with inert filler before it was ever persisted.

The redaction happens at the backend write choke-point
(``BaseEntityBackend.update_entities``), so this is the real storage path — not
a cosmetic pass over the printout.

Run it:

    uv run --extra pii python examples/pii_redaction_demo.py

Requires the ``[pii]`` extra (the CPEX ``cpex-pii-filter`` backend). Without it
the script prints a notice and exits, since there would be nothing to redact.
"""

from __future__ import annotations

import importlib.util
import tempfile

from altk_evolve.backend.filesystem import FilesystemSettings
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.pii import PIIConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.pii import NullRedactor
from altk_evolve.schema.core import Entity

FILLER = "[INERT]"

# All fictional — a made-up persona and obviously-fake identifiers.
PERSONA = "Dana Whitfield"
SECRETS = [
    PERSONA,
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


def main() -> int:
    if importlib.util.find_spec("cpex_pii_filter") is None:
        print("This demo needs the [pii] extra (cpex-pii-filter). Try:")
        print("    uv run --extra pii python examples/pii_redaction_demo.py")
        return 1

    with tempfile.TemporaryDirectory() as data_dir:
        config = EvolveConfig(
            backend="filesystem",
            settings=FilesystemSettings(data_dir=data_dir),
            pii=PIIConfig(
                enabled=True,
                mode="regex",
                entities=["email", "phone", "ssn", "credit_card", "ip_address"],
                mask_strategy="redact",
                redaction_text=FILLER,
                # The regex backend has no NER, so teach it the fictional name.
                custom_patterns=[{"name": "persona", "description": "demo persona name", "pattern": PERSONA}],
            ),
        )
        client = EvolveClient(config)

        active = type(client.backend.redactor).__name__
        if isinstance(client.backend.redactor, NullRedactor):
            print("Redactor is a no-op — PII would NOT be removed. Aborting demo.")
            return 1
        print(f"Redaction active: {active} (mask -> {FILLER})\n")

        client.ensure_namespace("demo")
        client.update_entities(
            "demo",
            [Entity(content=m, type="guideline") for m in MEMORIES],
            enable_conflict_resolution=False,
        )

        stored = sorted(client.get_all_entities("demo"), key=lambda e: e.id)

        print("What the agent tried to remember  ->  what actually got stored\n")
        for original, entity in zip(MEMORIES, stored):
            print(f"  IN : {original}")
            print(f"  OUT: {entity.content}\n")

        joined = "\n".join(str(e.content) for e in stored)
        leaked = [s for s in SECRETS if s in joined]
        if leaked:
            print("FAIL — PII leaked into storage:", leaked)
            return 1

        print(f"OK — all {len(SECRETS)} PII items were replaced with inert filler before storage;")
        print("     the non-PII memory (units + theme preference) was preserved verbatim.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
