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

### Example: a learned guideline, before and after

Memories are usually *guidelines* an agent learns. Here's one that happens to
carry PII — the redaction runs at the write choke-point, so this is what
actually gets stored:

```
INPUT guideline
  trigger: When a user asks how to resolve a billing dispute
  content: For billing disputes, call account owner Dana Whitfield at
           415-555-0199 or dana.whitfield@acme.com; verify the card on file
           (4111 1111 1111 1111) before issuing a refund over $500.

STORED guideline (PII removed)
  trigger: When a user asks how to resolve a billing dispute
  content: For billing disputes, call account owner [REDACTED] at [REDACTED] or
           [REDACTED]; verify the card on file ([REDACTED]) before issuing a
           refund over $500.
```

The *reusable* knowledge — how to handle a billing dispute, the $500 threshold —
is kept intact; only the person, phone, email, and card are removed.

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

### Why each memory was kept, flagged, or removed

The engine records *why* it acted (or didn't) on every memory — so a decision is
always explainable and auditable:

```
Decision & why (how the engine derived each outcome):
    FLAG   1 [guideline]  STALE: deploy only on Fridays
           why: not accessed in 200d ≥ rule 'unused-guidelines'
    KEEP   2 [guideline]  FRESH: prefer uv over pip for installs
           why: idle 0d < 90d
    DELETE 3 [guideline]  DERIVED from old session   [derived from session T1]
           why: its source session T1 was deleted → provenance cascade
    DELETE 4 [trajectory] SESSION transcript of an old support chat
           why: created 400d ago ≥ rule 'old-sessions'
```

Note memory **3**: it's only a few days old and would normally be *kept* — it's
removed **not for its own age** but because the session it was derived from
expired, and provenance links the two.

### When retention is useful (scenarios)

| Scenario | Rule | Example → decision |
|---|---|---|
| **Data minimization / compliance** — regulations cap how long you retain data | `max_age_days`, action `delete` | a 400-day-old session → **deleted** (over a 365-day cap) |
| **Right-to-be-forgotten / session expiry** — a session is deleted; everything learned *from* it must go too | session `delete` + `cascade_derived` | delete session T1 → its derived guideline **3** is **deleted too** (provenance cascade) |
| **Memory hygiene / drift** — guidelines nobody uses anymore accumulate and mislead | `max_unused_days` | a guideline unused for 200 days → **flagged** for review |
| **Human-in-the-loop** — don't hard-delete; let a person confirm first | action `flag` | flag (marker written), reviewed, then purged in a later pass |

The "unused" signal comes from `metadata.last_accessed`, which
`EvolveClient.record_access(namespace, ids)` stamps from your recall path;
absent that, it falls back to `created_at`.

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

**Talking points (synthetic set)**
- On **structured PII** (email, phone, SSN, card, IP) recall is **1.00** with
  **zero false positives** — it removes all of it and mangles nothing.
- **Names/addresses leak** out of the box (regex has no NER): that's the 0.68
  overall recall. Adding `custom_patterns` for names lifts recall to **0.91** —
  the honest mitigation, and the case for a `semantic` backend.

### Against a real corpus (ai4privacy/pii-masking-200k)

```bash
uv run --extra pii --extra bench python examples/pii_benchmark.py \
    --dataset ai4privacy/pii-masking-200k --limit 1000
```

```
== CPEX regex — structured entities only ==  (1000 real English records)
  recall=0.11  precision=1.00   record-level leak rate=0.99
  recall on CPEX-supported types only=0.71  (over 393 spans)
    email        84/84    recall=1.00
    ip_address  146/146   recall=1.00
    phone        31/67    recall=0.46
    ssn          15/33    recall=0.45
    credit_card   2/63    recall=0.03
    (firstname, lastname, street, dob, iban, … all 0.00 — no CPEX detector)
```

**Why the real-data numbers matter (and differ from the synthetic 1.00s):**
- **email / IP stay perfect** (1.00) — well-defined formats.
- **phone & SSN drop to ~0.45** — real data has many formats (international
  phones, varied SSN styling) the regex doesn't cover.
- **credit_card collapses to 0.03** — ai4privacy's card numbers are bare,
  mostly **non-Luhn-valid** 16-digit strings; CPEX's detector is strict (Luhn +
  grouping), so it (correctly) rejects them. This is as much a dataset-format
  artifact as a CPEX limit — a reminder that **benchmark numbers depend on the
  corpus's formatting conventions**.
- **Overall recall 0.11 / leak 0.99** because CPEX targets ~5 of ai4privacy's
  ~42 PII types; the *fair* number is **"supported-types recall" = 0.71**.

