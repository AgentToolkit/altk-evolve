# Agent Wiki PR Review Findings

Date: 2026-06-10

Branch reviewed: `explorations/agent-wiki`

Base: `public/main`

## Open Findings

### P2: `--out-root` crashes when passed an absolute path

`experiment_wiki_consult.py` accepts `--out-root`, and the path handling
correctly writes to an absolute output directory. At summary-render time,
however, it calls:

```python
runs_path.relative_to(REPO_ROOT)
transcripts_dir.relative_to(REPO_ROOT)
```

If the caller passed an absolute path outside `REPO_ROOT`, the run gets to the
end and then raises `ValueError`.

Repro:

```bash
tmp=$(mktemp -d /tmp/agent-wiki-review-harness.XXXXXX)
.venv/bin/python explorations/agent-wiki/skills/scripts/build_agent_wiki.py \
  --wiki-root "$tmp/wiki" catalog >/dev/null
.venv/bin/python explorations/agent-wiki/experiments/harness/experiment_wiki_consult.py \
  --wiki "$tmp/wiki" --trials 0 --task t1-lens-model \
  --out-root /tmp/agent-wiki-review-results
```

Observed failure:

```text
ValueError: '/tmp/agent-wiki-review-results/.../runs.jsonl' is not in the subpath of
'/Users/vinod/data/digitallabor/src/agents/altk-evolve.llmwiki/explorations/agent-wiki'
```

Relevant code:

- `explorations/agent-wiki/experiments/harness/experiment_wiki_consult.py`
  - summary footer renders `runs_path.relative_to(REPO_ROOT)`
  - summary footer renders `transcripts_dir.relative_to(REPO_ROOT)`

Suggested fix: use a helper that returns `path.relative_to(REPO_ROOT)` when
possible, and falls back to `str(path)` otherwise.

### P2: secrets scanning excludes the entire exploration tree

The PR excludes all of `explorations/agent-wiki/` from detect-secrets in both
pre-commit and `.secrets.baseline`.

Relevant config:

```yaml
# .pre-commit-config.yaml
exclude: 'package.lock.json|^explorations/agent-wiki/'
```

```json
// .secrets.baseline
"files": "^.secrets.baseline$|package-lock\\.json$|^explorations/agent\\-wiki/"
```

This was originally justified by generated example wiki content and schema
examples tripping high-entropy detection. The generated wikis have since moved
out of this PR, so the exclusion is broader than the stated reason. It also
means future scripts/docs added under this exploration will not be scanned for
secrets.

Suggested fix: narrow the exclusion to the specific generated/metrics/schema
files that cause false positives, or baseline those false positives directly.

## Previously Reported Issues Now Resolved

### Fresh `catalog` bootstrap

Fresh bootstrap now succeeds and creates:

- `AGENTS.md`
- `_config.yaml`
- `_index.jsonl`
- `guidelines/index.md`
- `index.md`
- `skills/index.md`
- `summaries/index.md`
- `tasks/index.md`

Smoke test:

```bash
tmp=$(mktemp -d /tmp/agent-wiki-review-fresh.XXXXXX)
.venv/bin/python explorations/agent-wiki/skills/scripts/build_agent_wiki.py \
  --wiki-root "$tmp/wiki" catalog
find "$tmp/wiki" -maxdepth 2 -type f | sort
```

Result: passed.

### Standalone skill command paths

Skill docs now point at checked-in standalone paths under
`explorations/agent-wiki/...`, including:

- `explorations/agent-wiki/skills/scripts/build_agent_wiki.py`
- `explorations/agent-wiki/experiments/harness/normalize_stream_json_transcripts.py`

Result: resolved.

### Harness task loading

`experiment_wiki_consult.py` now loads:

```python
Path(__file__).resolve().parent / "wiki_consult_tasks.yaml"
```

instead of the old missing `tests/e2e/wiki_consult_tasks.yaml` path.

Result: resolved.

### Cluster archive links

`render-cluster --archive-members` now links archived member guidelines as:

```text
../_archived/<guideline>.md
```

Smoke test confirmed the cluster page points to `../_archived/...` and the
archived file exists.

Result: resolved.

### README example-wiki wording

README now says the example wikis are shipped in a companion PR and are not
part of this split-down diff.

Result: resolved.

## Checks Run

```bash
git diff --check public/main...HEAD
```

Result: passed.

```bash
uv run ruff check explorations/agent-wiki/experiments/harness explorations/agent-wiki/skills/scripts
uv run ruff format --check explorations/agent-wiki/experiments/harness explorations/agent-wiki/skills/scripts
uv run python -m compileall -q explorations/agent-wiki/skills/scripts explorations/agent-wiki/experiments/harness
```

Result: passed.

Additional smoke tests:

- Fresh `catalog` bootstrap: passed.
- Cluster archive/link behavior: passed.
- Comparison scripts for `twobatch`, `threeway`, `fourway`, and `fiveway`: passed.
- `experiment_wiki_consult.py --out-root /tmp/...`: reproduced the open
  absolute-path crash above.
