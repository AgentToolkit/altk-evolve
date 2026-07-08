# Consistency Guidelines Integration

This document describes how consistency-based guideline generation has been integrated into altk-evolve, covering both supported creation modes and the key design decisions made.

---

## Background

altk-evolve supports two automatic guideline creation modes today:

| `creation_mode` | Trigger |
|---|---|
| `auto-phoenix` | `evolve sync phoenix` CLI — fetches agent trajectories from Arize Phoenix and generates guidelines in batch |
| `auto-mcp` | `save_trajectory` MCP tool — generates guidelines inline as a running agent stores its trajectory |

The consistency pipeline has been integrated into **both** of these modes. A third mode (`manual`) covers guidelines created directly via the `create_entity` MCP tool; it involves no automated generation and is unaffected by this change.

---

## What consistency guidelines add

Regular guidelines (`generate_guidelines`) apply a single LLM call to the full trajectory, segmented by subtask. This captures what the agent did, but not how reliably it did it.

Consistency guidelines (`generate_consistency_guidelines`) re-run each agent step N times with non-zero temperature, score the variance of the responses (using semantic similarity for text steps and Jaccard similarity for tool-call steps), and then ask an LLM to produce guidelines specifically focused on the steps that showed the highest uncertainty. This surfaces failure modes that only appear probabilistically — steps where the agent's behaviour is brittle or under-determined by its prompt.

---

## Vendored dependency: `consistency_analyzer`

The consistency analysis logic lives in `altk_evolve/llm/guidelines/consistency_analyzer/` — a vendored package derived from the `agent-consistency` research codebase, adapted for altk-evolve:

| File | Role |
|---|---|
| `resampling.py` | Re-executes each trajectory step N times via the LLM |
| `sample_preprocessing.py` | Parses sampled responses into a structured format |
| `single_step_consistency.py` | Computes per-step consistency scores |
| `consistency_metric.py` | Metric implementations: SBERT cosine similarity, Jaccard |
| `consistency_aggregator.py` | Aggregates step scores into a trajectory-level score |
| `consistency_analysis.py` | End-to-end pipeline: preprocessing → scoring → aggregation → score card |
| `inference_utils.py` | LiteLLM adapter (replaces IBM-specific provider dispatch from the original) |
| `utils.py` | Shared utilities (field extraction, flattening, weight rescaling) |

The original IBM-specific inference backend has been replaced with a single `litellm.completion()` call, matching the pattern used by `generate_guidelines` in the rest of altk-evolve. `sentence-transformers` is already a core dependency of altk-evolve; the `consistency` extra adds only `pyyaml`, `scipy`, and `pandas`.

---

## Trajectory Intermediate Representation (IR)

Before resampling, a trajectory is converted to an IR via `transform_trajectory_to_IR()`. Each assistant turn becomes a named step:

- **`OpenAIAgent_content`** — assistant turns whose response is plain text, when the trajectory carries an OpenAI `tools` schema (i.e. the agent used native tool-calling).
- **`OpenAIAgent_tool_calls`** — assistant turns whose response is a `tool_calls` list, same condition.
- **`AnyAgent_content`** — assistant text turns when no `tools` schema is present (e.g. smolagents `CodeAgent`, which describes tools in its system prompt rather than via the OpenAI protocol).

The step name drives which metric config is applied. The defaults are in `altk_evolve/llm/guidelines/consistency_analyzer/agent_config.yaml`:

```yaml
aggregation: mean
max_samples: 10      # LLM calls per step
max_steps: 15        # cap on steps resampled per trajectory

agents:
  - name: OpenAIAgent_content
    response_type: text
    metric: sbert_small
  - name: OpenAIAgent_tool_calls
    response_type: tool_calls
    fields:
      - name: function_name
        metric: jaccard
      - name: function_arguments
        metric: jaccard
  - name: AnyAgent_content
    response_type: text
    metric: sbert_small
```

A custom config can be passed via `config_path=` on `generate_consistency_guidelines()`.

---

## Guideline selection thresholds

Two constants in `consistency_guidelines.py` control which steps are surfaced to the LLM:

