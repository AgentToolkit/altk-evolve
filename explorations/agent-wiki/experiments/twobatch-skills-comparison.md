# Three-way wiki-helps comparison: empty vs guidelines vs skills

Same 16-task corpus, three arms, all `claude_md_strong` condition. Empty + guidelines arms are the existing twobatch experiment's batch-1 / batch-2. Skills arm is the new run against `wiki-twobatch-skills/`, populated from twobatch's batch-1 trajectories via `agent-wiki-synthesize-skill`.

## Aggregate (3 trials × 16 tasks per arm)

| Metric | Empty | Guidelines | Skills | Skills vs guidelines |
|---|---:|---:|---:|---:|
| Trials | 47 | 48 | 48 | +0 |
| Accuracy (mean) | 96% | 96% | 98% | +2% |
| Median duration | 43s | 27s | 28s | +1s (+5%) |
| Median input tokens | 4,517 | 4,378 | 4,376 | -2 (-0%) |
| Median output tokens | 406 | 268 | 206 | -62 (-23%) |
| Median total cost USD | $0.2141 | $0.1703 | $0.1463 | $-0.0240 (-14%) |
| Median tool calls | 7.0 | 4.0 | 4.0 | +0.0 |
| Median wiki reads | 5.0 | 3.0 | 2.0 | -1.0 |
| Median guideline reads | 1.0 | 1.0 | 0.0 | -1.0 |

## By task family

Median per-trial within each family. Skills column shows Δ vs guidelines.

| Family | Tasks | E acc | G acc | S acc | E dur | G dur | S dur | E tokens | G tokens | S tokens | E $ | G $ | S $ | Skills Δ$ |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| lens-model | 1 tasks | 67% | 100% | 100% | 101s | 49s | 27s | 25,990 | 27,957 | 16,087 | $0.3681 | $0.2460 | $0.1763 | $-0.0697 (-28%) |
| image | 4 tasks | 91% | 100% | 100% | 58s | 27s | 26s | 14,826 | 22,876 | 15,657 | $0.2141 | $0.1731 | $0.1477 | $-0.0253 (-15%) |
| archive | 4 tasks | 100% | 92% | 92% | 39s | 25s | 29s | 17,847 | 27,012 | 17,634 | $0.2133 | $0.1712 | $0.1343 | $-0.0368 (-22%) |
| text | 4 tasks | 100% | 100% | 100% | 40s | 23s | 28s | 17,142 | 25,683 | 18,204 | $0.2097 | $0.1459 | $0.1541 | +$0.0082 (+6%) |
| skip | 3 tasks | 100% | 89% | 100% | 53s | 42s | 31s | 20,992 | 30,099 | 18,267 | $0.2456 | $0.2061 | $0.1412 | $-0.0649 (-32%) |

## Per task

| Task | E acc | G acc | S acc | E dur | G dur | S dur | E $ | G $ | S $ | Skills Δ$ vs G |
|---|:-:|:-:|:-:|---:|---:|---:|---:|---:|---:|---:|
| `t1-lens-model` | 67% | 100% | 100% | 101s | 49s | 27s | $0.3681 | $0.2460 | $0.1763 | $-0.0697 (-28%) |
| `t6-png-dim` | 100% | 100% | 100% | 43s | 27s | 25s | $0.1970 | $0.1725 | $0.1487 | $-0.0238 (-14%) |
| `t7-gif-dim` | 67% | 100% | 100% | 57s | 24s | 28s | $0.2141 | $0.1637 | $0.1463 | $-0.0174 (-11%) |
| `t8-bmp-info` | 100% | 100% | 100% | 79s | 27s | 26s | $0.2950 | $0.1723 | $0.1508 | $-0.0215 (-12%) |
| `t9-webp-dim` | 100% | 100% | 100% | 70s | 27s | 26s | $0.2604 | $0.1772 | $0.1467 | $-0.0306 (-17%) |
| `t10-zip-list` | 100% | 100% | 100% | 29s | 25s | 29s | $0.2099 | $0.1667 | $0.1344 | $-0.0322 (-19%) |
| `t11-tar-list` | 100% | 100% | 100% | 40s | 25s | 38s | $0.2144 | $0.1731 | $0.1342 | $-0.0388 (-22%) |
| `t12-wav-info` | 100% | 67% | 67% | 49s | 24s | 24s | $0.2088 | $0.1702 | $0.1606 | $-0.0096 (-6%) |
| `t13-gzip-dec` | 100% | 100% | 100% | 37s | 36s | 29s | $0.2125 | $0.1663 | $0.1270 | $-0.0394 (-24%) |
| `t14-csv-quoted` | 100% | 100% | 100% | 39s | 22s | 29s | $0.2102 | $0.1501 | $0.1776 | +$0.0274 (+18%) |
| `t15-jsonl-kinds` | 100% | 100% | 100% | 44s | 27s | 42s | $0.2241 | $0.1469 | $0.1685 | +$0.0216 (+15%) |
| `t16-ini-key` | 100% | 100% | 100% | 36s | 22s | 26s | $0.1891 | $0.1456 | $0.1395 | $-0.0061 (-4%) |
| `t17-log-errors` | 100% | 100% | 100% | 46s | 27s | 22s | $0.1924 | $0.1453 | $0.1318 | $-0.0135 (-9%) |
| `t2-imports` | 100% | 67% | 100% | 64s | 50s | 32s | $0.2817 | $0.2436 | $0.1491 | $-0.0945 (-39%) |
| `t3-todos` | 100% | 100% | 100% | 54s | 55s | 35s | $0.2456 | $0.2305 | $0.1613 | $-0.0692 (-30%) |
| `t5-base64` | 100% | 100% | 100% | 37s | 19s | 28s | $0.2051 | $0.1266 | $0.1207 | $-0.0059 (-5%) |

## Notes

- Empty + guidelines columns reproduce the original twobatch comparison; skills column is new.
- 3 skills were synthesized from twobatch's batch-1 trajectories by the `agent-wiki-synthesize-skill` skill: `extract-jpeg-exif-camera-optics`, `read-image-format-dimensions`, `count-csv-rows-with-quoted-fields`. All other tasks in this arm have **no matching skill** — the agent should fall through to whatever it'd do on an empty wiki.

