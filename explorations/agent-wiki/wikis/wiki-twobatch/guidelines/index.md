---
type: section-index
section: guidelines
verified_at: 2026-06-10
count: 15
atomic: 15
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
- **[Read BMP width and bit depth via the BITMAPINFOHEADER offsets](read-bmp-width-and-bit-depth-via-the__6a9f9950c6f5.md)** `6a9f9950c6f5`
  - Validate the first 2 bytes are `BM` (the file header). The BITMAPINFOHEADER begins at byte 14. Width is at file offset 18 (uint32 LE, 4…
- **[Read GIF version and dimensions from the first 10 bytes via stdlib struct](read-gif-version-and-dimensions-from__70d9f68d438c.md)** `70d9f68d438c`
  - GIF header layout: bytes 0-5 are the signature ASCII (`GIF87a` or `GIF89a`). Bytes 6-7 are width (uint16 little-endian); bytes 8-9 are…
- **[Read PNG width and height from the IHDR chunk via stdlib struct](read-png-width-and-height-from-the-ihdr__d9c1eb48d6bf.md)** `d9c1eb48d6bf`
  - Validate the 8-byte signature `\x89PNG\r\n\x1a\n` first. The IHDR chunk follows immediately (4-byte length, 4-byte type 'IHDR'). Width and…
- **[Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md)** `e91cf5e787b3`
  - `wave.open(path, 'rb')` returns a reader with `.getframerate()`, `.getnchannels()`, `.getsampwidth()`, `.getnframes()`. Stdlib parses the…
- **[Read WebP dimensions by dispatching on the RIFF subchunk type](read-webp-dimensions-by-dispatching-on__7f630abacc50.md)** `7f630abacc50`
  - WebP is a RIFF container. Validate bytes 0-3 = `RIFF` and 8-11 = `WEBP`. Read the 4-byte chunk type at offset 12 to dispatch: `VP8 ` (lossy…
- **[Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md)** `df9160ecdaf0`
  - AGENTS.md ships with: 'Don't read me for trivial tasks (typo fix, single-line refactor) or topics clearly outside the wiki's scope.' If the…
- **[Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md)** `8bcec97f6837`
  - Just Read the file. INI's syntax is human-readable; a one-shot value lookup doesn't need `configparser`. For larger files, or when you need…
- **[Use stdlib `csv.reader` with `newline=''` for CSVs that may have quoted commas](use-stdlib-csv-reader-with-newline-for__599e2d3b582b.md)** `599e2d3b582b`
  - Open with `newline=''` (REQUIRED — without it, embedded newlines inside quoted fields break the row boundary). Then `csv.reader(f)` walks…
- **[Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md)** `f0785632775e`
  - After reading AGENTS.md, read `_index.jsonl` end-to-end and check whether any row's tags or trigger text overlaps your task's topical tags.…
- **[Walk the Exif sub-IFD via tag 0x8769 to read camera-optics fields](walk-the-exif-sub-ifd-via-tag-0x8769-to__4a0c0dc7fca9.md)** `4a0c0dc7fca9`
  - JPEG EXIF lives behind APP1 marker `0xFFE1`. Inside, after the literal `Exif\x00\x00`, sits the TIFF block. Walk IFD0 to find tag `0x8769`…

## By tag

### `untagged`

- [Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md) `d4ca5794caac`
- [Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md) `a0f68b14ae96`
- [Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md) `a126365d4ad6`
- [List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md) `e93a60691856`
- [List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md) `214b47b178bb`
- [Read BMP width and bit depth via the BITMAPINFOHEADER offsets](read-bmp-width-and-bit-depth-via-the__6a9f9950c6f5.md) `6a9f9950c6f5`
- [Read GIF version and dimensions from the first 10 bytes via stdlib struct](read-gif-version-and-dimensions-from__70d9f68d438c.md) `70d9f68d438c`
- [Read PNG width and height from the IHDR chunk via stdlib struct](read-png-width-and-height-from-the-ihdr__d9c1eb48d6bf.md) `d9c1eb48d6bf`
- [Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md) `e91cf5e787b3`
- [Read WebP dimensions by dispatching on the RIFF subchunk type](read-webp-dimensions-by-dispatching-on__7f630abacc50.md) `7f630abacc50`
- [Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md) `df9160ecdaf0`
- [Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md) `8bcec97f6837`
- [Use stdlib `csv.reader` with `newline=''` for CSVs that may have quoted commas](use-stdlib-csv-reader-with-newline-for__599e2d3b582b.md) `599e2d3b582b`
- [Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md) `f0785632775e`
- [Walk the Exif sub-IFD via tag 0x8769 to read camera-optics fields](walk-the-exif-sub-ifd-via-tag-0x8769-to__4a0c0dc7fca9.md) `4a0c0dc7fca9`


## Recall roll-up

Cross-summary tally of `recalled_guidelines:` blocks. Rows are alphabetical by guideline title. A row of zeros means the guideline has been contributed by a session but never recalled by another.

| Guideline | Total | followed | ignored | contradicted | harmful |
|-----------|------:|---------:|--------:|-------------:|--------:|
| [Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md) | 0 | 0 | 0 | 0 | 0 |
| [Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md) | 0 | 0 | 0 | 0 | 0 |
| [Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md) | 0 | 0 | 0 | 0 | 0 |
| [List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md) | 0 | 0 | 0 | 0 | 0 |
| [List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md) | 0 | 0 | 0 | 0 | 0 |
| [Read BMP width and bit depth via the BITMAPINFOHEADER offsets](read-bmp-width-and-bit-depth-via-the__6a9f9950c6f5.md) | 0 | 0 | 0 | 0 | 0 |
| [Read GIF version and dimensions from the first 10 bytes via stdlib struct](read-gif-version-and-dimensions-from__70d9f68d438c.md) | 0 | 0 | 0 | 0 | 0 |
| [Read PNG width and height from the IHDR chunk via stdlib struct](read-png-width-and-height-from-the-ihdr__d9c1eb48d6bf.md) | 0 | 0 | 0 | 0 | 0 |
| [Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md) | 0 | 0 | 0 | 0 | 0 |
| [Read WebP dimensions by dispatching on the RIFF subchunk type](read-webp-dimensions-by-dispatching-on__7f630abacc50.md) | 0 | 0 | 0 | 0 | 0 |
| [Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md) | 0 | 0 | 0 | 0 | 0 |
| [Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md) | 0 | 0 | 0 | 0 | 0 |
| [Use stdlib `csv.reader` with `newline=''` for CSVs that may have quoted commas](use-stdlib-csv-reader-with-newline-for__599e2d3b582b.md) | 0 | 0 | 0 | 0 | 0 |
| [Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md) | 0 | 0 | 0 | 0 | 0 |
| [Walk the Exif sub-IFD via tag 0x8769 to read camera-optics fields](walk-the-exif-sub-ifd-via-tag-0x8769-to__4a0c0dc7fca9.md) | 0 | 0 | 0 | 0 | 0 |

## Pages, by priority

Unified roll-up across clusters + atomic guidelines. Priority is computed each catalog run from recall counts and cluster membership (not authored). Rows sort by tier (`high` → `disputed` → `weak` → `normal` → `low` → `unvalidated`), then alphabetical within tier.

| Title | Kind | Priority | Trigger | Tags | Cluster | Recall (T / f / i / c / h) | Verified at |
|-------|------|----------|---------|------|---------|---------------------------:|-------------|
| [Count distinct field values in JSONL via `jq -r '.field' | sort -u | wc -l`](count-distinct-field-values-in-jsonl__d4ca5794caac.md) | atomic | **unvalidated** | When summarizing a JSONL field's distinct values and `jq` is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Count log lines matching a token via `grep -c '<token>' <path>`](count-log-lines-matching-a-token-via__a0f68b14ae96.md) | atomic | **unvalidated** | When counting occurrences of a literal token in a text file | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Inspect gzip contents via `gunzip -c <path> | head`](inspect-gzip-contents-via-gunzip-c-path__a126365d4ad6.md) | atomic | **unvalidated** | When you need a peek at a gzipped file's content without fully decompressing | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [List TAR entries via `tar -tvf <path>`](list-tar-entries-via-tar-tvf-path__e93a60691856.md) | atomic | **unvalidated** | When you need TAR entry names + metadata and a unix `tar` is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [List ZIP entries via stdlib `zipfile.ZipFile().namelist()`](list-zip-entries-via-stdlib-zipfile__214b47b178bb.md) | atomic | **unvalidated** | When you need ZIP entry names and Python is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read BMP width and bit depth via the BITMAPINFOHEADER offsets](read-bmp-width-and-bit-depth-via-the__6a9f9950c6f5.md) | atomic | **unvalidated** | When you need BMP dimensions or bit depth from raw bytes | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read GIF version and dimensions from the first 10 bytes via stdlib struct](read-gif-version-and-dimensions-from__70d9f68d438c.md) | atomic | **unvalidated** | When you need GIF version + dimensions without Pillow | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read PNG width and height from the IHDR chunk via stdlib struct](read-png-width-and-height-from-the-ihdr__d9c1eb48d6bf.md) | atomic | **unvalidated** | When you need PNG dimensions and Pillow / image tools may be unavailable | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read WAV sample rate / channels / bit depth via stdlib `wave`](read-wav-sample-rate-channels-bit-depth__e91cf5e787b3.md) | atomic | **unvalidated** | When you need WAV header fields and Python is available | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Read WebP dimensions by dispatching on the RIFF subchunk type](read-webp-dimensions-by-dispatching-on__7f630abacc50.md) | atomic | **unvalidated** | When you need WebP dimensions and the Pillow webp plugin may be missing | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Skip _index.jsonl when AGENTS.md scope warning rules out the task](skip-index-jsonl-when-agents-md-scope__df9160ecdaf0.md) | atomic | **unvalidated** | When the task is trivially answerable without external knowledge AND AGENTS.m… | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Skip the parser for tiny INI files — Read the file directly](skip-the-parser-for-tiny-ini-files-read__8bcec97f6837.md) | atomic | **unvalidated** | When the INI file is small (<50 lines) and you need one specific key | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Use stdlib `csv.reader` with `newline=''` for CSVs that may have quoted commas](use-stdlib-csv-reader-with-newline-for__599e2d3b582b.md) | atomic | **unvalidated** | When parsing CSV and any field might contain a comma, newline, or embedded quote | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Validate wiki applicability via _index.jsonl before forcing a citation](validate-wiki-applicability-via-index__f0785632775e.md) | atomic | **unvalidated** | When AGENTS.md tells you to consult the wiki, but the user's task may be outs… | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
| [Walk the Exif sub-IFD via tag 0x8769 to read camera-optics fields](walk-the-exif-sub-ifd-via-tag-0x8769-to__4a0c0dc7fca9.md) | atomic | **unvalidated** | When extracting LensModel / FocalLength / aperture / ISO from a JPEG and Pill… | — | — | 0 / 0 / 0 / 0 / 0 | 2026-06-10 |
