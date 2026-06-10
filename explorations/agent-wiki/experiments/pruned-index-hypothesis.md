# Pruned Index Hypothesis Test

Date: 2026-06-09

## Question

Did `wiki-twobatch-pruned` regress because the delete-on-promote policy is bad,
or because `_index.jsonl` was stale after skill synthesis archived covered
guidelines?

## Setup

High-signal slice rerun across four arms:

- `wiki-twobatch-skills`
- `wiki-twobatch-both`
- `wiki-twobatch-pruned` (original broken index)
- `wiki-twobatch-pruned-recataloged` (copy of original pruned, then `catalog`)

Tasks:

- `t1-lens-model`
- `t6-png-dim`
- `t7-gif-dim`
- `t8-bmp-info`
- `t9-webp-dim`
- `t14-csv-quoted`

Each arm used `claude_md_strong`, 3 trials per task, 18 trials per arm.

Before rerun:

| Wiki | Rows | Kinds | Missing links |
|---|---:|---|---:|
| `wiki-twobatch-skills` | 3 | 3 skills | 0 |
| `wiki-twobatch-both` | 18 | 3 skills, 15 guidelines | 0 |
| `wiki-twobatch-pruned` | 15 | 15 guidelines, 0 skills | 6 |
| `wiki-twobatch-pruned-recataloged` | 12 | 3 skills, 9 guidelines | 0 |

## Aggregate Results

All four arms completed 18/18 outcome matches.

| Arm | Median cost | Sum cost | Median output tokens | Median duration | Median tools | Median wiki reads | Median guideline reads | Median skill reads |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| skills | $0.1548 | $3.0366 | 154 | 23.21s | 4 | 2 | 0 | 1 |
| both | $0.1937 | $3.6701 | 252 | 22.40s | 5 | 2 | 0 | 1 |
| pruned-broken | $0.2323 | $4.1766 | 320 | 32.45s | 6 | 3 | 1 | 0 |
| pruned-recataloged | $0.1934 | $3.7721 | 198 | 22.96s | 5 | 2 | 0 | 1 |

## Per-Task Median Cost

| Task | Skills | Both | Pruned broken | Pruned recataloged |
|---|---:|---:|---:|---:|
| `t1-lens-model` | $0.1742 | $0.2113 | $0.2915 | $0.2527 |
| `t6-png-dim` | $0.1491 | $0.1821 | $0.2340 | $0.1881 |
| `t7-gif-dim` | $0.1438 | $0.1718 | $0.2257 | $0.1824 |
| `t8-bmp-info` | $0.1565 | $0.2161 | $0.2335 | $0.1939 |
| `t9-webp-dim` | $0.1478 | $0.1844 | $0.2319 | $0.1829 |
| `t14-csv-quoted` | $0.1919 | $0.2074 | $0.1561 | $0.2107 |

## Interpretation

The stale-index hypothesis is supported. Recataloging the pruned wiki reduced
median cost from `$0.2323` to `$0.1934` (-17%), median output tokens from `320`
to `198` (-38%), median duration from `32.45s` to `22.96s` (-29%), and median
tool calls from `6` to `5`.

The mechanism is also supported by retrieval behavior. The broken pruned arm
had no skill reads in any trial because `_index.jsonl` did not expose skills.
It had a median of 1 guideline read and followed stale rows for archived
guidelines. The corrected pruned arm had median 1 skill read and 0 guideline
reads, matching the intended retrieval path.

The broader skills-only conclusion still holds on this slice. Corrected pruned
roughly ties `both` on median cost (`$0.1934` vs `$0.1937`) and lowers output
tokens (`198` vs `252`), but it remains more expensive than skills-only
(`$0.1548`). So there are two effects:

1. The original pruned result was materially confounded by a stale/broken index.
2. Even after fixing the index, skills-only remains the cheapest retrieval
   surface for these direct skill-match tasks.

## Artifacts

Metrics:

- `experiments/results-pruned-index-hypothesis/skills/metrics.jsonl`
- `experiments/results-pruned-index-hypothesis/both/metrics.jsonl`
- `experiments/results-pruned-index-hypothesis/pruned-broken/metrics.jsonl`
- `experiments/results-pruned-index-hypothesis/pruned-recataloged/metrics.jsonl`

Corrected wiki copy:

- `wiki-twobatch-pruned-recataloged/`