**Takeaway:** strong, zero-false-positive removal of well-formatted structured
PII; real-world format variety and untargeted types (names, addresses) are where
you need `custom_patterns`, broader regex, or the semantic backend below.

### Two backends: regex (CPEX) vs. semantic (IBM READI)

`pii.mode: semantic` swaps the CPEX regex detector for **IBM READI**
(`readi-privacy`) — a transformer NER (spaCy `en_core_web_trf`) + dictionary
ensemble that catches free-form PII (names, locations, dates) regex can't.

```bash
uv run --extra pii --extra readi python examples/pii_benchmark.py \
    --dataset ai4privacy/pii-masking-200k --limit 150 --mode both
```

Same 150 real records, both backends (semantic uses READI's default English NER,
spaCy `en_core_web_trf`):

| Metric | regex (CPEX) | semantic (READI, `en_core_web_trf`) |
|---|---|---|
| Overall recall | 0.12 | **0.45** |
| Precision | 1.00 | 1.00 |
| Record-level leak rate | 0.99 | **0.87** |
| firstname recall | 0.00 | **0.93** |
| lastname recall | 0.00 | **0.92** |
| middlename | 0.00 | **1.00** |
| city / county / state | 0.00 | 0.60 / 1.00 / 0.92 |
| street / zipcode | 0.00 | 0.70 / 0.33 |
| dob / date / url | 0.00 / 0.20 / 0.00 | 0.88 / 0.70 / 1.00 |
| email / ip_address | 1.00 / 1.00 | 1.00 / 1.00 |

**Talking points**
- Semantic ~**4×** the overall recall, driven entirely by the free-form entities
  regex has no detector for — **names jump 0.00 → ~0.92**.
- **Precision stays 1.00** for both — neither over-redacts.
- Both still miss the same hard structured cases (non-Luhn cards, crypto,
  passwords), so leak rate is lower but not zero — defense-in-depth, not a silver
  bullet. READI is **far heavier** (torch + spaCy transformer; ~450MB model,
  seconds per batch) — the classic precision/speed vs. coverage trade-off.

The harness also takes `--data PATH` for a local JSONL corpus, `--dataset` for
any ai4privacy-style HF id, and `--mode regex|semantic|both`.

### Full-sized runs: English (43k) and a dedicated Japanese model (10k)

Run at scale, 4 processes in parallel, with runtimes.

**English — `ai4privacy/pii-masking-200k`, 43,501 records** (~65 min, both modes):

| Metric | regex (CPEX) | semantic (READI, `en_core_web_trf`) |
|---|---|---|
| Overall recall | 0.12 | 0.47 |
| Precision | 1.00 | 0.99 |
| Leak rate | 0.99 | 0.86 |
| firstname / lastname / middlename | 0.00 / 0.00 / 0.00 | **0.92 / 0.97 / 0.94** |
| city / county / state | 0.00 | 0.88 / 0.90 / 0.86 |
| email / ip | 1.00 / 1.00 | 1.00 / 0.95 |
| phone / ssn / credit_card | 0.46 / 0.22 / 0.04 | 0.60 / 0.43 / 0.03 |

**Japanese — WikiANN (`ja`), 10,000 records** (~8–9 min each, run in parallel).
English model vs a language-matched Japanese spaCy model, selected purely via
the pluggable `readi_extractor` / `readi_model` config:

```bash
uv run --extra readi --extra bench python examples/pii_benchmark.py \
    --dataset unimelb-nlp/wikiann --dataset-config ja --dataset-format wikiann \
    --split validation --limit 10000 --mode semantic \
    --readi-extractor spacy --readi-model ja_core_news_trf --readi-language ja
```

| Metric | English model (`en_core_web_trf`) | Japanese model (`ja_core_news_trf`) |
|---|---|---|
| Overall recall | **0.00** | **0.87** |
| person (per) | 0.00 | **0.93** |
| location (loc) | 0.00 | 0.89 |
| organization (org) | 0.01 | 0.79 |
| Precision | 0.12 | 0.49\* |
| Leak rate | 1.00 | 0.16 |

\* WikiANN labels only PER/ORG/LOC, but the spaCy model also emits
DATE/PRODUCT/MONEY/etc.; those extra detections count as "false positives" in
this scoring, so 0.49 understates real precision. For redaction it means some
over-redaction, not leaked PII.

**Takeaways**
- Semantic NER buys ~4× recall on free-form PII (names, locations) at ~1.0
  precision on English — but it is **language-bound**: the English model scores
  **0.00** on Japanese.
- A **language-matched model recovers it entirely** — `ja_core_news_trf` lifts
  Japanese person recall from 0.00 to **0.93** — and swapping it in is a one-line
  config change.
