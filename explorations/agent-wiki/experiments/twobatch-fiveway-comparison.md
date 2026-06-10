# Five-way wiki-helps comparison: empty / guidelines / skills / both / pruned

Same 16-task corpus, five arms, all `claude_md_strong` condition. Empty + guidelines arms are twobatch's batch-1 / batch-2. Skills arm is twobatch-skills (3 skills, no guidelines). Both arm is twobatch-both (those same 3 skills + ~15 atomics, no clusters). **Pruned arm** is twobatch-pruned: same 3 skills + only the no-skill-coverage atomics (delete-on-promote policy applied — image-format and CSV atomics archived because their corresponding skills were synthesized).

## Aggregate

| Metric | Empty | Guidelines | Skills | Both | Pruned | P vs G | P vs S | P vs B |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Trials | 47 | 48 | 48 | 48 | 48 | +0 | +0 | +0 |
| Accuracy (mean) | 96% | 96% | 98% | 98% | 98% | +2% | +0% | +0% |
| Median duration | 43s | 27s | 28s | 31s | 21s | -6s (-22%) | -7s (-25%) | -10s (-32%) |
| Median input tokens | 4,517 | 4,378 | 4,376 | 4,376 | 4,378 | +0 (+0%) | +2 (+0%) | +2 (+0%) |
| Median output tokens | 406 | 268 | 206 | 272 | 225 | -43 (-16%) | +19 (+9%) | -47 (-17%) |
| Median total cost USD | $0.2141 | $0.1703 | $0.1463 | $0.1788 | $0.1726 | +$0.0023 (+1%) | +$0.0263 (+18%) | $-0.0062 (-3%) |
| Median tool calls | 7.0 | 4.0 | 4.0 | 4.0 | 4.0 | +0.0 | +0.0 | +0.0 |
| Median wiki reads | 5.0 | 3.0 | 2.0 | 2.0 | 2.0 | -1.0 | +0.0 | +0.0 |
| Median guideline reads | 1.0 | 1.0 | 0.0 | 0.0 | 0.0 | -1.0 | — | — |

## By task family

Median total_cost_usd. `Δ S→P` = `pruned` minus `skills`; `Δ B→P` = `pruned` minus `both`.

| Family | Tasks | E acc | G acc | S acc | B acc | P acc | E $ | G $ | S $ | B $ | P $ | Δ S→P | Δ B→P |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lens-model | 1 | 67% | 100% | 100% | 100% | 100% | $0.3681 | $0.2460 | $0.1763 | $0.2071 | $0.2289 | +$0.0526 (+30%) | +$0.0218 (+11%) |
| image | 4 | 91% | 100% | 100% | 100% | 100% | $0.2141 | $0.1731 | $0.1477 | $0.1803 | $0.1964 | +$0.0487 (+33%) | +$0.0160 (+9%) |
| archive | 4 | 100% | 92% | 92% | 92% | 100% | $0.2133 | $0.1712 | $0.1343 | $0.1777 | $0.1663 | +$0.0320 (+24%) | $-0.0114 (-6%) |
| text | 4 | 100% | 100% | 100% | 100% | 100% | $0.2097 | $0.1459 | $0.1541 | $0.1527 | $0.1487 | $-0.0054 (-3%) | $-0.0040 (-3%) |
| skip | 3 | 100% | 89% | 100% | 100% | 89% | $0.2456 | $0.2061 | $0.1412 | $0.2031 | $0.1672 | +$0.0260 (+18%) | $-0.0359 (-18%) |

## Per task — cost USD

