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
offsets are exact and the run needs no network or external corpus.

To benchmark against a real, established corpus instead:
  * --dataset ai4privacy/pii-masking-200k   stream an ai4privacy-style HF dataset
    (its privacy_mask is already {value,start,end,label}); needs the [bench] extra.
  * --data PATH                             a local JSONL of {text, spans:[...]}.

Run (synthetic):  uv run --extra pii python examples/pii_benchmark.py
Run (real corpus): uv run --extra pii --extra bench python examples/pii_benchmark.py \\
                       --dataset ai4privacy/pii-masking-200k --limit 1000
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

# Canonical entity types the CPEX regex backend actually targets. Used to report
# a "supported-subset" recall: of the PII types CPEX claims to detect, how well
# does it remove them? (Distinct from overall recall, which is dragged down by
# types CPEX has no detector for — names, addresses, DOB, IBAN, crypto, …)
SUPPORTED = {"email", "phone", "ssn", "credit_card", "ip_address"}

# ai4privacy/pii-masking-* label vocabulary -> our canonical types. Unmapped
# ai4privacy labels are lowercased as-is (firstname, street, dob, …) so per-label
# recall still shows them (at recall 0 for CPEX).
AI4_NORMALIZE = {
    "EMAIL": "email",
    "PHONENUMBER": "phone",
    "TELEPHONENUM": "phone",  # openpii-1.5m
    "CREDITCARDNUMBER": "credit_card",
    "SSN": "ssn",
    "SOCIALNUM": "ssn",  # openpii-1.5m
    "IP": "ip_address",
    "IPV4": "ip_address",
    "IPV6": "ip_address",
}


def load_hf(dataset_id: str, split: str, limit: int, language: str | None) -> list[dict]:
    """Load an ai4privacy-style HF dataset into {text, spans:[{start,end,label,value}]}.

    The ai4privacy `privacy_mask` is already {value,start,end,label} with char
    offsets into `source_text`, so this is a near-1:1 mapping; labels are
    normalized to our canonical types where CPEX has an equivalent detector.
    """
    from datasets import load_dataset  # lazy: only needed for --dataset (the [bench] extra)

    ds = load_dataset(dataset_id, split=split, streaming=True)
    records: list[dict] = []
    for row in ds:
        if language and row.get("language") != language:
            continue
        spans = [
            {
                "start": m["start"],
                "end": m["end"],
                "label": AI4_NORMALIZE.get(m["label"], str(m["label"]).lower()),
                "value": m["value"],
            }
            for m in (row.get("privacy_mask") or [])
        ]
        records.append({"text": row["source_text"], "spans": spans})
        if len(records) >= limit:
            break
    return records


WIKIANN_LABELS = ["O", "B-PER", "I-PER", "B-ORG", "I-ORG", "B-LOC", "I-LOC"]


def load_wikiann(dataset_id: str, config: str, split: str, limit: int) -> list[dict]:
    """Load a WikiANN-style NER dataset (tokens + BIO ner_tags) into span records.

    Tokens are concatenated with no separator (correct for CJK scripts), and the
    BIO tags are folded into PER/ORG/LOC character spans. Used to benchmark NER
    coverage on languages ai4privacy doesn't include (e.g. Japanese).
    """
    from datasets import load_dataset  # lazy: [bench] extra

    ds = load_dataset(dataset_id, config, split=split, streaming=True)
    records: list[dict] = []
    for row in ds:
        tokens = row["tokens"]
        tags = [WIKIANN_LABELS[t] if isinstance(t, int) else t for t in row["ner_tags"]]
        text = "".join(tokens)
        offsets, pos = [], 0
        for tok in tokens:
            offsets.append(pos)
            pos += len(tok)
        spans: list[dict] = []
        i = 0
        while i < len(tags):
            if tags[i].startswith("B-"):
                label = tags[i][2:]
                start, end = offsets[i], offsets[i] + len(tokens[i])
                j = i + 1
                while j < len(tags) and tags[j] == f"I-{label}":
                    end = offsets[j] + len(tokens[j])
                    j += 1
                spans.append({"start": start, "end": end, "label": label.lower(), "value": text[start:end]})
                i = j
            else:
                i += 1
        records.append({"text": text, "spans": spans})
        if len(records) >= limit:
            break
    return records


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
    sup_detected = sum(d for lab, (d, _t) in per_label.items() if lab in SUPPORTED)
    sup_total = sum(t for lab, (_d, t) in per_label.items() if lab in SUPPORTED)
    return {
        "records": len(records),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "recall": recall,
        "precision": precision,
        "f1": f1,
        "leak_rate": leaked_records / len(records) if records else 0.0,
        "supported_recall": sup_detected / sup_total if sup_total else 0.0,
        "supported_total": sup_total,
        "per_label": dict(per_label),
    }


