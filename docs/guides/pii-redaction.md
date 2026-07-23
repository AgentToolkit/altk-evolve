# PII Redaction

Evolve redacts PII at the [memory hook seam](memory-hooks.md): before entities are persisted (`memory_pre_write`) and before messages leave the process for an LLM (`llm_pre_call`). PII detection ships as **two methods** — regex and semantic — each its own plugin and extra. They are not either/or: **running both is the recommended defence-in-depth default** (regex for structured identifiers, semantic for names and other free-form entities), and enabling or disabling either is a YAML edit, not a code change.

| Method | Plugin | Engine | Catches | Extra |
|---|---|---|---|---|
| Regex (lighter, deterministic) | `PIIFilterMemoryPlugin` | `cpex-pii-filter` (Rust regex) | Structured identifiers: email, phone, SSN, card, IP, IBAN, … | `[pii-regex]` |
| Semantic (more powerful, NER) | `ReadiSemanticPIIPlugin` | IBM READI (`readi-privacy`, NER) | Free-form entities: **names**, locations, organizations | `[pii-semantic]` |

Both extras are PII redaction; the name states the *method*, not "PII vs not-PII". `[pii]` remains as a backward-compatible alias for `[pii-regex]` so existing installs keep working with the same lightweight behaviour — it does **not** pull the heavy semantic dependencies.

## Why semantic redaction exists

Regex redaction is excellent at what it does and blind to everything else. Measured with `examples/pii_benchmark.py` on 200 rows of `ai4privacy/pii-masking-200k` (English):

| | regex | semantic (READI, `en_core_web_trf`) |
|---|---|---|
| overall span recall | **0.13** | **0.48** |
| precision | 1.00 | 1.00 |
| recall on regex-supported types | 0.72 | 0.77 |
| `firstname` recall | **0.00** | **0.92** |
| `lastname` recall | **0.00** | **0.94** |
| `middlename` recall | **0.00** | **1.00** |

The full-corpus figures are the same story (regex 0.12 recall / 1.00 precision; READI 0.47 / 0.99). Neither backend is a compliance guarantee — 0.48 recall means over half the labeled spans still survive, largely categories neither engine targets (`password`, `vehiclevin`, `phoneimei`, crypto addresses). What the numbers do establish: **regex redaction cannot redact people.** If your memory store holds free-text about humans, regex alone leaves the names in.

This is why the two are a chain, not a choice: run the regex method for structured identifiers at precision 1.00 **and** the semantic method for names. Neither replaces the other — the recommended default is both.

### Language matters more than model size

On Japanese (`ai4privacy/pii-masking-openpii-1.5m`, 2,000 rows):

| | overall recall | precision |
|---|---|---|
| regex | 0.03 | — |
| semantic, English model | 0.15 | — |
| semantic, `ja_core_news_trf` (language-matched) | **0.92** | ~0.99 |

Matching the model to the language is decisive — a bigger English model is not a substitute. Set `readi_extractor: spacy` with a language-matched pipeline.

Note on multibyte text: spans are **character** offsets end to end. `cpex-pii-filter`'s Rust engine reports *byte* offsets, and treating those as character offsets mis-places every span in a non-Latin script — it read as precision 0.31 on Japanese instead of 0.99. The benchmark converts them (`byte_spans_to_char_spans`), and both the conversion and the character-offset splice are pinned by regression tests in `tests/unit/test_readi_redaction_core.py`.

## Choosing a model

`readi_extractor` selects which of READI's shipped extractors runs:

