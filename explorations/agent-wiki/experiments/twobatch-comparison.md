# Two-batch wiki-helps comparison

**Question**: does a populated wiki reduce token cost / wall-clock at equal-or-better accuracy, vs the same task on an empty wiki?

Setup: 16 tasks × 3 trials × 2 batches = 96 sandbox trials, all `claude_md_strong`. Batch 1's agent saw an empty wiki. After ingestion the wiki was frozen (47 summaries → 15 atomics → consolidation; the live state is at `wiki-twobatch/`). Batch 2's agent saw the populated wiki.

## TL;DR

| Headline | Δ |
|---|---|
| **Median total cost** ($0.21 → $0.17) | **-20%** |
| **Median duration** (43s → 27s) | **-38%** |
| **Median tool calls** (7 → 4) | **-43%** |
| **Median wiki reads** (5 → 3) | **-40%** |
| **Median output tokens** (406 → 268) | **-34%** |
| **Cache-read tokens** (cheap) | -32% |
| **Cache-creation tokens** (one-shot, agent reads new pages) | +66% |
| **Aggregate accuracy** | unchanged (96%) |

**With wiki → faster, cheaper, fewer tools, equal accuracy.** The agent's
recipe path is shorter when the wiki has the recipe.

Two task-level regressions worth noting (both 100% → 67% in batch 2):
`t12-wav-info` and `t2-imports`. One trial of each failed in batch 2,
likely the agent over-applying or misreading a recalled guideline.
Lens-model went the other way: 67% → 100% (the wiki rescued failing
trials).

The `billable_tokens_proxy` column reads "+47%" because it doesn't
discount cache-read tokens. The actual `total_cost_usd` (which Anthropic
prices cache-reads at ~10% of regular input) is the ground truth — and
that's down 20%.

## Aggregate (96 trials)

| Metric | Batch 1 (empty wiki) | Batch 2 (with wiki) | Δ |
|---|---:|---:|---:|
| Trials | 47 | 48 | +1 |
| Accuracy (mean) | 96% | 96% | +0.0 (+0%) |
| Median duration | 43s | 27s | -17s (-38%) |
| Median input tokens | 4,517 | 4,378 | -139 (-3%) |
| Median cache-creation tokens | 13,197 | 21,855 | +8,658 (+66%) |
| Median cache-read tokens | 545,088 | 367,979 | -177,108 (-32%) |
| Median output tokens | 406 | 268 | -137 (-34%) |
| Median billable proxy (in+cc+out) | 17,838 | 26,223 | +8,385 (+47%) |
| Median total cost USD | $0.2141 | $0.1703 | $-0.0438 (-20%) |
| Median tool calls | 7.0 | 4.0 | -3.0 (-43%) |
| Median wiki reads | 5.0 | 3.0 | -2.0 (-40%) |
| Median guideline reads | 1.0 | 1.0 | +0.0 (+0%) |

## By task family

Median per-trial cost within each family. Δ = batch-2 minus batch-1.

| Family | Tasks | B1 acc | B2 acc | Δ acc | B1 dur | B2 dur | Δ dur | B1 tokens | B2 tokens | Δ tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lens-model | t1-lens-model | 67% | 100% | +0.3 (+50%) | 101s | 49s | -53s (-52%) | 25,990 | 27,957 | +1,967 (+8%) |
| image | t6-png-dim, t7-gif-dim, t8-bmp-info, t9-webp-dim | 91% | 100% | +0.1 (+10%) | 58s | 27s | -31s (-54%) | 14,826 | 22,876 | +8,050 (+54%) |
| archive | t10-zip-list, t11-tar-list, t12-wav-info, t13-gzip-dec | 100% | 92% | -0.1 (-8%) | 39s | 25s | -14s (-36%) | 17,847 | 27,012 | +9,165 (+51%) |
| text | t14-csv-quoted, t15-jsonl-kinds, t16-ini-key, t17-log-errors | 100% | 100% | +0.0 (+0%) | 40s | 23s | -17s (-42%) | 17,142 | 25,683 | +8,540 (+50%) |
| skip | t2-imports, t3-todos, t5-base64 | 100% | 89% | -0.1 (-11%) | 53s | 42s | -11s (-21%) | 20,992 | 30,099 | +9,107 (+43%) |

