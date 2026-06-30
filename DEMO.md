# Compliance PoC — PII redaction & data retention (issue #275)

A proof-of-concept for the two enterprise asks in
[issue #275](https://github.com/AgentToolkit/altk-evolve/issues/275):

1. **PII must never be saved into memory.**
2. **Memory must support data-retention policies** (delete/flag by age or
   disuse; delete old sessions and the memories derived from them).

Both are implemented in the `altk_evolve` package; PII redaction is also
mirrored into the evolve-lite plugin. This doc is a self-contained script for
demoing it to others — copy-paste commands and the output to expect.

---

## TL;DR — what was built

| Capability | Where | How it works |
|---|---|---|
| **PII redaction** | `altk_evolve/pii/` + `BaseEntityBackend.update_entities` | Every entity write (CLI, MCP, API, Phoenix sync) passes through one choke-point that scrubs PII *before* it's persisted. Backend = CPEX `cpex-pii-filter` (regex). |
| **Retention engine** | `altk_evolve/retention/` | Age-based and unused-based **flag** or **delete**, plus session retention that **cascade-deletes derived memories** via provenance. |
| **CLI** | `evolve retention run` | Run a policy file against a namespace. Dry-run by default. |
| **Plugin parity** | `plugin-source/lib/pii.py` | Same redaction wired into the evolve-lite save path (stdlib-only, CPEX optional). |

---

## Setup (once)

```bash
# from the repo root, on branch feat/275-pii-retention-poc
uv sync --extra pii        # installs deps + the CPEX PII backend (cpex-pii-filter)
```

---

## Demo 1 — PII never lands in memory

```bash
uv run python examples/pii_redaction_demo.py
```

It writes made-up memories full of fake PII into a throwaway namespace with
redaction enabled, then **reads them back from storage** to prove the PII is
gone (this is the real persisted content, not a cosmetic reprint):

```
Redaction active: CpexRegexRedactor (mask -> [REDACTED])

What the agent tried to remember  ->  what actually got stored

  IN : Primary contact is Dana Whitfield, who replies fastest at dana.whitfield@example.com.
  OUT: Primary contact is [REDACTED], who replies fastest at [REDACTED].

  IN : For billing we have SSN 123-45-6789 and card 4111 1111 1111 1111 on file.
  OUT: For billing we have SSN [REDACTED] and card [REDACTED] on file.

  IN : Last successful login came from IP 192.168.10.42 on the office network.
  OUT: Last successful login came from IP [REDACTED] on the office network.

  IN : Remember: the customer prefers metric units and a dark UI theme.
  OUT: Remember: the customer prefers metric units and a dark UI theme.

OK — all 6 PII items were replaced with inert filler before storage;
     the non-PII memory (units + theme preference) was preserved verbatim.
```

**Talking points**
- Name, email, phone, SSN, card, and IP are all replaced with inert filler.
- The non-PII sentence survives **verbatim** — redaction is surgical, not lossy.
- The fictional *name* is caught via a `custom_patterns` regex (the detector is
  regex-based and has no NER on its own — see Limitations).

---

## Demo 2 — Retention policies & provenance cascade

```bash
uv run python examples/retention_demo.py
```

It seeds a realistic store (a stale guideline, a fresh one, a 400-day-old
session, and a memory *derived* from that session), then runs a policy:

```
Seeded memories:
    1 [guideline] STALE: deploy only on Fridays
    2 [guideline] FRESH: prefer uv over pip for installs
    3 [guideline] DERIVED from old session: always run ruff
    4 [trajectory] SESSION transcript of an old support chat

DRY RUN (what the policy would do):
    DELETE  4   trajectory reason=age          rule=old-sessions
    DELETE  3   guideline  reason=cascade:T1   rule=old-sessions
    FLAG    1   guideline  reason=unused       rule=unused-guidelines
    store still has 4 entities (dry run mutates nothing)

APPLY:
    DELETE  4   trajectory reason=age          rule=old-sessions
    DELETE  3   guideline  reason=cascade:T1   rule=old-sessions
    FLAG    1   guideline  reason=unused       rule=unused-guidelines

Store after apply:
    1 [guideline] STALE: deploy only on Fridays <-- FLAGGED
    2 [guideline] FRESH: prefer uv over pip for installs
```

**Talking points**
- **Flag** is non-destructive (writes a `retention_flagged_at` marker) — the
  issue's "flag for deletion after N days".
- **Cascade**: deleting the old *session* also deletes the memory derived from
  it (linked by provenance: `metadata.source_task_id == trace_id`).
- The fresh guideline is untouched. **Dry-run** changes nothing.

---

## Benchmark — how effective is it?

```bash
uv run --extra pii python examples/pii_benchmark.py
```

Scores the redactor against a labeled gold set (text with known PII spans) and
reports **recall** (did we remove it — `1 - recall` is the span-level leak
rate), **precision** (over-redaction), **F1**, and a record-level **leak rate**.

```
== CPEX regex — structured entities only ==
  records=24  TP=45  FP=0  FN(leaked spans)=21
  recall=0.68  precision=1.00  F1=0.81
  record-level leak rate=0.62
  per-entity recall:
    address        0/6    recall=0.00
    credit_card    6/6    recall=1.00
    email         15/15   recall=1.00
    ip_address     6/6    recall=1.00
    person         0/15   recall=0.00
    phone         12/12   recall=1.00
    ssn            6/6    recall=1.00

== CPEX regex + custom name patterns ==
  recall=0.91  precision=1.00  F1=0.95   record-level leak rate=0.25
```

**Talking points**
- On **structured PII** (email, phone, SSN, card, IP) recall is **1.00** with
  **zero false positives** — it removes all of it and mangles nothing.
- **Names/addresses leak** out of the box (regex has no NER): that's the 0.68
  overall recall. Adding `custom_patterns` for names lifts recall to **0.91** —
  the honest mitigation, and the case for a `semantic` backend.
- The harness takes `--data PATH` (JSONL of `{text, spans}`) to score against a
  real corpus (e.g. ai4privacy/pii-masking-200k or Presidio's evaluator data)
  instead of the built-in synthetic set.

---

## Using it for real

### Enable PII redaction

Programmatically:

```python
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.pii import PIIConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient

client = EvolveClient(EvolveConfig(pii=PIIConfig(
    enabled=True,
    entities=["ssn", "credit_card", "email", "phone", "ip_address"],
    mask_strategy="redact",          # redact | partial | hash | tokenize | remove
    redaction_text="[REDACTED]",
)))
# every client.update_entities(...) is now scrubbed before storage
```

Or via environment (nested env vars): `EVOLVE_PII__ENABLED=true`,
`EVOLVE_PII__MASK_STRATEGY=hash`, …

### Run a retention policy

```bash
evolve retention run --policy retention.example.yaml             # dry run
evolve retention run --policy retention.example.yaml --apply     # mutate
```

See `retention.example.yaml` for the rule format (age / unused / cascade).
To power the "unused" signal, call `EvolveClient.record_access(ns, ids)` from
your recall path — it stamps `metadata.last_accessed`.

---

## Limitations (be upfront)

- **Regex, not NER.** CPEX (`cpex-pii-filter`) detects structured PII
  (emails, phones, SSNs, cards, IPs, …) but not free-form names unless you add
  a `custom_patterns` rule. A semantic/NER backend is a documented seam
  (`pii.mode: semantic`) — CPEX has no embedding detector, so that would plug
  in a library like Presidio.
- **Plugin scope.** Redaction is mirrored into the evolve-lite plugin, but the
  wired save path only ships to the `claw-code` variant; claude/codex/bob use
  native memory. The package is the guarantee for those flows.
- **Retention is package-side** in this PoC; a plugin-side equivalent over the
  `.evolve/` store is a follow-up.
