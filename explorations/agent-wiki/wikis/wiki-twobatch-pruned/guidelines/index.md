---
type: section-index
section: guidelines
verified_at: 2026-06-10
count: 9
atomic: 9
clusters: 0
---

# Guidelines

Atomic, trigger-tagged lessons plus aggregator **cluster pages** that group related variants. Cluster pages have the suffix `__cluster.md` and are recall-preferred — when a cluster and its members both match a query, the cluster wins. Members carry a `superseded_by:` field pointing at their cluster.

## Atomic guidelines, alphabetical

- **[Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md)** `d4ca5794caac`
  - `jq -r '.<field>' <path>` extracts one value per line. Pipe through `sort -u` for deduplication and `wc -l` for the count. If `jq` is…
- **[Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md)** `a0f68b14ae96`
  - `grep -c '<token>' <path>` returns just the count, no lines. Use `-i` for case-insensitive, `-E` for regex, `-w` for whole-word. To count…
- **[Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md)** `a126365d4ad6`
  - `gunzip -c <path>` writes decompressed output to stdout (the `-c` keeps the original file). Pipe through `head -n N` for the first N lines.…
- **[List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md)** `e93a60691856`
  - `tar -tvf <path>` lists entries one per line with mode, owner, size, mtime, name — strictly richer than `tarfile.getnames()`. Python…
- **[List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md)** `214b47b178bb`
  - Use `zipfile.ZipFile(path).namelist()` — one call returns a list of strings. The stdlib reads the central directory; no struct manipulation…
- **[Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md)** `e91cf5e787b3`
  - `wave.open(path, 'rb')` returns a reader with `.getframerate()`, `.getnchannels()`, `.getsampwidth()`, `.getnframes()`. Stdlib parses the…
- **[Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md)** `df9160ecdaf0`
  - AGENTS.md ships with: 'Don't read me for trivial tasks (typo fix, single-line refactor) or topics clearly outside the wiki's scope.' If the…
- **[Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md)** `8bcec97f6837`
  - Just Read the file. INI's syntax is human-readable; a one-shot value lookup doesn't need `configparser`. For larger files, or when you need…
- **[Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md)** `f0785632775e`
  - After reading AGENTS.md, read `_index.jsonl` end-to-end and check whether any row's tags or trigger text overlaps your task's topical tags.…

## By tag

### `untagged`

- [Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md) `d4ca5794caac`
- [Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md) `a0f68b14ae96`
- [Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md) `a126365d4ad6`
- [List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md) `e93a60691856`
- [List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md) `214b47b178bb`
- [Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md) `e91cf5e787b3`
- [Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md) `df9160ecdaf0`
- [Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md) `8bcec97f6837`
- [Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md) `f0785632775e`


## Recall roll-up

Cross-summary tally of `recalled_guidelines:` blocks. Rows are alphabetical by guideline title. A row of zeros means the guideline has been contributed by a session but never recalled by another.

| Guideline | Total | followed | ignored | contradicted | harmful |
|-----------|------:|---------:|--------:|-------------:|--------:|
| [Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md) | 0 | 0 | 0 | 0 | 0 |
| [Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md) | 0 | 0 | 0 | 0 | 0 |
| [Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md) | 0 | 0 | 0 | 0 | 0 |
| [List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md) | 0 | 0 | 0 | 0 | 0 |
| [List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md) | 0 | 0 | 0 | 0 | 0 |
| [Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md) | 0 | 0 | 0 | 0 | 0 |
| [Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md) | 0 | 0 | 0 | 0 | 0 |
| [Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md) | 0 | 0 | 0 | 0 | 0 |
| [Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md) | 0 | 0 | 0 | 0 | 0 |

## Pages, by priority

Unified roll-up across clusters + atomic guidelines. Priority is computed each catalog run from recall counts and cluster membership (not authored). Rows sort by tier (`high` → `disputed` → `weak` → `normal` → `low` → `unvalidated`), then alphabetical within tier.

| Title | Kind | Priority | Trigger | Tags | Cluster | Recall (T / f / i / c / h) | Verified at |
|-------|------|----------|---------|------|---------|---------------------------:|-------------|
| [Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md) | atomic | **unvalidated** | When summarizing a JSONL field's distinct values and `jq` is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md) | atomic | **unvalidated** | When counting occurrences of a literal token in a text file | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md) | atomic | **unvalidated** | When you need a peek at a gzipped file's content without fully decompressing | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md) | atomic | **unvalidated** | When you need TAR entry names + metadata and a unix `tar` is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md) | atomic | **unvalidated** | When you need ZIP entry names and Python is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md) | atomic | **unvalidated** | When you need WAV header fields and Python is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md) | atomic | **unvalidated** | When the task is trivially answerable without external knowledge AND AGENTS.m… | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md) | atomic | **unvalidated** | When the INI file is small (<50 lines) and you need one specific key | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md) | atomic | **unvalidated** | When AGENTS.md tells you to consult the wiki, but the user's task may be outs… | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
