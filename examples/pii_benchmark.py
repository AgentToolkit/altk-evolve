#!/usr/bin/env python3
"""Benchmark: how effectively does the redactor actually remove PII? (issue #275)

Scores the CPEX redactor against a labeled gold set — text with known PII spans —
and reports the metrics that matter for compliance:

  * recall      — fraction of true PII spans that got masked (the "did we remove
                  it?" number; 1 - recall is the leak rate at span level)
  * precision   — fraction of masked spans that were actually PII (over-redaction
                  is the complement)
  * F1          — harmonic mean
  * leak rate   — fraction of *records* where any PII literal survived redaction

The gold set is generated deterministically from templates with known values, so
offsets are exact and the run needs no network or external corpus. To benchmark
against a real corpus instead, pass --data PATH to a JSONL file of
{"text": ..., "spans": [{"start","end","label","value"}]} records (e.g. an export
of ai4privacy/pii-masking-200k, or Microsoft Presidio's evaluator data).

Run:  uv run --extra pii python examples/pii_benchmark.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from collections import defaultdict

# Fixed example values per label. The first five labels are entity types the
# CPEX regex backend supports; `person` and `address` are deliberately included
# to expose the no-NER gap honestly.
VALUES: dict[str, list[str]] = {
    "email": ["dana.whitfield@example.com", "j.doe@acme.co.uk", "support+billing@test.org"],
    "phone": ["415-555-0199", "(212) 555-0143", "+1 650 555 0188"],
    "ssn": ["123-45-6789", "078-05-1120"],
    "credit_card": ["4111 1111 1111 1111", "5500-0000-0000-0004"],
    "ip_address": ["192.168.10.42", "10.0.0.5"],
    "person": ["Dana Whitfield", "Carlos Mendes", "Priya Raman"],
    "address": ["742 Evergreen Terrace", "1600 Pennsylvania Ave"],
}

TEMPLATES = [
    "Email {person} at {email} or call {phone}.",
    "Charge card {credit_card} for the order and email a receipt to {email}.",
    "Login from {ip_address} flagged; notify {person} at {email}.",
    "{person} lives at {address}; SSN on file is {ssn}.",
    "Customer {person} (SSN {ssn}) paid with {credit_card}.",
    "Reach support at {phone}; escalations route to {email}.",
    "Server {ip_address} sent alerts to {phone} and {email}.",
    "Ship to {person}, {address}. Backup contact: {phone}.",
]

REPEATS = 3  # how many times to cycle the templates (advances the value picker)


def build_gold() -> list[dict]:
    """Render templates into (text, spans) records with exact offsets."""
    counters: dict[str, int] = defaultdict(int)

    def pick(label: str) -> str:
        options = VALUES[label]
        value = options[counters[label] % len(options)]
        counters[label] += 1
        return value

    records = []
    for _ in range(REPEATS):
        for template in TEMPLATES:
            text_parts: list[str] = []
            spans: list[dict] = []
            pos = 0
            for part in re.split(r"(\{[a-z_]+\})", template):
                if part.startswith("{") and part.endswith("}"):
                    label = part[1:-1]
                    value = pick(label)
                    spans.append({"start": pos, "end": pos + len(value), "label": label, "value": value})
                    text_parts.append(value)
                    pos += len(value)
                else:
                    text_parts.append(part)
                    pos += len(part)
            records.append({"text": "".join(text_parts), "spans": spans})
    return records


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def score(records: list[dict], redactor) -> dict:
    tp = fp = fn = 0
    per_label: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # label -> [detected, total]
    leaked_records = 0

    for rec in records:
        text = rec["text"]
        gold = rec["spans"]
        detected = redactor.detect(text)
        det_spans = [(d["start"], d["end"]) for spans in detected.values() for d in spans]

        record_leaked = False
        for g in gold:
            per_label[g["label"]][1] += 1
            if any(_overlap(g["start"], g["end"], ds, de) for ds, de in det_spans):
                tp += 1
                per_label[g["label"]][0] += 1
            else:
                fn += 1
                record_leaked = True

        for ds, de in det_spans:
            if not any(_overlap(g["start"], g["end"], ds, de) for g in gold):
                fp += 1

        # Value-level leak: does any gold PII literal survive the actual redaction?
        redacted = redactor.redact(text)
        if any(g["value"] in redacted for g in gold):
            record_leaked = True
        if record_leaked:
            leaked_records += 1

    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "records": len(records),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "leak_rate": leaked_records / len(records) if records else 0.0,
        "per_label": dict(per_label),
    }


def _print(title: str, result: dict) -> None:
    print(f"\n== {title} ==")
    print(f"  records={result['records']}  TP={result['tp']}  FP={result['fp']}  FN(leaked spans)={result['fn']}")
    print(f"  recall={result['recall']:.2f}  precision={result['precision']:.2f}  F1={result['f1']:.2f}")
    print(f"  record-level leak rate={result['leak_rate']:.2f}")
    print("  per-entity recall:")
    for label, (detected, total) in sorted(result["per_label"].items()):
        rec = detected / total if total else 0.0
        print(f"    {label:<12} {detected:>3}/{total:<3}  recall={rec:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="JSONL gold file ({text, spans}); defaults to the built-in synthetic set.")
    args = parser.parse_args()

    if importlib.util.find_spec("cpex_pii_filter") is None:
        print("This benchmark needs the [pii] extra. Try:")
        print("    uv run --extra pii python examples/pii_benchmark.py")
        return 1

    from altk_evolve.config.pii import PIIConfig
    from altk_evolve.pii import get_redactor

    if args.data:
        records = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]
    else:
        records = build_gold()

    structured = ["email", "phone", "ssn", "credit_card", "ip_address"]

    # Run 1: built-in structured entity flags (no names/addresses).
    base = get_redactor(PIIConfig(enabled=True, entities=structured))
    _print("CPEX regex — structured entities only", score(records, base))

    # Run 2: add custom_patterns for the persona names to show the mitigation
    # path for the no-NER gap (exact-name regexes here; in production these would
    # be name lists / a semantic backend).
    name_patterns = [{"name": f"person{i}", "description": "demo name", "pattern": re.escape(n)} for i, n in enumerate(VALUES["person"])]
    augmented = get_redactor(PIIConfig(enabled=True, entities=structured, custom_patterns=name_patterns))
    _print("CPEX regex + custom name patterns", score(records, augmented))

    print("\nNotes:")
    print("  - Recall on structured PII is the headline 'did we remove it' number.")
    print("  - 'person'/'address' have no built-in detector (regex has no NER), so they")
    print("    leak until you add custom_patterns or a semantic backend (pii.mode: semantic).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
