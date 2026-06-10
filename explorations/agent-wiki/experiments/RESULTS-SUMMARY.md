# Experiment results — running summary

A consolidated index of empirical findings from the wiki experiments, in
the order they were run. The detailed reports for each experiment live in
their own files under `experiments/`; this is the running tape of what we
learned.

## 1. AGENTS.md A/B sweep (the original)

**Question**: does pointing an agent at `wiki-example/AGENTS.md`
alter behavior on a related task it didn't see at training time?

**Setup**: 4 conditions × 3 trials = 12 sandbox runs of a single utt2
("what lens model was used for @sample.jpg?"). Conditions varied how the
pointer was delivered (`baseline` / `skill` / `prompt` / `claude_md`).

**Headline results** (`experiments/results/wiki-consult-20260605T070417Z/`):

| Condition                      | read AGENTS.md | cited guideline | outcome match |
| ------------------------------ | -------------- | --------------- | ------------- |
| baseline                       | 0/3            | 0/3             | 0/3           |
| prompt (per-utterance hint)    | 3/3            | 3/3             | 3/3           |
| claude_md (soft phrasing)      | 1/3            | 1/3             | 1/3           |
| claude_md_strong (added later) | 3/3            | 3/3             | 3/3           |

**The two CLAUDE.md variants** (verbatim, from
`tests/e2e/experiment_wiki_consult.py`; `WIKI_NAME` = `wiki-example`):

- **`claude_md` (weak/soft)**:
  > Before non-trivial tasks in this repo, consult `wiki-example/AGENTS.md` for relevant guidelines.

- **`claude_md_strong` (strong)**:
  > Before doing anything else in this workspace, read `wiki-example/AGENTS.md` and follow its retrieval recipe to find guidelines that apply to your task. Cite the guidelines you used.

What makes the strong variant load-bearing — three differences:

| Axis | weak | strong |
| --- | --- | --- |
| **Timing** | "before non-trivial tasks" (agent judges what's non-trivial) | "before doing anything else" (unconditional, first action) |
| **Verb** | "consult … for relevant guidelines" (vague) | "read … and follow its retrieval recipe" (imperative + concrete procedure) |
| **Accountability** | none | "Cite the guidelines you used" |

**Finding**: a strong-imperative pointer in CLAUDE.md performs as well as
a per-utterance prompt hint. A *soft* CLAUDE.md ("Before non-trivial
tasks, consult …") got skipped 2/3 of the time — the hedge ("non-trivial")
lets the agent rationalize skipping. **Wording at the pointer site is
load-bearing.**

## 2. Persistent-pointer mechanism comparison

**Question**: does it matter where the strong-imperative pointer lives —
in CLAUDE.md, in `--append-system-prompt`, or in a SessionStart hook?

**Setup**: 3 mechanisms × 3 trials = 9 trials of the same lens-model task.

**Headline results**:

| Mechanism              | Reads AGENTS.md as Tool 1 | Median runtime    |
| ---------------------- | ------------------------- | ----------------- |
| SessionStart hook      | 3/3                       | **47s** (fastest) |
| claude_md_strong       | 3/3                       | 52s               |
| --append-system-prompt | 3/3 (but Tool 3+)         | 63s (slowest)     |

**Finding**: all 3 mechanisms hit the same accuracy. **System-prompt
placement costs ~10–15s of orientation latency** (`ls`, `which exiftool`,
etc.) before the agent reads AGENTS.md. The SessionStart hook places the
pointer above-the-fold, so the agent reads AGENTS.md as Tool 1 with no
orientation pre-amble.

## 3–4. Build-pattern comparison (closed-loop vs retroactive)

> **Omitted from this public exploration.** These two experiments compared
> *how* a wiki is built — closed-loop (the wiki grows between trials, each
> trial sees what prior trials spawned) vs retroactive (the wiki stays empty
> during all trials, then is ingested in batch). They ran against internal
> trajectory corpora, so the detailed report and per-trial data are not
> included here.

**Portable finding**: the same real-task themes emerged in *all* build
patterns (open-loop, closed-loop, retroactive) —
image-format-headers-via-struct, prefer-stdlib-module-for-format,
shell-pipelines-for-line-tasks. Consolidation is robust to build order; what
varies between patterns is meta-content, recall data, and per-task cost.
Closed-loop is the only pattern that accumulates real intra-wiki recall data
(trial N+1 demonstrably reads what trial N spawned); the others need post-hoc
attribution.

## 5. Two-batch wiki-helps experiment

**Question**: does the wiki *measurably* reduce token cost / duration /
tool calls at equal accuracy, on the same task, with vs without?

**Setup**: 16 tasks × 3 trials × 2 batches = 96 trials, all
`claude_md_strong`. Batch 1 ran against an empty wiki. Wiki built from
batch 1's trajectories, frozen. Batch 2 ran against the populated wiki.
Same prompts, same workspace seeding — only variable: wiki content.

**Headline results** (from `experiments/twobatch-comparison.md`):

| Metric                    | Batch 1 (empty) | Batch 2 (with wiki) |                       Δ |
| ------------------------- | --------------: | ------------------: | ----------------------: |
| **Median total cost USD** |           $0.21 |               $0.17 |                **−20%** |
| **Median duration**       |             43s |                 27s |                **−38%** |
| **Median tool calls**     |               7 |                   4 |                **−43%** |
| Median wiki reads         |               5 |                   3 |                    −40% |
| Median output tokens      |             406 |                 268 |                    −34% |
| Cache-read tokens         |               — |                   — |                    −32% |
| Cache-creation tokens     |               — |                   — | +66% (new pages cached) |
| **Aggregate accuracy**    |             96% |                 96% |               unchanged |

**Per-task highlights**:

- **Wiki rescued failures on lens-model**: 67% → **100%** accuracy.
- **t8-bmp-info batch-1 trial 1 timed out at 300s**; with-wiki, all 3
  BMP trials completed in 27s median. **11× speedup** on that task.
- **t5-base64 with empty wiki**: 300s timeout. With wiki: 18s, 23s, 20s
  (3/3 succeed). The `skip-for-trivial` guideline — recalled — let the
  agent short-circuit AGENTS.md's recipe.
- **Two regressions**: t12-wav-info (100% → 67%) and t2-imports
  (100% → 67%). One trial each failed in batch 2 — likely the agent
  over-applying or misreading a recalled guideline.

**Finding**: **wiki → faster, cheaper, fewer tools, equal accuracy.**
Per-task `total_cost_usd` is the ground-truth cost metric (cache reads
are billed at ~10% of regular input rate, so the raw token-sum proxy
overcounts). The −20% cost figure is robust to that pricing nuance.

Detailed report: [`experiments/twobatch-comparison.md`](twobatch-comparison.md).

## 6. Skills-arm of the wiki-helps experiment

**Question**: would a wiki populated only with synthesized **skills**
(executable workflow pages) — instead of free-text guidelines — beat
the guidelines arm on the same 16-task corpus?

**Setup**: identical to twobatch except batch 2 mounted
`wiki-twobatch-skills/`, an empty wiki populated by acting as the
`agent-wiki-synthesize-skill` agent on twobatch's batch-1 transcripts.
Per the skill's own rules (skip if trivial / single command, broad-
trigger names), three skills emerged:

- `extract-jpeg-exif-camera-optics` (covers t1)
- `read-image-format-dimensions` (covers t6/t7/t8/t9 via magic-byte dispatch)
- `count-csv-rows-with-quoted-fields` (covers t14)

Other 12 tasks have no matching skill — agent should fall through.

**Headline results**:

|                       |  Empty | Guidelines | Skills | Δ vs guidelines |
| --------------------- | -----: | ---------: | -----: | --------------: |
| Median total cost USD |  $0.21 |      $0.17 | **$0.146** |        **−14%** |
| Median output tokens  |    406 |        268 |    **206** |             −23% |
| Median wiki reads     |      5 |          3 |      **2** |             −33% |
| Aggregate accuracy    |   96%  |        96% |    **98%** |             +2% |
| Trials                | 47/48  |      48/48 |   48/48 |       (no timeouts) |

**Per-task standouts**:

- **t1-lens-model**: −28% cost. Direct skill match.
- **t2-imports**: −39% cost AND **67% → 100%** accuracy — *no skill matched*,
  but the simpler wiki (3 skills, no guidelines) led to a faster path.
- **t3-todos**: −30%; same pattern.
- **skip family** (t2/t3/t5): 89% → 100% accuracy.
- **t14-csv-quoted**: **+18% cost** despite a matching skill — the skill's
  overhead exceeded the savings on a 5-row CSV.
- **text family**: +6% (only family where skills hurt — 3 of 4 text tasks
  had no matching skill).

**Finding**: **skills > guidelines on aggregate cost, even where skills
don't match.** A smaller wiki (3 skills, no guideline noise) seems to
help recall on no-skill-match tasks too — the wiki-noise effect is real.

Detailed report: [`experiments/twobatch-skills-comparison.md`](twobatch-skills-comparison.md).

## 7. Both-arm: skills + guidelines together (4-way comparison)

**Question**: does combining skills + guidelines compose additively, or
is there an overhead?

**Setup**: same 16-task corpus, fourth arm. `wiki-twobatch-both` was
built from twobatch's batch-1 trajectories with BOTH the retroactive
guideline pipeline AND the synthesize-skill pipeline. End state: 47
summaries + 15 atomics + 3 skills.

**Headline 4-way aggregate**:

|                       |  Empty | Guidelines |     Skills |       Both | Both vs G | Both vs S |
| --------------------- | -----: | ---------: | ---------: | ---------: | --------: | --------: |
| Median total cost USD |  $0.21 |      $0.17 | **$0.146** |     $0.179 |       +5% |     +22% |
| Median output tokens  |    406 |        268 |        206 |        272 |        +1% |     +32% |
| Median wiki reads     |      5 |          3 |          2 |          2 |       −33% |       =  |
| Median guideline reads |     1 |          1 |          0 |          0 |       −1   |       =  |
| Aggregate accuracy    |    96% |        96% |       98%  |        98% |       +2  |       =  |

**Per-family `Δ S→B`** (both minus skills, in cost):

|         |  Δ |
| ------- | --: |
| text    | −1% |
| image   | +22% |
| lens-model | +17% |
| archive | +32% |
| skip    | +44% |

**Findings**:

1. **Composition is non-additive — and slightly punitive.** Both arm is
   the most expensive populated wiki: +22% vs skills, +5% vs guidelines.
2. **The penalty is largest on tasks WITHOUT a matching skill.** Skip
   family +44%, archive +32%. Adding guidelines on top of skills did
   not help where guidelines should have been the primary recall path.
3. **Behavioral signal**: median output tokens 206 → 272 — agent says
   more in the both arm. Wiki-reads count is identical (2 + 0). Cost
   increase isn't from extra reads; it's from longer responses (likely
   the agent citing both the skill it used + adjacent guideline context).
4. **t14-csv-quoted: +49% vs guidelines, +26% vs skills** — the most
   extreme regression. Having both the CSV skill AND the underlying CSV
   guideline available pushed cost higher than either alone.

**Conclusion**: **less wiki content + targeted (procedural) recall
wins.** Don't pile guidelines on top of skills; pick one or the other.

Detailed report: [`experiments/twobatch-fourway-comparison.md`](twobatch-fourway-comparison.md).

## 8. Pruned-arm: delete-on-promote policy (5-way comparison)

**Question**: §7 closed with the open question "if 'both' loses to
'skills-only', does 'skills + only the no-skill-coverage guidelines'
beat 'skills-only'?" This experiment tests that.

**Policy added** to the agent-wiki builder: when a cluster is rendered,
archive its member atomics; when a skill is synthesized, archive every
atomic the skill *covers* — inferred via three paths:

1. **Tag-superset**: skill's tags ⊇ atomic's tags AND ≥2 non-generic
   tags shared.
2. **Slug-keyword**: a non-stopword token (≥4 chars) from the skill
   slug appears in the atomic's title.
3. **Description-format-token**: an uppercase format identifier (e.g.
   `PNG`, `BMP`, `WebP`, `JPEG`) that appears in both the skill's
   description and the atomic's title.

Soft archive: moves to `<wiki>/_archived/<filename>` with an audit
log entry; recall data on archived atomics is discarded.

**Setup**: same 16-task corpus, same `claude_md_strong` condition.
`wiki-twobatch-pruned/` was built by the same pipeline that built
`wiki-twobatch-both/`, but with `--archive-covered` on each
synthesize-skill call. End state:

- 47 summaries
- 9 surviving atomics (all from no-skill-match tasks: zip, tar, wav,
  gzip, jsonl, ini, log, plus the imports/todos/base64 meta-atomics)
- 3 skills (same as skills/both arms)
- **6 archived atomics** (PNG, GIF, BMP, WebP, walk-EXIF-sub-IFD,
  use-stdlib-csv-reader) — exactly the atomics covered by the 3 skills

> **⚠️ Corrected 2026-06-10.** The numbers below are the **re-run** against
> a fixed index. The original §8 (commit `8bcd713`) ran the pruned arm
> against a wiki whose `_index.jsonl` was stale — `render-skill` archived the
> covered atomics but never refreshed the indexes, so the wiki exposed
> **0 skills, 15 guideline rows, and 6 broken links**. Agents never saw the
> skills and chased dangling guideline rows. Commit `2adc67a` fixed the
> builder (refresh indexes + integrity assertion after `render-skill` /
> `render-cluster`); this section reflects the corrected run. The original
> (broken) figures are kept in strikethrough for comparison.
> See [`pruned-index-hypothesis.md`](pruned-index-hypothesis.md).

**Headline 5-way aggregate** (Pruned = corrected re-run):

|                        |  Empty | Guidelines |     Skills |       Both |              Pruned | P vs S | P vs B |
| ---------------------- | -----: | ---------: | ---------: | ---------: | ------------------: | -----: | -----: |
| Median total cost USD  |  $0.21 |      $0.17 | **$0.146** |     $0.179 | $0.173 (~~$0.181~~)  |   +18% |    −3% |
| Median output tokens   |    406 |        268 |    **206** |        272 |     226 (~~290~~)    |    +9% |   −17% |
| Median wiki reads      |      5 |          3 |        2   |        2   |       2 (~~3~~)      |      = |      = |
| Median guideline reads |      1 |          1 |        0   |        0   |       0 (~~1~~)      |      = |      = |
| Aggregate accuracy     |    96% |        96% |       98%  |       98%  |       98%            |     =  |     =  |

**Per-family `Δ` (cost vs skills-only / vs both)** — corrected:

| Family     |   B vs S |    P vs S | P vs B |
| ---------- | -------: | --------: | -----: |
| lens-model |     +17% |      +30% |   +11% |
| image      |     +22% |      +33% |    +9% |
| archive    |     +32% |      +24% |    −6% |
| text       |      −1% |      −3%  |    −3% |
| skip       |     +44% |      +18% |   −18% |

**Findings** (corrected):

1. **The stale index was a real confound.** Fixing it cut the pruned arm's
   median cost $0.181 → **$0.173**, output tokens 290 → **226**, wiki reads
   3 → 2, and **guideline reads 1 → 0**. The broken arm's extra read and
   guideline-read were agents following dangling/archived rows that the
   correct index no longer exposes. The original "pruning is *worse* than
   both" result (+1%) flips to **−3% vs both** once the index is correct.

2. **But skills-only still wins.** Even corrected, pruned ($0.173) remains
   **+18% vs skills-only** ($0.146). The §7 open question still gets a "no":
   adding the no-skill-coverage atomics on top of skills does not beat
   skills-alone on aggregate cost.

3. **Pruning still costs on skill-match families, just far less.** Image
   +9% vs both (was +28%), lens-model +11% (was +79%). The dramatic
   skill-match penalty in the original was mostly the broken index; a
   smaller residual penalty remains — having sibling atomics in the index
   at all is slightly distracting even when a skill is the right answer.

4. **Pruning genuinely helps no-skill-match families.** Archive −6% vs both,
   skip −18% vs both, text −3%. Where there's no skill to fall through to,
   the leaner atomic list is a real (and now larger) win.

5. **Size *is* a lever once you control for index correctness — but a small
   one, and composition still dominates.** Corrected pruned (12 index rows)
   now sits between skills (12 rows) and both (18 rows), in the expected
   order — the earlier "smallest wiki yet most expensive" paradox was an
   artifact of the bug, not a real inversion.

6. **Same-session matcher variant is a wash.** Re-pruning through the
   *also*-fixed archive matcher (commit `1272097`, which keeps GIF/BMP/WebP
   the old loose matcher wrongly archived cross-session) yields a 12-atomic
   wiki. Its full-corpus median is **$0.175** (sum $8.23) — statistically
   indistinguishable from the 9-atomic arm. The 3 extra cross-session
   atomics cost essentially nothing.

7. **Both single-trial misses were known-flaky tasks, not regressions.**
   9-atomic missed t2-imports trial-1 (the prompt renders the module name as
   a blank placeholder — the agent correctly asked which module); 12-atomic
   missed t12-wav-info trial-2 (the same task that flaked to 67% in the
   guidelines/skills/both arms). 47/48 each.

**Operational implication** (revised): the original "don't expect pruning to
reduce cost" was too pessimistic — it was measuring a broken index. With a
correct index, **delete-on-promote is a net positive vs `both`** (−3%
aggregate, −6%/−18% on no-skill-match families) and is sound hygiene. But it
still doesn't beat **skills-only**, which remains the cheapest surface. If
cost is the only goal, ship skills-only; if you want to keep authored
guidelines for tasks no skill covers, pruned-on-a-fresh-index is a reasonable
middle and clearly better than stacking everything (`both`).

Detailed report: [`experiments/twobatch-fiveway-comparison.md`](twobatch-fiveway-comparison.md).

## Cross-experiment findings

1. **Wording > placement.** Strong-imperative pointer wording matters
   more than which channel delivers it. Soft CLAUDE.md got skipped; any
   strong-imperative variant succeeded.

2. **Same real-task themes emerge regardless of build pattern.** The
   3-cluster set (image-format-headers, stdlib-module, shell-pipelines)
   appears in open-loop, closed-loop, and retroactive builds.
   **Consolidation is robust.** What varies between builds is meta-
   content, recall data, and accuracy/cost on individual tasks.

3. **Closed-loop is the only build with real intra-wiki recall data.**
   Other builds need post-hoc attribution or cross-wiki references.
   Empirically demonstrated: trial N+1 reads what trial N spawned.

4. **The wiki materially reduces cost on identical tasks.** −20% cost,
   −38% duration, −43% tool calls in the controlled two-batch A/B at
   unchanged accuracy. Effect is largest on tasks where the recipe is
   non-obvious without the wiki (lens-model, BMP, base64-with-scope-
   warning).

5. **Cost reduction comes mainly from output tokens and tool-call
   reduction**, not from input-token compression. The agent doesn't read
   *fewer* bytes when it has the wiki — it reads MORE byes (cache-creation
   on guideline pages goes up). But it produces shorter responses and
   takes fewer tool turns.

6. **Two-batch experiment surfaced two regressions** (wav-info, imports)
   where the wiki may have *hurt* accuracy on one trial each. Worth
   investigating before scaling — the wiki's value isn't unconditional.

7. **Skills > guidelines on cost.** The skills arm (3 synthesized skills,
   no guidelines) beat the guidelines arm by 14% on median cost and
   matched it on accuracy (98% vs 96%). Largest savings on tasks with a
   direct skill match (t1-lens-model −28%) but ALSO on tasks where no
   skill matched (t2-imports −39%, t3-todos −30%) — suggesting the
   smaller wiki (less to scan) helps recall even when no recall fires.

8. **Skills + guidelines together is the worst populated wiki.**
   Combining the two arms (`wiki-twobatch-both`: same 3 skills + 15
   atomics) costs +22% vs skills and +5% vs guidelines. Composition is
   non-additive. Output tokens jump (206 → 272) without a corresponding
   reads increase — the agent talks more when both kinds of recall are
   available, even though it doesn't read more pages. **Implication: pick
   skills OR guidelines, not both.**

9. **Delete-on-promote beats `both` but not skills-only — and a stale
   index nearly hid that.** *(Corrected 2026-06-10, see §8.)* The pruned
   arm (3 skills + only the no-skill-coverage atomics) costs **−3% vs
   both** and **+18% vs skills** on a correctly-indexed wiki. The
   originally-reported +1%/+24% came from a builder bug: `render-skill`
   archived atomics without refreshing `_index.jsonl`, so the wiki
   exposed 0 skills and agents chased dangling guideline rows (commit
   `2adc67a` fixed it). Corrected, pruning *helps* no-skill-match
   families (archive −6%, skip −18% vs both) and costs only a small
   residual on skill-match ones (image +9%, lens-model +11% vs both,
   down from +28%/+79%). **Composition still dominates size, and
   skills-only is still cheapest** — but delete-on-promote is a net
   positive over stacking everything, not the wash the broken run
   suggested.

## File map

```
explorations/agent-wiki/experiments/
├── RESULTS-SUMMARY.md                     this file
├── twobatch-comparison.md                 with-wiki vs without-wiki A/B
├── twobatch-skills-comparison.md          3-way (empty / guidelines / skills)
├── twobatch-fourway-comparison.md         4-way (+ both arm)
├── twobatch-fiveway-comparison.md         5-way (+ pruned arm)
├── pruned-index-hypothesis.md             stale-index confound + correction
│
├── metrics/                               per-trial metric rollups (no raw transcripts)
│   ├── twobatch.metrics.jsonl             empty (batch-1) + guidelines (batch-2)
│   ├── twobatch-skills.metrics.jsonl
│   ├── twobatch-both.metrics.jsonl
│   └── pruned-fixed-9atomic.metrics.jsonl corrected pruned arm
│
└── harness/                               reproduce-it scripts
    ├── experiment_wiki_consult.py         sandbox A/B runner
    ├── wiki_consult_tasks.yaml            the 16-task corpus
    ├── extract_trial_metrics.py           per-trial token/duration/tool metrics
    ├── normalize_stream_json_transcripts.py  stream-json → OpenAI chat format
    ├── twobatch_compare.py                metrics → comparison markdown
    ├── threeway_compare.py                + skills column
    ├── fourway_compare.py                 + both column
    └── fiveway_compare.py                 + pruned column
```

> Raw per-trial transcripts (`results*/.../trial-N.jsonl`) are intentionally
> excluded from this public exploration; only the metric rollups under
> `metrics/` and the narrative reports are included. The comparison scripts
> read those rollups.

## Open questions worth pursuing

- **Statistical power.** Headline metrics are based on 3 trials per task.
  More trials would tighten the per-task confidence intervals,
  particularly on the regression cases (wav-info, imports).
- **Why wav-info and imports regressed.** Single-trial failures could be
  noise; could also be the agent following a recalled guideline that
  doesn't quite fit. Spot-check those transcripts.
- **Transfer test.** All experiments use the same task in batch 1 and
  batch 2. A real "transfer" experiment would test wiki-on-task-X with
  wiki-built-from-tasks-Y where X ≠ Y but X ∈ family(Y). That tests
  whether clusters generalize.
- **Larger corpus.** 16 tasks × 3 trials is a small experiment.
  Repeating with a 50-task corpus over more trials would test whether
  the cost-reduction percentage scales, regresses, or saturates.
- **Cross-pattern ensembling.** Could a wiki built closed-loop +
  retroactive (using the seeding from the former + the per-task
  templates from the latter) outperform either pattern alone?
- **Skill granularity sensitivity.** Skills arm used 3 broad skills.
  Would 16 narrow per-task skills do better or worse? Issue-260's prior
  finding (broad triggers 4/5 vs narrow 2/5) suggests broad wins, but
  per-task skills weren't tested on this corpus.
- **Why the both arm regresses on no-skill-match tasks.** Median wiki
  reads is identical between skills and both (2 + 0). The cost penalty
  is purely output-token-driven. A trace-level inspection of agent
  responses on archive/skip-family tasks would reveal whether the agent
  is citing nearby guidelines without reading them, or whether the
  presence of guidelines in the index is changing how it phrases its
  answer.
- **~~Pruning experiment~~ — answered in §8.** Pruned arm (3 skills +
  9 no-skill-coverage atomics) does NOT beat skills-only. Skills-only
  still wins on aggregate. The both-arm penalty is composition-driven,
  not index-size-driven.

- **Why does the pruned arm regress on skill-match tasks?** Pruning
  should be neutral or positive on tasks WITH a matching skill — the
  skill is unaffected and the index is smaller. Yet image and
  lens-model families regressed sharply vs both. A trace-level
  inspection of t1-lens-model trial 1 (which alone cost $0.488 in
  pruned vs $0.36 in skills) might reveal whether the agent is
  reading the surviving atomics out of curiosity or whether something
  about the AGENTS.md / index format changes its decision path.