## Per task

Median across 3 trials per cell. Token = `billable_tokens_proxy` (input + cache-creation + output; cache reads excluded).

| Task | B1 acc | B2 acc | B1 dur | B2 dur | Δ dur | B1 tokens | B2 tokens | Δ tokens | B1 tools | B2 tools |
|---|:-:|:-:|---:|---:|---:|---:|---:|---:|---:|---:|
| `t1-lens-model` | 67% | 100% | 101s | 49s | -53s (-52%) | 25,990 | 27,957 | +1,967 (+8%) | 9.0 | 5.0 |
| `t6-png-dim` | 100% | 100% | 43s | 27s | -16s (-38%) | 13,676 | 27,940 | +14,264 (+104%) | 6.0 | 4.0 |
| `t7-gif-dim` | 67% | 100% | 57s | 24s | -32s (-57%) | 12,852 | 18,117 | +5,265 (+41%) | 7.0 | 4.0 |
| `t8-bmp-info` | 100% | 100% | 79s | 27s | -53s (-66%) | 45,158 | 23,850 | -21,308 (-47%) | 6.5 | 4.0 |
| `t9-webp-dim` | 100% | 100% | 70s | 27s | -43s (-62%) | 20,458 | 20,807 | +349 (+2%) | 7.0 | 4.0 |
| `t10-zip-list` | 100% | 100% | 29s | 25s | -4s (-13%) | 17,948 | 26,281 | +8,333 (+46%) | 7.0 | 4.0 |
| `t11-tar-list` | 100% | 100% | 40s | 25s | -15s (-38%) | 16,798 | 27,807 | +11,009 (+66%) | 7.0 | 4.0 |
| `t12-wav-info` | 100% | 67% | 49s | 24s | -25s (-51%) | 19,681 | 27,826 | +8,145 (+41%) | 6.0 | 4.0 |
| `t13-gzip-dec` | 100% | 100% | 37s | 36s | -1s (-3%) | 17,885 | 24,229 | +6,344 (+35%) | 7.0 | 4.0 |
| `t14-csv-quoted` | 100% | 100% | 39s | 22s | -17s (-43%) | 16,516 | 25,200 | +8,684 (+53%) | 7.0 | 3.0 |
| `t15-jsonl-kinds` | 100% | 100% | 44s | 27s | -16s (-38%) | 19,519 | 22,883 | +3,364 (+17%) | 7.0 | 3.0 |
| `t16-ini-key` | 100% | 100% | 36s | 22s | -14s (-39%) | 18,618 | 26,166 | +7,548 (+41%) | 6.0 | 3.0 |
| `t17-log-errors` | 100% | 100% | 46s | 27s | -19s (-41%) | 15,727 | 27,010 | +11,283 (+72%) | 6.0 | 3.0 |
| `t2-imports` | 100% | 67% | 64s | 50s | -14s (-21%) | 25,637 | 32,932 | +7,295 (+28%) | 8.0 | 5.0 |
| `t3-todos` | 100% | 100% | 54s | 55s | +1s (+3%) | 20,992 | 32,489 | +11,497 (+55%) | 8.0 | 5.0 |
| `t5-base64` | 100% | 100% | 37s | 19s | -18s (-48%) | 12,771 | 17,436 | +4,665 (+37%) | 6.0 | 2.0 |

## Notes

- `billable_tokens_proxy` = `input_tokens + cache_creation_input_tokens + output_tokens` (cache reads are very cheap and not directly billed at the same rate).
- A trial that timed out is recorded with `outcome_match=False`, `duration_s=300`, all token fields = 0. These bring batch-1 means down if they happen.
- Only `claude_md_strong` was run in this experiment for clean comparison (no condition mixing).

