# Experiments

Measurement scripts and research pipelines that run against this checkout of
Evolve. Nothing here is packaged (only `altk_evolve*` ships), but the code is
linted, type-checked and collected by pytest like the rest of the repo.

Each subdirectory is one experiment family:

| Directory | What it measures | Needs |
|-----------|------------------|-------|
| [`claude_sandbox/`](claude_sandbox/) | Token, wall-clock and step savings when Claude Code recalls guidelines or synthesized skills | Docker, the `claude-sandbox` image, Anthropic credentials |
| [`guideline_pipeline/`](guideline_pipeline/README.md) | Guidelines mined from benchmark datasets through processing profiles, via per-dataset adapters | An Evolve backend with atomic writes; LLM credentials for the chosen profile |

Results, datasets and run output are written next to the code that produced
them (for example `claude_sandbox/results/`) and are not committed; see
[`.gitignore`](.gitignore).

If a script here graduates into a regression check, move it under `tests/`.

## `claude_sandbox/`

Standalone scripts adapted from `tests/e2e/test_claude_sandbox_learn_recall.py`.
They aren't run in CI and don't assert anything; they print a comparison table
and write results to `experiments/claude_sandbox/results/`.

**Requires:** Docker, the `claude-sandbox` image (`just sandbox-build claude`),
and `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) in the environment.

### `token_savings.py`

Measures the token / wall-clock / step gap on utterance 2 when guidelines from
utterance 1 are recallable vs. not.

```bash
# 3 runs per condition, fresh seed for every with-guidelines run
python3 experiments/claude_sandbox/token_savings.py --runs 3

# 5 measure runs against a single shared seed (cheaper, lower variance)
python3 experiments/claude_sandbox/token_savings.py --runs 5 --shared-seed

# Keep the per-run workspaces afterwards (transcripts on disk for inspection)
python3 experiments/claude_sandbox/token_savings.py --runs 5 --shared-seed --keep-workspaces
```

**Output** lands in `experiments/claude_sandbox/results/token_savings_<UTC-timestamp>/`:

- `report.md` — auto-generated comparison table + per-turn breakdown for one
  representative run per condition.
- `raw.json` — full `usage` payload from every run, plus per-turn usage parsed
  from each saved transcript.
- `summary.md` — hand-written writeup (when present) with sample tool-call
  traces and the contents of the recalled guidelines.
- `workspaces/` — only with `--keep-workspaces`. ~1–2 MB per run.

**Wall-clock budget:** roughly 25–35 min for `--runs 5`. The script prints
per-run progress so you can see where it is.

### `skill_from_trajectory.py`

Compares three recall conditions on the same utterances: no recall, recalled
guidelines, and a skill synthesized from the seed trajectory by
`/evolve-lite:synthesize-skill`. Reuses the helpers in `token_savings.py`.

```bash
python3 experiments/claude_sandbox/skill_from_trajectory.py --trials 5
```

**Output** lands in `experiments/claude_sandbox/results/skill_from_trajectory_<UTC-timestamp>/`
(`report.md`, `raw.json`, `synthesized_skills/`).

### Results layout

One `results/<script>_<timestamp>/` directory per run. The timestamp is the UTC
start time, so directory order = chronological order. Old result dirs are kept
as-is — don't rename.
