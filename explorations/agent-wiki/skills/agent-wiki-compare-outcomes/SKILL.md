---
name: agent-wiki-compare-outcomes
description: Compare successful and failed normalized agent trajectories to derive evidence-backed agent-wiki guidelines. Use when Codex has multiple runs for the same or similar task, evaluator outcomes, failed/successful variants, benchmark trajectories, or wants to learn rules from contrasts rather than from one trajectory alone.
---

# Agent Wiki — Compare Outcomes

## Overview

Use this pass after summarize/extract/synthesize when there are multiple
trajectories that can be judged as successful or failed. It can judge
outcomes with an LLM from the normalized transcript, so it does not need to
depend on benchmark-specific success/failure labels. It derives
**contrastive guidelines**: rules that are supported by a failed path, a
successful path, and concrete evidence from task wording, tool/API
documentation, tool/API calls, transcript evidence, optional failure snippets,
and optionally an LLM success/failure judgment.

This pass exists to avoid hand-authored domain knowledge. Do not write a rule
just because you know the benchmark or application. Write a rule only when the
input trajectories contain the evidence.

## Workflow

### Step 1: Build an Evidence Pack

Run the bundled script over normalized trajectory JSON files:

```bash
uv run python explorations/agent-wiki/skills/agent-wiki-compare-outcomes/scripts/compare_outcomes.py \
  --input <normalized-dir-or-json> \
  --out-json <analysis.json> \
  --out-md <analysis.md> \
  --judge-outcomes always
```

Pass `--input` multiple times to compare several experiment arms.

The script groups traces by `metadata.task_id` when present; otherwise it uses
a normalized task request. For each group it compares successful and failed
runs, then extracts:

- task request text;
- stored outcome and failure snippets when present;
- LLM-judged outcome when `--judge-outcomes missing` or
  `--judge-outcomes always` is set;
- observed tool/API calls from `stats.top_tools`, code snippets, and source
  `api_calls.jsonl` when available;
- tool/API descriptions shown in the trajectory transcript.

Judging modes:

- `--judge-outcomes never`: use only stored `outcome.success`.
- `--judge-outcomes missing`: judge only traces without stored outcomes.
- `--judge-outcomes always`: ignore stored success labels and use the LLM
  judgment for all traces.

Prefer `--judge-outcomes always` when the available stored labels come from a
benchmark evaluator or another dataset-specific schema. Use stored outcomes
only when they are trusted, dataset-neutral annotations you are comfortable
using as ground truth.

Use `--judge-include-failures` when generic failure reports or evaluator
snippets are available and you want the LLM to interpret them. This does not
require benchmark-specific code; the snippets are passed as opaque evidence.
Without failure snippets or ground truth, an LLM can still identify obvious
tool errors, step-limit failures, missing finalization, or apparent success,
but it may not detect silent semantic mismatches.

### Step 2: Inspect Candidate Rules

Read the generated Markdown. A candidate is promotable only if it has:

- at least one failed trajectory and one successful trajectory in the same
  group;
- a task-action tool/API or workflow difference between them, not just
  authentication, documentation lookup, or finalization calls;
- a comparison between plausible alternatives in the same tool namespace,
  unless the transcript evidence clearly supports a cross-namespace workflow
  rule;
- evidence that the successful tool/API is more semantically aligned with the
  current task wording, or that the transcript/failure evidence names the
  failed side effect;
- source trajectory IDs for both sides.

If the evidence is incomplete, keep it as a hypothesis. Hypotheses are useful
for evaluation notes but should not be promoted into future-agent instructions.

### Step 3: Promote Carefully

When a candidate is strong, render it as a guideline with provenance:

```json
{
  "entities": [
    {
      "type": "guideline",
      "title": "Choose record source from task wording",
      "content": "Apply this rule only when the live choice is between the observed successful and failed APIs, or between APIs with the same documented meanings. Prefer the successful source when the request matches its observed documentation. Do not apply this rule when the request explicitly uses failed-side terms; inspect the failed-side source instead. Do not generalize this rule to other record families or unrelated APIs unless a separate contrast includes those APIs.",
      "rationale": "In the contrasted trajectories, failed runs used a feed endpoint for a task about the user's own transactions, while the successful run used the documented account-owned transaction endpoint.",
      "trigger": "Use only when choosing between the observed successful and failed APIs and the task wording aligns with the successful-side documentation; skip when the task explicitly mentions failed-side terms or asks about a different record family.",
      "session_id": "<comparison-id>",
      "agent": "agent-wiki-compare-outcomes",
      "tags": ["contrastive", "tool-selection", "data-source-routing"],
      "normalized_path": "<analysis.json>"
    }
  ]
}
```

Pipe through the normal helper:

```bash
cat /tmp/contrastive-guideline.json | uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> render-guidelines
uv run python explorations/agent-wiki/skills/scripts/build_agent_wiki.py --wiki-root <wiki-root> catalog
```

## Guardrails

- Do not derive rules from outcome labels or private evaluator data alone.
  Outcome labels and LLM judgments can identify which side failed, but the
  proposed future behavior must come from trajectory-visible task wording,
  observed calls, or observed documentation.
- Do not invent tool/API names. A concrete name must appear in a call or in
  retrieved documentation.
- Prefer generic rule wording first, with tool-specific examples under
  evidence. The wiki can specialize only where the evidence supports it.
- Keep triggers narrow. Name the observed successful and failed API pair, add
  the successful-side positive terms, and add explicit counter-scope for
  failed-side terms and unrelated record families.
- Record counterexamples: if a failed and successful run used the same tool,
  this pass did not identify a source-selection rule.
- Keep confidence explicit. High confidence requires at least one success, one
  failure, and a clear successful-only vs failed-only behavior difference.