| Constant | Value | Effect |
|---|---|---|
| `LOW_UNCERTAINTY` | `0.1` | Minimum uncertainty for a step to be included when there are no high-uncertainty steps |
| `HIGH_UNCERTAINTY` | `0.2` | Steps above this are always included and labelled as low-success-probability |
| `SKIP_ON_NO_UNCERTAINTY` | `True` | Skip the LLM call entirely if no step exceeds `LOW_UNCERTAINTY` (trajectory is sufficiently consistent) |

---

## The `EVOLVE_GUIDELINES_MODE` environment variable

Both creation modes read a single environment variable to select which pipeline runs:

| Value | Behaviour |
|---|---|
| `"regular"` (default) | Run `generate_guidelines` only |
| `"consistency"` | Run `generate_consistency_guidelines` only |
| `"both"` | Run both pipelines; store all results in one `update_entities` call |

When `EVOLVE_GUIDELINES_MODE` is unset or empty, `"regular"` is used. An unrecognised value logs a warning and falls back to `"regular"`.

### `creation_mode` vs `generation_method`

Rather than multiplying `creation_mode` values, a new `generation_method` metadata field covers the pipeline dimension separately:

- **`creation_mode`** (`"auto-phoenix"` / `"auto-mcp"`) — **how** the guideline entered the system. Existing field, unchanged and backward-compatible.
- **`generation_method`** (`"regular"` / `"consistency"`) — **which pipeline** produced it. New field, added to all auto-generated guidelines going forward. Existing guidelines without this field are implicitly `"regular"`.

This lets downstream consumers filter on either dimension independently.

---

## Integration: `auto-phoenix` (Phoenix sync)

### CLI

```
EVOLVE_GUIDELINES_MODE=consistency evolve sync phoenix ...
```

### Merge point

Inside `_process_trajectory`, `guidelines_mode` is read from `EVOLVE_GUIDELINES_MODE` at call time, then:

```
generate_guidelines(messages)             → regular entity list  (generation_method: "regular")
generate_consistency_guidelines(traj)     → consistency entity list  (generation_method: "consistency")
                                                      ↓ concatenate
                                          guideline_entities  (merged list)
                                                      ↓
                              update_entities(enable_conflict_resolution=True)
```

In single-mode (`"regular"` or `"consistency"`), only the relevant branch runs. In `"both"` mode both pipelines execute and their results are stored in a single `update_entities` call.

---

## Integration: `auto-mcp` (MCP `save_trajectory` tool)

### Tool signature

The `save_trajectory` tool signature is unchanged. Pipeline selection is controlled by `EVOLVE_GUIDELINES_MODE` on the server process, not by a tool argument. Set it before starting the MCP server to enable consistency or both-mode guideline generation.

The consistency pipeline uses `llm_settings.guidelines_model` for resampling (configured via `EVOLVE_GUIDELINES_MODEL` or `EVOLVE_MODEL_NAME`). Without a `tools` schema (the MCP tool only receives raw messages), steps are classified as `AnyAgent_content`, which is correctly handled by `consistency_analyzer/agent_config.yaml`.

### Merge point

Same pattern as phoenix sync — entity lists are built per pipeline inside `save_trajectory`, tagged with `generation_method`, concatenated, then sent in a single `update_entities` call.

---

## Debug artifacts

`generate_consistency_guidelines` can write intermediate artifacts to disk for inspection: the trajectory IR, the resampled IR, the consistency score card, and the generated guidelines — all as JSON files. This is an internal development tool for the altk-evolve team and is not exposed in any consumer-facing interface.

To enable it, set the environment variable:

```
EVOLVE_DEBUG_DIR=/path/to/dir
```

When set, artifacts are written automatically by `generate_consistency_guidelines` whenever it runs, regardless of which creation mode triggered it (`auto-phoenix` or `auto-mcp`). When the variable is unset (the default), no artifacts are written.

---

## What is not yet supported

- **`auto-mcp` with a `tools` schema**: The `save_trajectory` MCP tool receives raw messages only. Passing a `tools` schema is not yet supported, so tool-calling agents using the MCP path are always classified as `AnyAgent`. This is a known limitation for a future iteration.
