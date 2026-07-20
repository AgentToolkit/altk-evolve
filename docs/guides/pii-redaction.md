# PII Redaction

Evolve redacts PII at the [memory hook seam](memory-hooks.md): before entities are persisted (`memory_pre_write`) and before messages leave the process for an LLM (`llm_pre_call`). Two redaction plugins ship, and **which one runs is a YAML edit, not a code change**.

| Plugin | Engine | Catches | Extra |
|---|---|---|---|
| `PIIFilterMemoryPlugin` | `cpex-pii-filter` (Rust regex) | Structured identifiers: email, phone, SSN, card, IP, IBAN, … | `[pii]` |
| `ReadiSemanticPIIPlugin` | IBM READI (`readi-privacy`, NER) | Free-form entities: **names**, locations, organizations | `[readi]` |

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

The two chain, and running both is the sensible default: regex for structured identifiers at precision 1.00, NER for names.

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

## Configuration

Both plugins are configured through their own `config` block in the hook plugin YAML — see [`examples/hooks_plugins.yaml`](https://github.com/AgentToolkit/altk-evolve/blob/main/examples/hooks_plugins.yaml), which ships the regex plugin active and the semantic one commented out and ready to enable.

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
    redact_metadata: false      # metadata often holds ids/paths redaction would corrupt
```

Switching regex → semantic is exactly this: comment out one block, uncomment another, restart. That is the seam's selling point — a compliance posture change with no code change and no redeploy of Evolve itself.

`mode: sequential` and `on_error: fail` are load-bearing, not cosmetic. CPEX silently downgrades `continue_processing=False` → `True` in `transform`/`audit` mode, so a redactor registered as `transform` can redact but can never block; and `on_error: fail` is what guarantees a crashing or timing-out redactor halts the operation rather than quietly passing unredacted content through.

## Using the core directly

The redaction logic is a pure, engine-free core — usable without cpex, and testable with a two-line fake detector:

```python
from altk_evolve.hooks.plugins.readi import build_readi_detector, redact_text

detect = build_readi_detector(extractor="default")          # needs [readi]
redact_text("Dana Whitfield emailed dana@example.com", detect)
# '[REDACTED] emailed [REDACTED]'
```

`build_readi_detector` returns a `SpanDetector` — `text -> iterable of (start, end)` character spans. Anything matching that shape works, so a different NER engine plugs in without touching the redaction logic. `redact_entities` / `redact_messages` apply it to hook payloads; all of them return copies and never mutate their input, per the seam's plugin contract.

## Benchmarking

`examples/pii_benchmark.py` scores any `SpanDetector` against a labeled gold set (synthetic by default; `--dataset` streams an ai4privacy- or WikiANN-style HF corpus with the `[bench]` extra) and reports recall, precision, F1, per-entity recall, and a record-level leak rate.

```bash
uv run --extra pii --extra readi python examples/pii_benchmark.py --mode both
uv run --extra pii --extra readi --extra bench python examples/pii_benchmark.py \
    --dataset ai4privacy/pii-masking-200k --limit 200 --mode both
```

Use `--limit`: the full corpus is 43k rows and takes hours under transformer NER.

## Known limitations

- **Presidio is English-only through READI.** READI's `PresidioEntityExtractor` hardcodes `language="en"` in its analyze call (carrying an explicit "this needs to be fixed" note upstream). `readi_language` reaches the spaCy engine configuration but not Presidio itself, so multilingual Presidio needs an upstream fix or a local override. Use `readi_extractor: spacy` for non-English.
- **Apple Silicon / MPS thread affinity.** `spacy-curated-transformers` places transformer pipelines on torch's MPS backend, which binds to the first thread that touches it. The hook seam's sync bridge dispatches on a dedicated thread when an event loop is already running, so a model first used on the main thread raises `Placeholder storage has not been allocated on MPS device!` there — and with `on_error: fail` that blocks the operation. A per-thread model cache does not help (verified). Workarounds on macOS: use a non-transformer pipeline (`en_core_web_lg`), use `readi_extractor: hf`, or build with a CPU-only torch. CPU and CUDA hosts are unaffected.
- **Recall is not a guarantee.** 0.48 overall recall on ai4privacy means these plugins reduce exposure; they do not eliminate it. Categories neither engine targets (passwords, device identifiers, crypto addresses, vehicle IDs) pass through. Treat redaction as defence in depth, not as the control that lets you store regulated data.
- **Metadata is not redacted by default.** `redact_metadata: false` is the default because metadata typically holds ids, paths and trace keys that redaction would corrupt. Turn it on deliberately.
- **Process-global plugin manager.** As documented in [memory-hooks.md](memory-hooks.md), CPEX's `PluginManager` is a process-wide singleton — a second `EvolveClient` constructed with hooks enabled replaces the first client's plugins, which for a redaction plugin means redaction can be silently disabled by unrelated code.