- Structured PII (cards, phones, SSNs) stays the weak spot for *both* engines —
  the case for a **hybrid** deployment: regex (CPEX) for structured PII at high
  precision, plus a language-matched NER for names/locations.

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

For the semantic (NER) backend, install the `[readi]` extra and set
`mode="semantic"` (catches names/locations; downloads the spaCy transformer on
first use). The detection engine is pluggable, using the extractors READI ships,
and defaults to READI's spaCy-English pipeline:

```python
PIIConfig(enabled=True, mode="semantic")                          # default: READI spaCy-English
PIIConfig(enabled=True, mode="semantic", readi_extractor="presidio")  # Microsoft Presidio (MIT)
PIIConfig(enabled=True, mode="semantic",                          # any spaCy pipeline, e.g. Japanese
          readi_extractor="spacy", readi_model="ja_core_news_trf", readi_language="ja")
PIIConfig(enabled=True, mode="semantic",                          # any HF pipeline("ner") model
          readi_extractor="hf", readi_model="julian-schelb/roberta-ner-multilingual")
```

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

- **Two backends, different trade-offs.** `mode: regex` (CPEX) is fast and
  precise on structured PII but has no NER (names need `custom_patterns`).
  `mode: semantic` (IBM READI) adds transformer NER for free-form PII (names,
  locations, dates) at ~4× recall, but is heavy (torch + spaCy, ~450MB model)
  and still misses some hard structured cases. Neither alone is a silver bullet;
  a hybrid (regex for precision + NER for coverage) is the natural production
  setup.
- **Plugin scope.** Redaction is mirrored into the evolve-lite plugin, but the
  wired save path only ships to the `claw-code` variant; claude/codex/bob use
  native memory. The package is the guarantee for those flows.
- **Retention is package-side** in this PoC; a plugin-side equivalent over the
  `.evolve/` store is a follow-up.

---

## Reviewing this PoC (for agents)

This was built verification-first: every claim above is backed by code you can
run. Review it the same way — **reproduce, don't trust the prose.** Ideally spawn
a subagent per area (PII, retention, benchmark, plugin), have each verify by
*running*, and adversarially spot-check anything surprising.

**Setup:** `uv sync --extra pii --extra readi --extra bench`

**1. PII redaction — that PII never reaches storage.**
- Run `examples/pii_redaction_demo.py`; confirm OUT lines contain no PII and the
  non-PII sentence is byte-identical.
- *Adversarial:* don't trust the printout — the redaction is at the backend
  choke-point (`BaseEntityBackend.update_entities`), so write an entity with PII
  via `EvolveClient`, then grep the on-disk namespace JSON (filesystem backend)
  for the raw PII — it must be **absent**. Confirm a disabled config is a true
  no-op (`NullRedactor`) and a missing extra degrades gracefully (no crash).

**2. Retention — flag/delete/cascade + dry-run safety.**
- Run `examples/retention_demo.py`; confirm stale→flagged (not deleted), old
  session + its derived memory both deleted (provenance cascade), fresh
  untouched.
- *Adversarial:* re-run with `dry_run=True` and confirm the store is byte-identical
  after (nothing mutated). Confirm cascade only removes memories whose
  `metadata.source_task_id` matches the deleted trajectory's `trace_id`.

**3. Benchmark — reproduce the numbers.**
- Synthetic: `examples/pii_benchmark.py`. Real: `--dataset ai4privacy/pii-masking-200k --mode both`.
  Japanese: `--dataset unimelb-nlp/wikiann --dataset-config ja --dataset-format wikiann --mode semantic`.
  Re-run and confirm recall/precision match the tables within noise.
- *Adversarial:* surprising numbers deserve a spot-check. E.g. `credit_card`
  recall 0.03 on ai4privacy — verify it's *real* (values are non-Luhn) and not a
  harness bug by running CPEX's detector on a few gold card strings directly.
- Check the scorer: spans match by overlap; precision counts detections that
  overlap no gold span. Confirm gold offsets are correct (synthetic generates
  them; wikiann derives them from BIO tags).

**4. Plugin parity.** Run `uv run python plugin-source/build_plugins.py check` —
must exit 0 (rendered `platform-integrations/` matches `plugin-source/`). Confirm
`plugin-source/lib/pii.py` is stdlib-only.

**Be skeptical of:** numbers that look too clean (synthetic 1.00s hide real-world
format sensitivity); "semantic is strictly better" (it's ~4× recall on free-form
PII but heavier, English-default, and still misses hard structured cases); and
any claim here without a runnable command behind it.
