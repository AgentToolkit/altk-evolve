# Results

## AppWorld Benchmark

We evaluated Evolve on [AppWorld](https://appworld.dev), where agents complete realistic multi-step tasks via APIs, averaging 9.5 APIs across 1.8 apps. Hard tasks require more complex control flow across multiple services.

A ReAct agent received the task instruction plus the top 5 retrieved guidelines generated from one prior run on train/dev and was tested on an unseen partition (test-normal). We report Scenario Goal Completion (SGC), a strict consistency metric requiring success across scenario variants.

| Difficulty | Baseline SGC | + Evolve | Gain |
|---|---:|---:|---:|
| Easy | 79.0% | 84.2% | +5.2 |
| Medium | 56.2% | 62.5% | +6.3 |
| **Hard** | **19.1%** | **33.3%** | **+14.2** |
| **Aggregate** | **50.0%** | **58.9%** | **+8.9** |

### Key findings

- **Generalization:** The agent improves on unseen test tasks, showing it learns transferable principles rather than memorizing solutions.
- **Complexity scaling:** The harder the task, the more the agent benefits from learned guidelines. Hard tasks saw a 74% relative increase in success rate.
- **Consistency:** SGC gains exceeded raw pass-rate improvements, reducing "flaky" behavior across scenario variants. Guidelines help the agent solve tasks reliably, not just occasionally.

## Paper

For full details on the architecture, experiments, and analysis, see:

> [Trajectory-Informed Memory Generation for Self-Improving Agent Systems](https://arxiv.org/abs/2603.10600) (arXiv:2603.10600)
