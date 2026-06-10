# Four-way wiki-helps comparison: empty / guidelines / skills / both

Same 16-task corpus, four arms, all `claude_md_strong` condition. Empty + guidelines arms are twobatch's batch-1 / batch-2. Skills arm is twobatch-skills (3 skills, no guidelines). Both arm is twobatch-both (those same 3 skills + ~15 atomics, no clusters).

## Aggregate

| Metric | Empty | Guidelines | Skills | Both | Both vs G | Both vs S |
|---|---:|---:|---:|---:|---:|---:|
| Trials | 47 | 48 | 48 | 48 | +0 | +0 |
| Accuracy (mean) | 96% | 96% | 98% | 98% | +2% | +0% |
| Median duration | 43s | 27s | 28s | 31s | +4s (+15%) | +3s (+10%) |
| Median input tokens | 4,517 | 4,378 | 4,376 | 4,376 | -2 (-0%) | +0 (+0%) |
| Median output tokens | 406 | 268 | 206 | 272 | +4 (+1%) | +66 (+32%) |
| Median total cost USD | $0.2141 | $0.1703 | $0.1463 | $0.1788 | +$0.0085 (+5%) | +$0.0325 (+22%) |
| Median tool calls | 7.0 | 4.0 | 4.0 | 4.0 | +0.0 | +0.0 |
| Median wiki reads | 5.0 | 3.0 | 2.0 | 2.0 | -1.0 | +0.0 |
| Median guideline reads | 1.0 | 1.0 | 0.0 | 0.0 | -1.0 | — |

## By task family

Median total_cost_usd. `Δ G→B` is `both` minus `guidelines`; `Δ S→B` is `both` minus `skills`.

| Family | Tasks | E acc | G acc | S acc | B acc | E $ | G $ | S $ | B $ | Δ G→B | Δ S→B |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lens-model | 1 | 67% | 100% | 100% | 100% | $0.3681 | $0.2460 | $0.1763 | $0.2071 | $-0.0389 (-16%) | +$0.0308 (+17%) |
| image | 4 | 91% | 100% | 100% | 100% | $0.2141 | $0.1731 | $0.1477 | $0.1803 | +$0.0073 (+4%) | +$0.0326 (+22%) |
| archive | 4 | 100% | 92% | 92% | 92% | $0.2133 | $0.1712 | $0.1343 | $0.1777 | +$0.0065 (+4%) | +$0.0433 (+32%) |
| text | 4 | 100% | 100% | 100% | 100% | $0.2097 | $0.1459 | $0.1541 | $0.1527 | +$0.0069 (+5%) | $-0.0014 (-1%) |
| skip | 3 | 100% | 89% | 100% | 100% | $0.2456 | $0.2061 | $0.1412 | $0.2031 | $-0.0030 (-1%) | +$0.0619 (+44%) |

## Per task — cost USD

| Task | E $ | G $ | S $ | B $ | Δ G→B | Δ S→B |
|---|---:|---:|---:|---:|---:|---:|
| `t1-lens-model` | $0.3681 | $0.2460 | $0.1763 | $0.2071 | $-0.0389 (-16%) | +$0.0308 (+17%) |
| `t6-png-dim` | $0.1970 | $0.1725 | $0.1487 | $0.1778 | +$0.0053 (+3%) | +$0.0291 (+20%) |
| `t7-gif-dim` | $0.2141 | $0.1637 | $0.1463 | $0.1736 | +$0.0099 (+6%) | +$0.0274 (+19%) |
| `t8-bmp-info` | $0.2950 | $0.1723 | $0.1508 | $0.1931 | +$0.0208 (+12%) | +$0.0423 (+28%) |
| `t9-webp-dim` | $0.2604 | $0.1772 | $0.1467 | $0.1796 | +$0.0024 (+1%) | +$0.0329 (+22%) |
| `t10-zip-list` | $0.2099 | $0.1667 | $0.1344 | $0.1501 | $-0.0166 (-10%) | +$0.0156 (+12%) |
| `t11-tar-list` | $0.2144 | $0.1731 | $0.1342 | $0.1799 | +$0.0068 (+4%) | +$0.0457 (+34%) |
| `t12-wav-info` | $0.2088 | $0.1702 | $0.1606 | $0.1822 | +$0.0120 (+7%) | +$0.0216 (+13%) |
| `t13-gzip-dec` | $0.2125 | $0.1663 | $0.1270 | $0.1725 | +$0.0062 (+4%) | +$0.0456 (+36%) |
| `t14-csv-quoted` | $0.2102 | $0.1501 | $0.1776 | $0.2235 | +$0.0734 (+49%) | +$0.0460 (+26%) |
| `t15-jsonl-kinds` | $0.2241 | $0.1469 | $0.1685 | $0.1484 | +$0.0014 (+1%) | $-0.0201 (-12%) |
| `t16-ini-key` | $0.1891 | $0.1456 | $0.1395 | $0.1534 | +$0.0078 (+5%) | +$0.0139 (+10%) |
| `t17-log-errors` | $0.1924 | $0.1453 | $0.1318 | $0.1456 | +$0.0003 (+0%) | +$0.0138 (+10%) |
| `t2-imports` | $0.2817 | $0.2436 | $0.1491 | $0.2480 | +$0.0044 (+2%) | +$0.0989 (+66%) |
| `t3-todos` | $0.2456 | $0.2305 | $0.1613 | $0.2177 | $-0.0128 (-6%) | +$0.0565 (+35%) |
| `t5-base64` | $0.2051 | $0.1266 | $0.1207 | $0.1292 | +$0.0026 (+2%) | +$0.0085 (+7%) |

## Per task — accuracy

| Task | E acc | G acc | S acc | B acc |
|---|:-:|:-:|:-:|:-:|
| `t1-lens-model` | 67% | 100% | 100% | 100% |
| `t6-png-dim` | 100% | 100% | 100% | 100% |
| `t7-gif-dim` | 67% | 100% | 100% | 100% |
| `t8-bmp-info` | 100% | 100% | 100% | 100% |
| `t9-webp-dim` | 100% | 100% | 100% | 100% |
| `t10-zip-list` | 100% | 100% | 100% | 100% |
| `t11-tar-list` | 100% | 100% | 100% | 100% |
| `t12-wav-info` | 100% | 67% | 67% | 67% |
| `t13-gzip-dec` | 100% | 100% | 100% | 100% |
| `t14-csv-quoted` | 100% | 100% | 100% | 100% |
| `t15-jsonl-kinds` | 100% | 100% | 100% | 100% |
| `t16-ini-key` | 100% | 100% | 100% | 100% |
| `t17-log-errors` | 100% | 100% | 100% | 100% |
| `t2-imports` | 100% | 67% | 100% | 100% |
| `t3-todos` | 100% | 100% | 100% | 100% |
| `t5-base64` | 100% | 100% | 100% | 100% |

## Notes

- Empty + guidelines columns reproduce twobatch.
- Skills column reproduces the skills-arm experiment.
- Both column is the new arm: same 3 skills + ~15 atomics from twobatch's batch-1 trajectories. No clusters (matching the guidelines arm's structure).
- Trivial-recipe tasks (t11-tar, t13-gzip, t15-jsonl, t16-ini, t17-log, t2/t3, t5) have no matching skill in any arm.
