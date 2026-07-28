# Enabling Guidelines

Guidelines are short, actionable recommendations Evolve extracts from agent conversations ("trajectories") and stores as `guideline` entities. This guide covers how guideline generation works in **full Evolve (MCP server / CLI)** and how to choose between the two available generation methods, **regular** and **consistency**.

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
| `regular` (default) | Correctness on a single run | Single LLM pass over the trajectory; produces one guideline set. |
| `consistency` | Reliability across repeated runs | Resampling pass to score agent decision steps in the trajectory for consistency followed by a focused LLM pass to produce guidelines for inconsistent steps.  |
| `both` | Both | Runs both pipelines and stores both sets of guidelines side by side. |

```bash
# Regular guidelines (default) — no change needed
export EVOLVE_GUIDELINES_MODE=regular

# Consistency guidelines only
export EVOLVE_GUIDELINES_MODE=consistency

# Generate both
export EVOLVE_GUIDELINES_MODE=both
```

Or for a one-off Phoenix sync, set `--guidelines-mode` to `regular`, `consistency`, or `both`:

```bash
uv run evolve sync phoenix --guidelines-mode consistency
```

### Configuring consistency guideline generation

The consistency pipeline scores each decision step in a trajectory by resampling the decision multiple times and measuring how much the outcome varies, i.e. its uncertainty. The guideline-generation prompt is then steered toward the highest-uncertainty steps rather than summarizing the trajectory as a whole.

Consistency guideline generation is noticeably more costly (multiple resample LLM calls per trajectory instead of one) and is worth it when you specifically want to catch agent behavior that's unstable across runs — decisions that the agent sometimes gets right and sometimes doesn't. 

Tunable via:

| Variable | Default | Description |
|---|---|---|
| `EVOLVE_HIGH_UNCERTAINTY_THRESHOLD` | `0.2` | Steps scoring above this are treated as high-uncertainty |
| `EVOLVE_LOW_UNCERTAINTY_THRESHOLD` | `0.1` | Steps scoring below this are treated as stable |
| `EVOLVE_SKIP_ON_NO_UNCERTAINTY` | `true` | Skip guideline generation entirely if no step exceeds the uncertainty threshold |

The resampling behavior itself (sample count, per-step-type uncertainty metric) is defined in a YAML config file shipped alongside the consistency pipeline; advanced users calling `generate_consistency_guidelines()` directly from Python can point it at a custom config via `config_path=`.

## Verifying output

```bash
uv run evolve entities list <namespace> --type guideline
```

Each guideline's `metadata.generation_method` is `"regular"` or `"consistency"`, so you can tell which pipeline produced it when running in `both` mode. See [Guideline Provenance](low-code-tracing.md#6-understanding-guideline-provenance-metadata) for the full metadata schema, including `creation_mode` (`auto-mcp` vs `auto-phoenix` vs `manual`).

## See also

- [Configuration](configuration.md) — model selection (`EVOLVE_GUIDELINES_MODEL`) and other environment variables
- [Low-Code Tracing](low-code-tracing.md) — instrumenting your agent so traces reach Phoenix in the first place
- [Phoenix Sync](phoenix-sync.md) — batch guideline generation from traced trajectories