- **`default`** — READI's own PII pipeline (spaCy `en_core_web_trf` plus READI's identifier extractors). English only. Good default for English deployments.
- **`spacy`** + `readi_model` — any spaCy pipeline. **This is the multilingual path.** spaCy's per-language pipelines are published by Explosion under MIT, cover the large-economy languages, and are versioned and reproducible — the right kind of dependency for a compliance control. Examples: `ja_core_news_trf`, `de_core_news_lg`, `es_core_news_lg`, `fr_core_news_lg`, `zh_core_web_trf`, `pt_core_news_lg`, `it_core_news_lg`, `ru_core_news_lg`. NER entities only (no structured identifiers) — pair with the regex plugin.
- **`hf`** + `readi_model` — any Hugging Face `pipeline("ner")` model. Maximum reach (multilingual XLM-R NER covers languages spaCy does not), but **check the licence and the publisher**: prefer models from reputable organizations with a commercial-friendly licence over individually-authored community uploads, which carry no maintenance or provenance guarantees.
- **`presidio`** — Microsoft Presidio (NER plus structured recognizers). See the limitation below before choosing it for non-English.

Guidance in one line: English → `default`; other languages → `spacy` with a language-matched model; unsupported language → a reputable-org `hf` model.

## Cost and latency

Be honest with yourself about the trade: regex is a Rust pass over the string, effectively free. NER is a transformer forward pass per redacted string, on CPU unless you have an accelerator — orders of magnitude slower, and it runs on **every** memory write and **every** LLM call. The first call also downloads model weights (~460MB for `en_core_web_trf`), cached on disk thereafter, and each loaded pipeline holds its weights in memory for the process lifetime.

If that cost is unacceptable on the write path, a reasonable posture is: regex on both hooks (cheap, high precision), semantic on `llm_pre_call` only (bounded call volume), or a smaller non-transformer spaCy pipeline (`*_core_news_lg`) at some recall cost.

## Enabling redaction

The fastest path scaffolds a project-local, auto-discovered config:

```console
$ evolve hooks init            # writes ./evolve.hooks.yaml
$ pip install 'altk-evolve[pii-semantic]'
```

`evolve hooks init` ships the **READI semantic plugin active** and the **regex plugin commented out** (both `mode: sequential`, `on_error: fail`). Evolve auto-discovers `./evolve.hooks.yaml` (search order: `$EVOLVE_HOOKS_CONFIG` → `./evolve.hooks.yaml` → `~/.config/evolve/hooks.yaml`), so once the extra is installed, redaction is live — no code change. For **defence-in-depth**, uncomment the regex block so both methods run (regex for structured identifiers, semantic for names); leaving READI active. To run regex only, comment the READI block and uncomment the regex block, and install `[pii-regex]` instead.

