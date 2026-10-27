# Results

Published evaluations of Evolve cover task completion, repeat-run consistency, and memory efficiency on [AppWorld](https://appworld.dev) and [τ²-bench](https://github.com/sierra-research/tau2-bench). Performance depends on the agent, model, memory configuration, and evaluation protocol. Each section identifies the setup and links to its reported evidence.

The studies use three metrics:

- **Task Goal Completion (TGC):** the percentage of individual AppWorld tasks completed successfully, as checked by the benchmark's programmatic grader.
- **Scenario Goal Completion (SGC):** the percentage of AppWorld scenarios for which every task variant succeeds, usually three variants per scenario.
- **Pass^k:** success across all k attempts at the same task. The repeated-run studies below use five attempts on AppWorld and four on τ²-bench. **Mean accuracy** averages success across those attempts.

SGC measures success across related task variants; Pass^k measures success across repeated attempts. Completion and accuracy values are percentages, and absolute gains are reported in percentage points (pp).

## Generalization to unseen tasks

A GPT-4.1 ReAct agent was evaluated on AppWorld's held-out `test-normal` partition. Guidelines were generated from trajectories on the train and dev partitions. The configuration below uses subtask-level guidelines with LLM-guided selection, retrieving the top five guidelines before reasoning, with a maximum of 30 reasoning-action steps per task. It achieves the highest aggregate SGC among the configurations reported in the technical paper.

| Difficulty | Baseline TGC | + Evolve TGC | Baseline SGC | + Evolve SGC |
|---|---:|---:|---:|---:|
| Easy | 89.5% | 91.2% | 79.0% | 89.5% |
| Medium | 66.7% | 70.8% | 56.2% | 56.2% |
| Hard | 54.0% | 58.7% | 19.1% | 47.6% |
| **Aggregate** | **69.6%** | **73.2%** | **50.0%** | **64.3%** |

Aggregate task completion improves by **+3.6 pp**, and scenario completion by **+14.3 pp**. Hard-task scenario completion rises from **19.1% to 47.6% (+28.5 pp)**. Medium-task scenario completion is unchanged, even though individual task completion improves. The effect therefore depends on both difficulty and metric.

These results test transfer from train/dev experience to unseen tasks. The [technical paper](https://arxiv.org/html/2603.10600v1) reports additional extraction and retrieval configurations; the [Memory as Middleware paper](https://arxiv.org/abs/2609.32091) reproduces this table in its preliminary evaluation.

## Repeat-run consistency

A separate study evaluates a GPT-4.1 ReAct agent on AppWorld `test_normal` (168 tasks), with five attempts per task. Consistency guidelines are generated from one baseline trajectory per task. Same-task evaluation applies those guidelines to fresh attempts at that task; sibling-task evaluation applies guidelines from other variants of the same scenario.

| Guidelines from | Pass^5 | Gain vs. baseline | Mean@5 | Gain vs. baseline |
|---|---:|---:|---:|---:|
| No guidelines (baseline) | 53.0% | — | 77.4% | — |
| Same task | 69.0% | +16.0 pp | 81.0% | +3.6 pp |
| Sibling task | 66.0% | +13.0 pp | 79.5% | +2.1 pp |

The **53% → 66%** result measures transfer between related scenario variants. The **53% → 69%** result measures improved repeat use of the same task. Both improve aggregate mean accuracy, alongside the stricter all-five-attempts metric.

Guideline generation has an additional offline cost: the paper's Consistency Analyzer uses 30 resamples per inference step, requiring roughly 30 times the tokens for each trajectory analyzed. That analysis cost is separate from the runtime inference-token figures in the next section.

Sources: [Closing the Consistency Gap](https://arxiv.org/abs/2609.08832), especially the evaluation and Appendix Table 1; [Your Agent Aced the Task. Will It Do It Again?](https://huggingface.co/blog/ibm-research/altk-evolve-consistency).

## Model coverage and efficiency

The reported eight-model sweep evaluates different ways to deliver learned guidelines. The examples below use AppWorld `test_normal`; the article describes guideline mining from the training split only. Each row shows the best of the tested delivery configurations for that model.

**Curated retrieval** supplies a fixed, high-confidence core plus task-relevant guidelines. The **full guideline set** supplies every mined guideline at each ReAct step.

| Model | Best tested delivery | TGC: baseline → Evolve | SGC: baseline → Evolve |
|---|---|---:|---:|
| gpt-oss-120b | Curated retrieval | 39.9% → 56.0% | 21.4% → 37.5% |
| DeepSeek-V3.2 | Full guideline set | 79.8% → 89.3% | 64.3% → 80.4% |
| Claude Opus 4.6 | Full guideline set | 90.5% → 94.6% | 87.5% → 94.6% |
| GPT-5.5 | Full guideline set | 92.3% → 95.2% | 82.1% → 89.3% |
| GLM-5 | Full guideline set | 87.5% → 87.5% | 80.4% → 80.4% |

The results show benefits for several models, including ones with high baseline completion, and no measured gain for GLM-5 in this setup. Memory delivery is a configuration choice to evaluate for the model and task distribution.

### Runtime token usage

The article reports average inference tokens per task, accumulated across agent steps. These figures describe runtime usage; guideline generation is an additional operation. Token counts are rounded as reported (K = thousand tokens).

| Model | Delivery | No-memory tokens/task | Evolve tokens/task |
|---|---|---:|---:|
| gpt-oss-120b | Curated retrieval | 110K | 116K |
| gpt-oss-120b | Full guideline set | 110K | 166K |
| DeepSeek-V3.2 | Full guideline set | 148K | 263K |

With curated retrieval, gpt-oss-120b gains **+16.1 pp task completion**. Supplying all guidelines incurs substantially more tokens for both models in the table.

Source: [How Much Memory Does Your Agent Actually Need?](https://huggingface.co/blog/ibm-research/altk-evolve-hmm).

### In-house comparison with ACE

The ACE comparison uses in-house, single-run evaluations on AppWorld `test_normal` (168 tasks), with the same base models and ReAct harness. Memory is mined from train/dev. The systems use different prompt templates and have different no-memory baselines; the table shows their reported post-memory performance and inference tokens.

For the repeated Evolve scores, the articles describe different guideline-mining splits: train only in the memory-dosage article and train/dev in the ACE comparison. The relationship between those runs remains unclear in the published descriptions.

| Model | ACE TGC / SGC | Evolve TGC / SGC | ACE tokens/task | Evolve tokens/task |
|---|---:|---:|---:|---:|
| gpt-oss-120b | 54.8% / 35.7% | 56.0% / 37.5% | 777K | 116K |
| DeepSeek-V3.2 | 80.4% / 73.2% | 89.3% / 80.4% | 634K | 263K |

For gpt-oss-120b, Evolve uses about **one-seventh of the inference tokens**. The article treats task completion as approximately tied: an Evolve repeat run scored 54.8%, matching ACE. For DeepSeek-V3.2, Evolve reports higher completion with about **41% of ACE's inference tokens**. These are token comparisons; monetary cost depends on model pricing and caching.

Source: [Thinking of ACE? We Can Do It with Fewer Tokens](https://huggingface.co/blog/ibm-research/altk-evolve-sldd), including its method notes and reference tables.

## Customer-service tasks

The τ²-bench experiments use the benchmark's native ReAct scaffold (THINK → ACT), with learned guidelines appended after the domain policy in the system prompt. Guidelines are learned from each domain's train split using the agent model. Evaluation uses τ²-bench v1.0.1, GPT-5.5 as the customer simulator, and four attempts per task.

### Held-out test results

The table below uses **40 retail test tasks** and **20 airline test tasks**. `sup3` retains guidelines supported by at least three training tasks; `sup2` uses a threshold of two. “All” retains every consolidated guideline, including support-one guidelines. Counts appear in parentheses.

| Domain | Agent model | Guidelines | Mean accuracy | Pass^4 |
|---|---|---|---:|---:|
| Retail (40 test tasks) | GPT-4.1 | None | 78.1% | 57.5% |
| Retail (40 test tasks) | GPT-4.1 | sup3 (31) | 78.8% | 60.0% |
| Retail (40 test tasks) | GPT-4.1 | All (292) | 82.5% | 67.5% |
| Airline (20 test tasks) | GPT-4.1 | None | 57.5% | 35.0% |
| Airline (20 test tasks) | GPT-4.1 | sup3 (14) | 60.0% | 45.0% |
| Airline (20 test tasks) | GPT-4.1 | All (101) | 65.0% | 40.0% |
| Airline (20 test tasks) | GPT-5.5 | None | 72.5% | 55.0% |
| Airline (20 test tasks) | GPT-5.5 | sup2 (103) | 80.0% | 70.0% |
| Airline (20 test tasks) | GPT-5.5 | All (146) | 82.5% | 70.0% |

On retail, GPT-4.1 gains **+4.4 pp mean accuracy** and **+10.0 pp Pass^4** with all guidelines. On airline, GPT-5.5 gains **+10.0 pp mean accuracy** and **+15.0 pp Pass^4** with all guidelines. GPT-4.1's airline result illustrates the delivery tradeoff: all guidelines give the highest mean accuracy, while the smaller sup3 set gives the highest Pass^4.

The article also reports cost on the full base split: retail's combined agent and customer-simulator cost increases from approximately **$0.084 to $0.139 per conversation (+65%)** with all guidelines. This cost table covers a different task set from the held-out table above.

Source: [ALTK-Evolve: How learned memory tops the τ²-bench leaderboard on customer service tasks without a frontier model](https://huggingface.co/blog/ibm-research/altk-evolve-bc), particularly Tables 2, 3, and 5.

### Submitted full-base results and leaderboard snapshot

The October 20 article compares full-base submissions with a leaderboard snapshot from **October 12, 2026**. The benchmark's base split includes both train and test tasks, so it includes tasks used to learn guidelines and differs from the held-out evaluation above.

| Domain | Agent model | Base tasks × attempts | Submitted Pass^4 | Submission |
|---|---|---:|---:|---|
| Retail | GPT-4.1 | 114 × 4 | 67.54% | [PR #542](https://github.com/sierra-research/tau2-bench/pull/542) |
| Airline | GPT-5.5 | 50 × 4 | 82.00% | [PR #551](https://github.com/sierra-research/tau2-bench/pull/551) |

As of **October 27, 2026**, both submission PRs are open. Both disclose `modified_prompts: true`, training-task overlap, and an expected **Unverified** status. The article's ranking comparisons refer to its dated snapshot; the held-out tables provide the evidence for transfer to unseen tasks.

## April launch evaluation

The [April 8 introduction](https://huggingface.co/blog/ibm-research/altk-evolve) reports the following AppWorld SGC table. The [April 7 IBM announcement](https://www.ibm.com/new/announcements/altk-evolve-on-the-job-learning-for-ai-agents) highlights the aggregate **+8.9 pp** and hard-task **+14.2 pp** gains. The introduction describes a ReAct agent receiving the top five guidelines generated from prior train/dev trajectories and evaluated on unseen `test-normal` tasks.

| Difficulty | Baseline SGC | + Evolve SGC | Gain |
|---|---:|---:|---:|
| Easy | 79.0% | 84.2% | +5.2 pp |
| Medium | 56.2% | 62.5% | +6.3 pp |
| Hard | 19.1% | 33.3% | +14.2 pp |
| **Aggregate** | **50.0%** | **58.9%** | **+8.9 pp** |

These are the historical figures behind the original homepage headline, including the approximately 74% relative improvement on hard tasks. The [generalization section](#generalization-to-unseen-tasks) presents the best SGC configuration reported in the technical papers with its stated setup.