| Task | E $ | G $ | S $ | B $ | P $ | Δ S→P | Δ B→P |
|---|---:|---:|---:|---:|---:|---:|---:|
| `t1-lens-model` | $0.3681 | $0.2460 | $0.1763 | $0.2071 | $0.2289 | +$0.0526 (+30%) | +$0.0218 (+11%) |
| `t6-png-dim` | $0.1970 | $0.1725 | $0.1487 | $0.1778 | $0.1975 | +$0.0489 (+33%) | +$0.0198 (+11%) |
| `t7-gif-dim` | $0.2141 | $0.1637 | $0.1463 | $0.1736 | $0.2181 | +$0.0719 (+49%) | +$0.0445 (+26%) |
| `t8-bmp-info` | $0.2950 | $0.1723 | $0.1508 | $0.1931 | $0.2481 | +$0.0973 (+65%) | +$0.0550 (+28%) |
| `t9-webp-dim` | $0.2604 | $0.1772 | $0.1467 | $0.1796 | $0.1834 | +$0.0368 (+25%) | +$0.0039 (+2%) |
| `t10-zip-list` | $0.2099 | $0.1667 | $0.1344 | $0.1501 | $0.1425 | +$0.0081 (+6%) | $-0.0075 (-5%) |
| `t11-tar-list` | $0.2144 | $0.1731 | $0.1342 | $0.1799 | $0.1658 | +$0.0316 (+24%) | $-0.0141 (-8%) |
| `t12-wav-info` | $0.2088 | $0.1702 | $0.1606 | $0.1822 | $0.1674 | +$0.0068 (+4%) | $-0.0149 (-8%) |
| `t13-gzip-dec` | $0.2125 | $0.1663 | $0.1270 | $0.1725 | $0.1704 | +$0.0434 (+34%) | $-0.0021 (-1%) |
| `t14-csv-quoted` | $0.2102 | $0.1501 | $0.1776 | $0.2235 | $0.2080 | +$0.0304 (+17%) | $-0.0156 (-7%) |
| `t15-jsonl-kinds` | $0.2241 | $0.1469 | $0.1685 | $0.1484 | $0.1624 | $-0.0061 (-4%) | +$0.0140 (+9%) |
| `t16-ini-key` | $0.1891 | $0.1456 | $0.1395 | $0.1534 | $0.1424 | +$0.0029 (+2%) | $-0.0110 (-7%) |
| `t17-log-errors` | $0.1924 | $0.1453 | $0.1318 | $0.1456 | $0.1378 | +$0.0060 (+5%) | $-0.0078 (-5%) |
| `t2-imports` | $0.2817 | $0.2436 | $0.1491 | $0.2480 | $0.1672 | +$0.0181 (+12%) | $-0.0808 (-33%) |
| `t3-todos` | $0.2456 | $0.2305 | $0.1613 | $0.2177 | $0.1920 | +$0.0307 (+19%) | $-0.0257 (-12%) |
| `t5-base64` | $0.2051 | $0.1266 | $0.1207 | $0.1292 | $0.0926 | $-0.0281 (-23%) | $-0.0366 (-28%) |

## Per task — accuracy

| Task | E acc | G acc | S acc | B acc | P acc |
|---|:-:|:-:|:-:|:-:|:-:|
| `t1-lens-model` | 67% | 100% | 100% | 100% | 100% |
| `t6-png-dim` | 100% | 100% | 100% | 100% | 100% |
| `t7-gif-dim` | 67% | 100% | 100% | 100% | 100% |
| `t8-bmp-info` | 100% | 100% | 100% | 100% | 100% |
| `t9-webp-dim` | 100% | 100% | 100% | 100% | 100% |
| `t10-zip-list` | 100% | 100% | 100% | 100% | 100% |
| `t11-tar-list` | 100% | 100% | 100% | 100% | 100% |
| `t12-wav-info` | 100% | 67% | 67% | 67% | 100% |
| `t13-gzip-dec` | 100% | 100% | 100% | 100% | 100% |
| `t14-csv-quoted` | 100% | 100% | 100% | 100% | 100% |
| `t15-jsonl-kinds` | 100% | 100% | 100% | 100% | 100% |
| `t16-ini-key` | 100% | 100% | 100% | 100% | 100% |
| `t17-log-errors` | 100% | 100% | 100% | 100% | 100% |
| `t2-imports` | 100% | 67% | 100% | 100% | 67% |
| `t3-todos` | 100% | 100% | 100% | 100% | 100% |
| `t5-base64` | 100% | 100% | 100% | 100% | 100% |

## Notes

- Empty + guidelines + skills + both columns reproduce the 4-way comparison.
- Pruned column is the new arm, testing the **delete-on-promote** policy: when `synthesize-skill` produces a skill, it inferentially archives the atomic guidelines covered by the skill (via tag-superset, slug-keyword, or format-identifier description match). Result: 3 skills + 9 atomics + 6 archived.
- The pruned arm is the experimental answer to the open question "if 'both' loses to 'skills-only', does 'skills + only the no-skill-coverage guidelines' beat 'skills-only'?" raised in §7 of RESULTS-SUMMARY.md.

### Correction — Pruned column is the re-run against a fixed index

The original pruned arm (commit `8bcd713`) ran against a wiki whose `_index.jsonl` was **stale**: `render-skill` archived the covered atomics but never refreshed the indexes, so the wiki exposed **0 skills, 15 guideline rows, 6 broken links**. Agents couldn't see the skills and fell back to dangling guideline rows (original: median $0.181, 290 output tokens, 3 wiki reads, 1 guideline read).

Commit `2adc67a` fixed the builder to refresh the section indexes + `_index.jsonl` after `render-skill`/`render-cluster` (with an integrity assertion). This Pruned column is the full 16-task re-run against the corrected wiki: median **$0.173**, ~225 output tokens, 2 wiki reads, **0** guideline reads. Net: pruned moved from +1% to **-3% vs both** and from +24% to **+18% vs skills**. Skills-only is still cheapest, but the apparent "pruning is worse than both" result was largely the stale-index bug, not the policy. See `pruned-index-hypothesis.md` for the slice-level diagnosis.