> **macOS caveat:** READI's transformer model uses the Apple-Silicon MPS backend, which can fail-closed and block writes from the seam's worker thread (see [Known limitations](#known-limitations)). On macOS for local dev, prefer the regex block, or run READI on CPU/Linux.

## Configuration

Both plugins are configured through their own `config` block in the hook plugin YAML — see [`examples/hooks_plugins.yaml`](https://github.com/AgentToolkit/altk-evolve/blob/main/examples/hooks_plugins.yaml) for the full, annotated reference.

```yaml
- name: readi_semantic_pii
  kind: altk_evolve.hooks.plugins.readi.ReadiSemanticPIIPlugin
  hooks:
    - memory_pre_write
    - llm_pre_call
  mode: sequential     # transform mode can redact but can NEVER block — see memory-hooks.md
  priority: 10
  on_error: fail       # fail-closed: a crashing NER model must not pass PII through
  config:
    readi_extractor: spacy      # default | spacy | hf | presidio
    readi_model: ja_core_news_trf
    readi_language: ja
    redaction_text: "[REDACTED]"
    # redact_metadata defaults to true (matches the regex plugin). Set false only
    # if your metadata holds ids/paths/trace keys that redaction would corrupt.
    redact_metadata: true
```

Enabling or disabling either method is exactly this: comment a block in or out and restart — a compliance posture change with no code change and no redeploy of Evolve itself. The recommended posture is to run **both** methods (regex + semantic) together; you can also run just one where its trade-off fits (see cost and latency below).

`mode: sequential` and `on_error: fail` are load-bearing, not cosmetic. CPEX silently downgrades `continue_processing=False` → `True` in `transform`/`audit` mode, so a redactor registered as `transform` can redact but can never block; and `on_error: fail` is what guarantees a crashing or timing-out redactor halts the operation rather than quietly passing unredacted content through.

## Using the core directly

The redaction logic is a pure, engine-free core — usable without cpex, and testable with a two-line fake detector:

```python
from altk_evolve.hooks.plugins.readi import build_readi_detector, redact_text

detect = build_readi_detector(extractor="default")          # needs [pii-semantic]
redact_text("Dana Whitfield emailed dana@example.com", detect)
# '[REDACTED] emailed [REDACTED]'
```

`build_readi_detector` returns a `SpanDetector` — `text -> iterable of (start, end)` character spans. Anything matching that shape works, so a different NER engine plugs in without touching the redaction logic. `redact_entities` / `redact_messages` apply it to hook payloads; all of them return copies and never mutate their input, per the seam's plugin contract.

`ReadiSemanticPIIPlugin` itself is a **native** hook plugin (see [memory-hooks.md](memory-hooks.md#writing-a-plugin)) — it imports no cpex, subclasses `HookPluginBase`, and returns `payload.replace(...)`; the execution engine sits behind an adapter it never sees. Only its detector needs the `[pii-semantic]` extra, surfaced fail-closed at engine init via `startup_validate`.

## Benchmarking

`examples/pii_benchmark.py` scores any `SpanDetector` against a labeled gold set (synthetic by default; `--dataset` streams an ai4privacy- or WikiANN-style HF corpus with the `[bench]` extra) and reports recall, precision, F1, per-entity recall, and a record-level leak rate.

```bash
uv run --extra pii-regex --extra pii-semantic python examples/pii_benchmark.py --mode both
uv run --extra pii-regex --extra pii-semantic --extra bench python examples/pii_benchmark.py \
    --dataset ai4privacy/pii-masking-200k --limit 200 --mode both
```

Use `--limit`: the full corpus is 43k rows and takes hours under transformer NER.

## Known limitations

- **Presidio is English-only through READI.** READI's `PresidioEntityExtractor` hardcodes `language="en"` in its analyze call (carrying an explicit "this needs to be fixed" note upstream). `readi_language` reaches the spaCy engine configuration but not Presidio itself, so multilingual Presidio needs an upstream fix or a local override. Use `readi_extractor: spacy` for non-English.
- **Apple Silicon / MPS thread affinity.** `spacy-curated-transformers` places transformer pipelines on torch's MPS backend, which binds to the first thread that touches it. The hook seam's sync bridge dispatches on a dedicated thread when an event loop is already running, so a model first used on the main thread raises `Placeholder storage has not been allocated on MPS device!` there — and with `on_error: fail` that blocks the operation. A per-thread model cache does not help (verified). Workarounds on macOS: use a non-transformer pipeline (`en_core_web_lg`), use `readi_extractor: hf`, or build with a CPU-only torch. CPU and CUDA hosts are unaffected.
- **Recall is not a guarantee.** 0.48 overall recall on ai4privacy means these plugins reduce exposure; they do not eliminate it. Categories neither engine targets (passwords, device identifiers, crypto addresses, vehicle IDs) pass through. Treat redaction as defence in depth, not as the control that lets you store regulated data.
- **Metadata is redacted by default.** `redact_metadata: true` is the default so the semantic plugin matches the regex plugin, which round-trips the whole entity through cpex-pii-filter and therefore redacts metadata unconditionally — shipping the two with opposite defaults would be a silent parity gap, and masking more is the fail-safe default for a redactor. The trade-off: metadata can hold ids, paths and trace keys that redaction would corrupt, so a deployment that keys on those should set `redact_metadata: false` deliberately (the regex plugin has no such opt-out).
- **Process-global plugin manager.** As documented in [memory-hooks.md](memory-hooks.md), CPEX's `PluginManager` is a process-wide singleton — a second `EvolveClient` that resolves its own plugins replaces the first client's plugins, which for a redaction plugin means redaction can be silently disabled by unrelated code.
