# Enabling Guidelines

Guidelines are short, actionable recommendations Evolve extracts from agent conversations ("trajectories") and stores as `guideline` entities. This guide covers how guideline generation works in **full Evolve (MCP server / CLI)** and how to choose between the two available generation methods, **standard** and **consistency**.

> This guide applies to full Evolve. It does not apply to [Evolve Lite](../integrations/claude/evolve-lite.md), where guideline extraction happens entirely inside the host agent's own reasoning (a prompt-driven skill) rather than through the LLM pipeline described here.

## Two ways trajectories reach the guideline pipeline

| Entry point | When it runs |
|---|---|
| `save_trajectory` MCP tool | Called directly by an MCP client (e.g. Claude Desktop, Claude Code) at the end of a conversation |
| `evolve sync phoenix` CLI | Pulls previously-traced conversations out of Arize Phoenix in a batch — see [Phoenix Sync](phoenix-sync.md), which requires traces already flowing in via [Low-Code Tracing](low-code-tracing.md) |

Both entry points funnel into the same underlying pipeline and respect the same `EVOLVE_GUIDELINES_MODE` setting below.

## Choosing a guideline generation mode

Set `EVOLVE_GUIDELINES_MODE` (or pass `--guidelines-mode` to `evolve sync phoenix`) to control which pipeline(s) run:

| Mode | Optimizes for | What it does |
|---|---|---|
| `standard` (default) | Correctness on a single run | Single LLM pass over the trajectory; produces one guideline set. |
| `consistency` | Reliability across repeated runs | Scores agent decision steps for consistency, then a focused LLM pass produces guidelines for the inconsistent ones. Two interchangeable **methods** compute that score — see [Choosing a consistency method](#choosing-a-consistency-method) below. |
| `all` | Both | Runs both pipelines and stores both sets of guidelines side by side. |

```bash
# Standard guidelines (default) — no change needed
export EVOLVE_GUIDELINES_MODE=standard

# Consistency guidelines only
export EVOLVE_GUIDELINES_MODE=consistency

# Generate both
export EVOLVE_GUIDELINES_MODE=all
```

Or for a one-off Phoenix sync, set `--guidelines-mode` to `standard`, `consistency`, or `all`:

```bash
uv run evolve sync phoenix --guidelines-mode consistency
```

## Choosing a consistency method

`consistency` mode has two interchangeable implementations, controlled by `EVOLVE_CONSISTENCY_METHOD` (or `--consistency-method` on `evolve sync phoenix`). **This setting only has an effect when `EVOLVE_GUIDELINES_MODE` is `consistency` or `all`** — it's silently ignored in `standard` mode.

| Method | How it estimates uncertainty | Cost |
|---|---|---|
| `fast` (default) | Asks the guideline-generation LLM to judge each step's stability itself, in the same call that produces guidelines — no resampling | Same as `standard` mode: one LLM call per generated subtask, or one for the whole trajectory when it isn't segmented |
| `accurate` | Resamples each decision step multiple times and measures how much the outcome varies across resamples | Several extra LLM calls per trajectory |

```bash
# Fast (default) — no change needed
export EVOLVE_CONSISTENCY_METHOD=fast

# Accurate — resample each step and measure actual variance
export EVOLVE_CONSISTENCY_METHOD=accurate
```

Or for a one-off sync:

```bash
uv run evolve sync phoenix --guidelines-mode consistency --consistency-method accurate
```

Use `accurate` when you want uncertainty estimated by actually observing variance across resamples, not the LLM's own self-assessment of confidence, or when you're comfortable paying for the extra resampling calls in exchange for a measured signal. Use `fast` (the default) when `accurate`'s per-trajectory resampling cost is too expensive to run at the volume you need.

`accurate` has further tuning knobs — the uncertainty thresholds that decide what counts as "high" vs "stable" (`high_uncertainty_threshold`, `low_uncertainty_threshold`), and whether to skip generation entirely when nothing looks uncertain (`skip_on_no_uncertainty`) — defined alongside the resampling config below; they're advanced settings, not something most readers need on a first pass. `fast` has no equivalent tunables today: it relies entirely on the prompt instructing the LLM to return no guidelines when it judges every step confident, rather than a pre-call numeric skip gate.

The resampling behavior (sample count, per-step-type uncertainty metric) and the `accurate`-only tuning knobs above are all defined in a YAML config file shipped alongside the consistency pipeline (`consistency_analyzer/agent_config.yaml`); advanced users calling `generate_consistency_guidelines()` directly from Python can point it at a custom config via `config_path=`.

**Mixed-provider Phoenix syncs and `accurate` resampling:** resampling forwards `EVOLVE_CUSTOM_LLM_PROVIDER` for every step, regardless of which model the traced step actually used — it's treated as a single deployment-wide routing setting, not per-trace. This is fine when every trajectory in a sync comes from the same provider. If a namespace mixes traces from different providers (e.g. `claude-*` and `gpt-*` traces synced from the same Phoenix project) and `EVOLVE_CUSTOM_LLM_PROVIDER` resolves to `openai` — which it does by default whenever `OPENAI_API_KEY` or `OPENAI_BASE_URL` is set, even if you never set it explicitly — resampling forces every step through the OpenAI provider regardless of the traced model, and non-OpenAI steps fail or get misrouted. For mixed-provider syncs, either set `EVOLVE_CUSTOM_LLM_PROVIDER` to match the traces you're resampling and run `accurate` once per provider, or use `fast` (the default), which never resamples and has no provider-routing step to get wrong.

## Verifying output

```bash
uv run evolve entities list <namespace> --type guideline
```

Each guideline's `metadata.generation_method` is `"standard"`, `"consistency"` (accurate method), or `"consistency-fast"` (fast method), so you can tell which pipeline — and, for consistency, which method — produced it when running in `all` mode. See [Guideline Provenance](low-code-tracing.md#6-understanding-guideline-provenance-metadata) for the full metadata schema, including `creation_mode` (`auto-mcp` vs `auto-phoenix` vs `manual`).

## See also

- [Configuration](configuration.md) — model selection (`EVOLVE_GUIDELINES_MODEL`) and other environment variables
- [Low-Code Tracing](low-code-tracing.md) — instrumenting your agent so traces reach Phoenix in the first place
- [Phoenix Sync](phoenix-sync.md) — batch guideline generation from traced trajectories
