# Experiments

Ad-hoc measurement scripts. Not part of the test suite — these aren't run in
CI, don't assert anything, and exist to produce numbers and writeups.

If a script here graduates into a regression check, move it under `tests/`.

## Scripts

### `token_savings.py`

Measures the token / wall-clock / step gap on utterance 2 when guidelines from
utterance 1 are recallable vs. not. Adapted from
`tests/e2e/test_claude_sandbox_learn_recall.py` but standalone — runs as a
script, prints a comparison table, writes results to `results/`.

**Requires:** Docker, the `claude-sandbox` image (`just sandbox-build claude`),
and `ANTHROPIC_API_KEY` (or `ANTHROPIC_AUTH_TOKEN`) in the environment.

**Run:**

```bash
# 3 runs per condition, fresh seed for every with-guidelines run
python3 experiments/token_savings.py --runs 3

# 5 measure runs against a single shared seed (cheaper, lower variance)
python3 experiments/token_savings.py --runs 5 --shared-seed

# Keep the per-run workspaces afterwards (transcripts on disk for inspection)
python3 experiments/token_savings.py --runs 5 --shared-seed --keep-workspaces
```

**Output** lands in `experiments/results/token_savings_<UTC-timestamp>/`:

- `report.md` — auto-generated comparison table + per-turn breakdown for one
  representative run per condition.
- `raw.json` — full `usage` payload from every run, plus per-turn usage parsed
  from each saved transcript.
- `summary.md` — hand-written writeup (when present) with sample tool-call
  traces and the contents of the recalled guidelines.
- `workspaces/` — only with `--keep-workspaces`. ~1–2 MB per run.

**Wall-clock budget:** roughly 25–35 min for `--runs 5`. The script prints
per-run progress so you can see where it is.

## Results layout

`experiments/results/token_savings_<timestamp>/` per run. The timestamp is the
UTC start time, so directory order = chronological order. Old result dirs are
kept as-is — don't rename.