def _print(title: str, result: dict) -> None:
    print(f"\n== {title} ==")
    print(f"  records={result['records']}  TP={result['tp']}  FP={result['fp']}  FN(leaked spans)={result['fn']}")
    print(f"  recall={result['recall']:.2f}  precision={result['precision']:.2f}  F1={result['f1']:.2f}")
    print(f"  recall on CPEX-supported types only={result['supported_recall']:.2f}  (over {result['supported_total']} spans)")
    print(f"  record-level leak rate={result['leak_rate']:.2f}")
    print("  per-entity recall:")
    for label, (detected, total) in sorted(result["per_label"].items()):
        rec = detected / total if total else 0.0
        print(f"    {label:<12} {detected:>3}/{total:<3}  recall={rec:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", help="JSONL gold file ({text, spans}); defaults to the built-in synthetic set.")
    parser.add_argument("--dataset", help="Hugging Face dataset id of an ai4privacy-style set, e.g. ai4privacy/pii-masking-200k.")
    parser.add_argument("--split", default="train", help="HF split to stream (default: train).")
    parser.add_argument("--limit", type=int, default=1000, help="Max records to score from --dataset (default: 1000).")
    parser.add_argument("--language", default="en", help="Filter HF records by language (default: en; empty for all).")
    parser.add_argument(
        "--mode",
        choices=["regex", "semantic", "both"],
        default="regex",
        help="Backend(s) to benchmark: regex (CPEX), semantic (IBM READI NER), or both.",
    )
    parser.add_argument("--dataset-format", choices=["ai4privacy", "wikiann"], default="ai4privacy", help="Schema of --dataset.")
    parser.add_argument("--dataset-config", default=None, help="HF dataset config name (e.g. 'ja' for wikiann).")
    # Semantic-backend selection (passed through to PIIConfig for --mode semantic/both).
    parser.add_argument("--readi-extractor", choices=["default", "spacy", "hf", "presidio"], default="default")
    parser.add_argument("--readi-model", default=None, help="spaCy pipeline name or HF pipeline('ner') id for the extractor.")
    parser.add_argument("--readi-language", default="en", help="Language code for the spacy/presidio extractor.")
    args = parser.parse_args()

    want_regex = args.mode in ("regex", "both")
    want_semantic = args.mode in ("semantic", "both")

    if want_regex and importlib.util.find_spec("cpex_pii_filter") is None:
        print("regex mode needs the [pii] extra. Try: uv run --extra pii python examples/pii_benchmark.py")
        return 1
    if want_semantic and importlib.util.find_spec("risk_assessment") is None:
        print("semantic mode needs the [readi] extra. Try: uv run --extra readi python examples/pii_benchmark.py --mode semantic")
        return 1

    from altk_evolve.config.pii import PIIConfig
    from altk_evolve.pii import get_redactor

    if args.dataset and args.dataset_format == "wikiann":
        print(f"Loading {args.dataset} [{args.dataset_config}] (split={args.split}, limit={args.limit}) ...")
        records = load_wikiann(args.dataset, args.dataset_config, args.split, args.limit)
    elif args.dataset:
        print(f"Loading {args.dataset} (split={args.split}, limit={args.limit}, language={args.language or 'all'}) ...")
        records = load_hf(args.dataset, args.split, args.limit, args.language or None)
    elif args.data:
        records = [json.loads(line) for line in open(args.data, encoding="utf-8") if line.strip()]
    else:
        records = build_gold()

    structured = ["email", "phone", "ssn", "credit_card", "ip_address"]

    if want_regex:
        base = get_redactor(PIIConfig(enabled=True, entities=structured))
        _print("CPEX regex — structured entities only", score(records, base))
        if not args.dataset:
            # Synthetic set: show the no-NER mitigation by adding name patterns.
            name_patterns = [
                {"name": f"person{i}", "description": "demo name", "pattern": re.escape(n)} for i, n in enumerate(VALUES["person"])
            ]
            augmented = get_redactor(PIIConfig(enabled=True, entities=structured, custom_patterns=name_patterns))
            _print("CPEX regex + custom name patterns", score(records, augmented))

    if want_semantic:
        label = f"IBM READI semantic ({args.readi_extractor}"
        label += f":{args.readi_model}" if args.readi_model else ""
        label += ")"
        print(f"\n(Loading {label} — transformer NER is slow; models download once.)")
        semantic = get_redactor(
            PIIConfig(
                enabled=True,
                mode="semantic",
                readi_extractor=args.readi_extractor,
                readi_model=args.readi_model,
                readi_language=args.readi_language,
            )
        )
        _print(label, score(records, semantic))

    print("\nNotes:")
    print("  - 'recall on CPEX-supported types only' is the fair number for the regex backend.")
    print("  - regex (CPEX) excels at structured PII at precision ~1.0 but has no NER, so it")
    print("    misses names/addresses; semantic (READI) catches those free-form entities.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
