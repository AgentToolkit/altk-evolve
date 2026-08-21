# Compliance demos — PII redaction, secrets, & retention on the memory hook seam (issue #275)

This is a **presentation branch**: it is `origin/main` plus a small set of demo
files. It shows how to USE the now-merged memory hook seam for the two
enterprise asks in
[issue #275](https://github.com/AgentToolkit/altk-evolve/issues/275), plus a
third, related method (structured secrets):

1. **PII must never be saved into memory.** → PII redaction plugins on the seam.
2. **Memory must support data-retention policies** (delete/flag by age or
   disuse; delete old sessions and the memories derived from them). → the
   retention engine + `evolve retention run`.
3. **Credentials/tokens must not be saved either** (adjacent to #275). → the
   structured-secrets plugin.

All three are wired the same way: as **plugins on the memory hook seam** (merged
in [PR #295](https://github.com/AgentToolkit/altk-evolve/pull/295)). The seam is
always live; behavior is decided entirely by which plugins you configure. Hook
points sit at the backend read/write choke points and before every LLM call, so
a redactor fires on `memory_pre_write` (before anything is persisted) and on
`llm_pre_call` (before any completion call site sees the content).

Three redaction methods ship, and they compose (defence in depth):

| Method | Plugin / extra | Catches | Misses |
|---|---|---|---|
| **Regex PII** | `PIIFilterMemoryPlugin` · `[pii-regex]` | structured identifiers (email, phone, SSN, card, IP) at precision ~1.00 | **names** and non-Western formats (no NER) |
| **Semantic PII** | `ReadiSemanticPIIPlugin` · `[pii-semantic]` | free-form **names**/locations via IBM READI NER | language-bound; needs a language-matched model; heavy |
| **Structured secrets** | `SecretsFilterMemoryPlugin` · `[secrets]` | credentials/tokens (AWS keys, GitHub/Slack tokens, private keys) | entropy/heuristic secrets (off by default — over-redact) |

---

## Install

```bash
# from the repo root, on branch feat/275-pii-retention-poc
uv sync --extra hooks --extra pii-regex --extra pii-semantic --extra secrets
# then always run with --no-sync so uv doesn't re-resolve:
uv run --no-sync python examples/<name>.py
```

`[pii-semantic]` downloads a spaCy transformer (~460MB) on first use. On
macOS/Apple-Silicon it fails closed on the MPS backend — see
[Limitations](#limitations). If you only want the mac-friendly path, drop
`--extra pii-semantic` and install `--extra hooks --extra pii-regex --extra secrets`.

---

## How to use it — two equivalent ways to configure the seam

### 1. YAML (`evolve hooks init`) — edit YAML to change behavior

```bash
evolve hooks init          # scaffolds ./evolve.hooks.yaml
```

Evolve **auto-discovers** this file (`$EVOLVE_HOOKS_CONFIG` → `./evolve.hooks.yaml`
→ `~/.config/evolve/hooks.yaml`), so every `EvolveClient` in the project picks it
up with no code change. The scaffold ships the **semantic** plugin active and the
regex + secrets plugins commented out. Changing posture is a YAML edit — comment
a plugin out (or set `mode: disabled`) to turn it off; uncomment the regex and
secrets blocks to run all three together. See
[`examples/hooks_plugins.yaml`](examples/hooks_plugins.yaml) for the full,
annotated plugin reference.

`mode: sequential` + `on_error: fail` are load-bearing on every redactor:
`sequential` (not `transform`) is what lets a redactor **block**, not just
redact (CPEX silently downgrades `continue_processing=False` → `True` in
transform mode), and `on_error: fail` makes a crashing/timing-out redactor halt
the write rather than pass unredacted content through (fail-closed).

### 2. Code-first (`HooksConfig(plugins=[...])`) — the equivalent

```python
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.hooks import HookPluginSpec, HooksConfig
from altk_evolve.frontend.client.evolve_client import EvolveClient

config = EvolveConfig(hooks=HooksConfig(plugins=[
    HookPluginSpec(
        name="pii_filter_memory",
        kind="altk_evolve.hooks.plugins.pii.PIIFilterMemoryPlugin",
        hooks=["memory_pre_write", "llm_pre_call"],
        mode="sequential", priority=10, on_error="fail",
        config={"detect_email": True, "detect_ssn": True, "detect_phone": True,
                "detect_credit_card": True, "detect_ip_address": True,
                "redaction_text": "[REDACTED]"},
    ),
    HookPluginSpec(
        name="readi_semantic_pii",
        kind="altk_evolve.hooks.plugins.readi.ReadiSemanticPIIPlugin",
        hooks=["memory_pre_write", "llm_pre_call"],
        mode="sequential", priority=10, on_error="fail",
        config={"readi_extractor": "default", "redaction_text": "[REDACTED]"},
    ),
]))
client = EvolveClient(config)   # every update_entities(...) is now redacted before storage
```

`HooksConfig(plugins_yaml="path.yaml")` points at a YAML file instead; explicit
`plugins`/`plugins_yaml` override auto-discovery.

---

## Demo 1 — PII never lands in memory (`examples/pii_redaction_demo.py`)

Writes a fake persona (name + email/phone/SSN/card/IP) with **both** the regex
and semantic PII plugins configured, then reads it back FROM STORAGE. The
headline: the regex method removes every structured identifier, but only the
**semantic** method removes the *name*.

```bash
uv run --no-sync python examples/pii_redaction_demo.py
```

Expected output on a host **without** `[pii-semantic]` (e.g. this darwin/MPS
machine — regex-only path, name deliberately survives to make the point):

```
PII redaction on the memory hook seam (memory_pre_write choke point)
  regex    method ([pii-regex])    : ACTIVE  -> structured identifiers
  semantic method ([pii-semantic]) : absent -> names will NOT be caught

What the agent tried to remember  ->  what actually got stored

  IN : Primary contact is Dana Whitfield, who replies fastest at dana.whitfield@example.com.
  OUT: Primary contact is Dana Whitfield, who replies fastest at [REDACTED].

  IN : Dana Whitfield asked for a callback on 415-555-0199 before noon Friday.
  OUT: Dana Whitfield asked for a callback on [REDACTED] before noon Friday.

  IN : For billing we have SSN 123-45-6789 and card 4111 1111 1111 1111 on file.
  OUT: For billing we have SSN [REDACTED] and card [REDACTED] on file.

  IN : Last successful login came from IP 192.168.10.42 on the office network.
  OUT: Last successful login came from IP [REDACTED] on the office network.

  IN : Remember: the customer prefers metric units and a dark UI theme.
  OUT: Remember: the customer prefers metric units and a dark UI theme.

OK  — all 5 structured PII values (email, phone, SSN, card, IP)
      were replaced with inert filler before storage [regex method].
NOTE — the NAME 'Dana Whitfield' is STILL PRESENT — regex has no NER.
       Install [pii-semantic] and re-run to catch names too (defence in depth).

      The non-PII memory (units + theme preference) was preserved verbatim.
```

With `[pii-semantic]` installed and running (a CPU/CUDA host), the same run
prints `semantic method ([pii-semantic]) : ACTIVE`, the name lines read
`OUT: Primary contact is [REDACTED], who replies fastest at [REDACTED].`, and
the final assertion becomes:

```
OK  — the free-form NAME 'Dana Whitfield' was also removed [semantic method];
      regex alone has no NER and would have left it in place.
```

**Talking points**
- Redaction is at `memory_pre_write`, the backend write choke point — the OUT
  lines are the *real persisted content*, read back from the filesystem store.
- Structured identifiers (email, phone, SSN, card, IP) → regex, precision ~1.00,
  non-PII sentence survives verbatim (surgical, not lossy).
- The **name** is the semantic method's job; regex has no NER. Running both is
  defence in depth.

---

## Demo 2 — Structured secrets never land in memory (`examples/secrets_demo.py`)

A fake AWS key and GitHub token are scrubbed on write AND on LLM egress; a
non-secret policy string survives.

```bash
uv run --no-sync python examples/secrets_demo.py
```

```
Structured-secrets redaction on the memory hook seam

  IN (agent tried to remember):
    CI notes: the pipeline uses AWS key AKIAIOSFODNN7EXAMPLE and GitHub token ghp_1234567890abcdefghijklmnopqrstuvwxyz; policy is to deploy only on Fridays.  # pragma: allowlist secret

  OUT (what actually got stored):
    CI notes: the pipeline uses AWS key [REDACTED] and GitHub token [REDACTED]; policy is to deploy only on Fridays.

  LLM egress (dispatch_llm_pre_call):
    Use [REDACTED] to list buckets

OK  — the AWS key and GitHub token were redacted on write AND on LLM egress;
      the non-secret policy ('deploy only on Fridays') was preserved verbatim.
```

**Talking points**
- Orthogonal to the PII methods: catches *credentials/tokens*, not people.
- Same seam, same `memory_pre_write` + `llm_pre_call` wiring — it chains
  alongside a PII method.
- Entropy/heuristic detectors (generic API keys, JWT-shaped, hex/base64) are OFF
  by default because they over-redact a memory corpus; opt in deliberately.

---

## Demo 3 — All the seam plugins together (`examples/hooks_demo.py`)

The end-to-end seam demo: a metadata normalizer (stamps `trace_id`/`created_at`
on write), an access stamp (`last_accessed` on read), and the regex PII filter,
all at once.

```bash
uv run --no-sync python examples/hooks_demo.py
```

```
stored content:   Customer Dana Whitfield, email [REDACTED], SSN [REDACTED].
stored metadata:  {'created_at': '2026-07-24T17:34:00.576531+00:00', 'task_id': 'task-0042', 'trace_id': 'task-0042'}
last_accessed:    2026-07-24T17:34:00.577754+00:00
llm egress:       Summarize: call [REDACTED] or mail [REDACTED]
```

Note `trace_id` copied from `task_id` (normalizer), `last_accessed` stamped by
the read (access stamp), and the PII redacted on both storage and LLM egress.

---

## Demo 4 — Retention policies & provenance cascade (`examples/retention_demo.py`)

Seeds a realistic store (stale/fresh/recently-read guidelines, an old session,
and a memory derived from it) and runs a policy from
[`examples/retention.example.yaml`](examples/retention.example.yaml): flag by
disuse, delete by age, and cascade-delete memories derived from a deleted
session.

```bash
uv run --no-sync python examples/retention_demo.py
# or run a policy against a live namespace:
evolve retention run --policy examples/retention.example.yaml            # dry run
evolve retention run --policy examples/retention.example.yaml --apply    # mutate
```

```
Seeded memories:
    1 [guideline] STALE: deploy only on Fridays
    2 [guideline] FRESH: prefer uv over pip for installs
    3 [guideline] USED: the flaky test needs a retry   (last read ...)
    4 [guideline] DERIVED from the old session: always run ruff
    5 [trajectory] SESSION transcript of an old support chat

APPLY:
    DELETE  5   trajectory reason=age          rule=old-sessions
    DELETE  4   guideline  reason=cascade:T1   rule=old-sessions
    FLAG    1   guideline  reason=unused       rule=unused-guidelines

Store after apply:
    1 [guideline] STALE: deploy only on Fridays <-- FLAGGED
    2 [guideline] FRESH: prefer uv over pip for installs
    3 [guideline] USED: the flaky test needs a retry
```

**Talking points**
- **Flag** is non-destructive (`retention_flagged_at` marker); **delete** is
  hard removal. **Dry-run** is the default and mutates nothing.
- **Cascade**: deleting the old *session* also deletes the guideline derived
  from it (linked by provenance: `metadata.source_task_id == trace_id`), even
  though that guideline is young.
- The "unused" signal is `metadata.last_accessed`, stamped by `AccessStampPlugin`
  on `memory_post_read` (or `EvolveClient.record_access`). Absent it, disuse
  falls back to `created_at` and the engine warns — the guideline read yesterday
  survives *because* it was stamped, the never-read stale one is flagged.

---

## Measured results (retained — these measure detector/model behavior)

These numbers are independent of how the detectors are wired into the write
path, so they carry forward unchanged from the earlier PoC. Reproduce with
[`examples/pii_benchmark.py`](examples/pii_benchmark.py).

### English — `ai4privacy/pii-masking-200k`, 43,501 records (both methods)

| Metric | regex (`[pii-regex]`) | semantic (READI, `en_core_web_trf`) |
|---|---|---|
| Overall recall | 0.12 | **0.47** |
| Precision | 1.00 | 0.99 |
| Leak rate | 0.99 | 0.86 |
| firstname / lastname / middlename | 0.00 / 0.00 / 0.00 | **0.92 / 0.97 / 0.94** |
| city / county / state | 0.00 | 0.88 / 0.90 / 0.86 |
| email / ip | 1.00 / 1.00 | 1.00 / 0.95 |
| phone / ssn / credit_card | 0.46 / 0.22 / 0.04 | 0.60 / 0.43 / 0.03 |

The regex method is precise on structured identifiers (email/IP = 1.00) but
**0.00 on names** — it catches identifiers, not people. Semantic ~4× the overall
recall, driven entirely by the free-form entities regex has no detector for
(**names 0.00 → ~0.92**), at precision ~1.00 (neither over-redacts).

### Japanese — `ai4privacy/pii-masking-openpii-1.5m` (`ja`), 2,000 records

A **language-matched** model is decisive. Same rows, three engines:

| PII type | regex (`[pii-regex]`) | English NER (`en_core_web_trf`) | Japanese NER (`ja_core_news_trf`) |
|---|---|---|---|
| **Overall recall** | 0.03 | 0.15 | **0.92** |
| **Precision** | 0.99 | 0.99 | 0.93 |
| email | 0.09 | 0.09 | 0.94 |
| phone | 0.13 | 0.47 | 0.99 |
| zipcode | 0.00 | 0.46 | 1.00 |
| credit card | 0.02 | 0.03 | 1.00 |
| social / ID number | 0.46 | 0.46 | 1.00 |
| given / surname | 0.00 / 0.00 | 0.07 / 0.04 | 0.82 / 0.96 |
| city / street | 0.00 / 0.00 | 0.00 / 0.00 | 1.00 / 0.95 |

```bash
uv run --no-sync python examples/pii_benchmark.py \
    --dataset ai4privacy/pii-masking-openpii-1.5m --language ja --split validation \
    --limit 2000 --mode both --readi-extractor spacy --readi-model ja_core_news_trf --readi-language ja
```

- On real Japanese PII, regex is largely ineffective (0.03) — Western-format
  patterns don't fit no-space Japanese text (even email → 0.09).
- The English NER barely helps (0.15) — it can't read Japanese names/places.
- A language-matched model catches **~92% at ~93% precision** — a one-line config
  change (`readi_extractor="spacy", readi_model="ja_core_news_trf"`).
- Bonus: this benchmark caught a real bug — READI's char offsets vs cpex's byte
  offsets; byte offsets broke span scoring on multibyte text (precision looked
  like 0.31; fixed to 0.99). Redaction itself was always correct.

---

## Defence in depth — run all three together

The three methods are complementary, not either/or. The recommended production
posture is to run **regex + semantic + secrets** at once: regex for structured
identifiers, semantic (NER) for names and free-form entities, secrets for
credentials/tokens. They chain on the same `memory_pre_write` + `llm_pre_call`
hooks; each is a high-precision floor, and together they close each other's
gaps. Enable all three by uncommenting the regex and secrets blocks in the
`evolve hooks init` scaffold and leaving READI active — a YAML edit, no code
change.

---

## Limitations

- **Regex PII is blind to names and non-Western formats.** No NER: names,
  addresses, and no-space scripts leak (0.00 recall on names; 0.03 overall on
  Japanese). Use it for structured identifiers, and pair it with the semantic
  method for coverage.
- **Semantic PII is language-bound and needs a language-matched model.** READI's
  default English pipeline barely helps on Japanese (0.15); a matched model
  reaches 0.92. It is also heavy (torch + spaCy transformer, ~460MB weights on
  first use).
- **macOS / Apple-Silicon: READI fails closed on MPS.** spaCy-curated-transformers
  places the model on torch's MPS backend, which binds to the first thread that
  touches it; the seam's sync bridge dispatches on a worker thread, so the model
  raises *"Placeholder storage has not been allocated on MPS device!"* and, under
  `on_error: fail`, blocks the write. **This machine is darwin**, so the semantic
  e2e path could not be run here — `examples/pii_redaction_demo.py` catches the
  failure and falls back to regex-only with a printed note. **Regex and secrets
  are Rust/regex and unaffected — regex is the mac-friendly PII method.** The
  caveat does not affect CPU/CUDA hosts.
- **Secrets and regex PII are high-precision floors, not proof of absence.** Both
  are pattern-based with no verification; entropy/heuristic secret detectors are
  off by default because they over-redact a memory corpus.

---

## Reviewer note

This branch is **`main` plus demo files only** — a presentation branch, not a
history-preserving merge. The diff vs `main` is exactly:

- `examples/pii_redaction_demo.py` (new)
- `examples/secrets_demo.py` (new)
- `DEMO.md` (new)

Everything the demos exercise — the hook seam, the three redaction plugins, the
retention engine, `examples/hooks_demo.py` / `examples/retention_demo.py` /
`examples/pii_benchmark.py` — is already on `main`. Reproduce, don't trust the
prose: run each command above and confirm the output. The semantic e2e path
needs a CPU/CUDA host (see Limitations); everything else runs on this darwin
machine.
