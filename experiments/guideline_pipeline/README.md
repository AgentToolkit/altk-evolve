# Guideline pipeline

Turns benchmark trajectories into guidelines with this checkout of Evolve. A
dataset **adapter** reads one dataset format and yields records; a **stage**
consumes them. This directory currently has one stage, `mine`.

## Running `mine`

`mine` sends every record through `EvolveClient.process_trajectory` with a
published processing profile, so generated guidelines carry profile provenance,
memory hooks run, and each record is checkpointed.

```bash
# 1. Publish a processing profile once (see docs/design/processing-profiles.md).
uv run evolve processing-profiles apply guidelines-consistency --file profile.json --expected-revision 0

# 2. Check that the adapter can read the input, without touching storage.
uv run python -m experiments.guideline_pipeline mine --adapter <name> --input <path> \
    --namespace <ns> --processing-profile guidelines-consistency --dry-run

# 3. Mine.
uv run python -m experiments.guideline_pipeline mine --adapter <name> --input <path> \
    --namespace <ns> --processing-profile guidelines-consistency [--revision N] [--limit N]
```

- The backend comes from the usual `EVOLVE_*` settings (`.env`). It must support
  atomic writes — **filesystem** (the default) or **postgres**. Milvus is rejected
  up front, because Evolve only checkpoints batches on backends that commit
  outputs and checkpoints together.
- The profile is resolved once per run; without `--revision` that is the latest
  revision at start-up. The summary line reports which revision ran.
- Re-running is idempotent. Records whose batch already committed are counted
  as `skipped (checkpointed)`; only new or failed records, and records with a
  new batch revision, are processed. To deliberately regenerate everything, change the adapter's batch
  scope or use a new namespace — changing the profile does not replay.
- A record that fails (for example a transient LLM error) is printed to stderr
  and the run continues. The exit code is 1 if anything failed, 2 for a usage
  error (unknown adapter or profile, unsupported backend).
- `--limit N` stops after N records read from the adapter, including skipped ones.
- `--adapter-option KEY=VALUE` (repeatable) configures the adapter for this run.
  Each adapter documents its keys; an unknown key or a bad value is a usage error,
  as is passing options to an adapter that takes none.
- On macOS, `make_client()` first calls `runtime.stabilize_runtime()` to keep
  the embedding model off MPS. This is a temporary workaround.

## CUGA

The `cuga` adapter reads CUGA benchmark runs (AppWorld tasks). `--input` is one
run directory or a directory of them:

```text
<input>/
  <run>/                      # e.g. test_normal_dev; becomes metadata.run and metadata.partition
    metadata.json             # optional: {"task_ids": [...], "experiment_name": ...}
    results.json              # optional: {"<task_id>": {"eval": <JSON string or object>, ...}}
    <task_id>.json            # {"intent", "score", "dataset_name", "steps": [{"name", "data", "prompts"?}]}
    appworld_sdk_*.json       # ignored
```

When `metadata.json` lists `task_ids`, those are the tasks; otherwise every JSON
file other than `results`, `metadata` and `appworld_sdk_*` is one. Each task
becomes one record:

- **messages**: the intent as the user message, then each `Raw_Assistant_Response`
  as an `execute_ipython` function call and each `User_output` / `Observation` /
  `Tool_output` as an `OBSERVATION:` assistant message (the extractor ignores
  `role: "tool"`). Steps are cut to 2000 characters. CUGA's duplicate logs
  (`Assistant_code`, `Assistant_nl`), the final-answer step and the evaluation
  are left out.
- **outcome**: `success` from `results.json` (falling back to the task's
  `EvaluationResult` step, then to `score >= 1`), the failed ground-truth
  requirements as `failed_checks`, and the first 2000 characters of the
  evaluation report as `detail`. A task with no evaluation and no score has no outcome.
- **batch**: source `cuga`, conversation `<run>/<task_id>`, batch `<task_id>`, and
  a revision that digests the record's content. Correcting a task file or its
  `results.json` entry, or changing an option that changes the content, reprocesses
  that task; re-running unchanged input skips it. Renaming a run directory changes
  its identity.
- **Long runs**: the extractor keeps only the first 50 steps, so the adapter keeps
  the first 6 and the last 43 steps with one `[N steps elided ...]` marker between
  them. This is temporary, until the extractor's limits are configurable.

Options (`--adapter-option KEY=VALUE`):

| Key | Effect |
| --- | --- |
| `include_system_prompt=true` | Add the agent's system prompt (the first `system` message in any step's `prompts`) as a `system` context message. |
| `include_summaries=true` | Add each reflective `Summary` block from `User_return` steps as an assistant context message. |
| `task_ids=a_1,b_2` | Only these tasks. A listed ID that is not found ends the run with an error. |
| `task_manifest=<file>` | Only the tasks in a JSON file (`task_ids` or `tasks[].task_id`) or a text file with one ID per line (`#` comments). Combines with `task_ids`. |
| `model=<name>` | The agent's model, recorded on the trajectory and in metadata. |

Context messages are supporting history: the extractor sees them, but they are
not counted as steps and are rendered within a character budget that keeps the
most recent ones.

A sample profile, [profiles/cuga.json](profiles/cuga.json), runs the built-in
`evolve.guidelines` processor in `standard` mode with segmentation off. Its model
and provider are omitted, so they are captured from the `EVOLVE_*` / LLM settings
when the profile is published.

```bash
uv run evolve processing-profiles apply cuga-guidelines \
    --file experiments/guideline_pipeline/profiles/cuga.json --expected-revision 0

uv run python -m experiments.guideline_pipeline mine --adapter cuga --input path/to/cuga_runs \
    --namespace cuga --processing-profile cuga-guidelines \
    --adapter-option include_summaries=true --adapter-option model=gpt-4.1 --dry-run
```

Drop `--dry-run` to mine. The built-in processor sets each guideline's `evidence`
from the outcome, and its standard generator grounds extraction in the outcome's
`failed_checks` and `detail` (#351).

## VAKRA

The `vakra` adapter reads VAKRA benchmark output. `--input` is one run directory:

```text
<input>/
  <domain>.json               # a list of records: {"uuid", "domain", "status", "model_input": {"tools", ...},
                              #   "trajectory": [{"type": "HumanMessage" | "AIMessage" | "ToolMessage", ...}],
                              #   "output": [{"turn_id", "query", "answer", ...}]}
  <domain>_tools.json         # ignored
  results.json                # optional evaluator output: {"domains": {"<domain>": {"dialogues": [{"uuid", "score", "details"}]}}}
```

Domain files are read one at a time, in name order. Each record becomes one record:

- **messages**: the first `HumanMessage` (else the first turn's `query`) as the
  user message, then, as assistant steps: a `TOOL SPACE` summary (the tools the
  agent called, then the names of all tools it was offered), each `AIMessage`'s
  `reasoning`, each of its `tool_calls` as a function call with the real tool
  name and arguments, its `content` as the answer, each `ToolMessage` result as an
  `OBSERVATION:`, and any later `HumanMessage` as a `USER MESSAGE:`. Text longer
  than a step is split into `(part i/n)` steps rather than truncated. Runs longer
  than the extractor's 50 steps are elided as for CUGA (the helper is shared, in
  `adapters/elision.py`).
- **outcome**: from the record's `results.json` dialogue. `success` is
  `score >= 1`; the record's `status` is execution status only and is not used. A
  failed dialogue's `failed_checks` are its turns' sub-checks (policy, exact
  match, answer, groundedness) that scored 0, with the judge's explanation. The
  `detail` is the per-turn scores and explanations, cut to 2000 characters. A
  dialogue with only a score gets just `success`; a record with no results entry
  has no outcome.
- **trace_id** and **batch**: the record's `uuid` is the trace, conversation and
  batch ID (source `vakra`); the revision digests the record's content, as for CUGA.
- **metadata**: `domain`, `split` (`train` or `heldout`) and `score`.

### Train / held-out split

Each domain is split by position, in file order and without shuffling, and only
one side is yielded. By default that is the train side, so `mine` never sees a
held-out query.

- `train_n=N` (default 7): the first N records of each domain are train; the rest are held out.
- `dev_frac=F` (0 <= F < 1): the last `round(n * F)` records of each domain are held
  out. Rounding means a one-record domain stays in train and a two-record domain
  splits 1 / 1 at `dev_frac=0.3`. Use it when domain sizes vary too much for one `train_n`.
- Setting both is a usage error.

The held-out side is recorded in a manifest, `{domain: [{uuid, query, answer, score}, ...]}`,
for a later retrieval evaluation. Set `heldout_out=<file>` and the adapter writes it
when reading starts, from the same options as the split it yields, so the
manifest is exactly the complement of the mined records. The file is
deterministic: the same input and options always produce the same bytes. A dry
run writes it too, without an LLM call. From Python, `VakraAdapter(...).heldout_manifest(path)`
returns the same dict.

Options (`--adapter-option KEY=VALUE`):

| Key | Effect |
| --- | --- |
| `train_n=N` | The first N records of each domain are train (default 7). |
| `dev_frac=F` | Instead, hold out the last `round(n * F)` records of each domain. |
| `split=heldout` | Yield the held-out records instead of the train records (default `train`). |
| `domains=authors,hockey` | Only these domain files (by name without `.json`). A missing domain ends the run with an error. |
| `include_tools=false` | Leave out the `TOOL SPACE` step. |
| `results=<file>` | Read the evaluation from this file instead of `<input>/results.json`. |
| `heldout_out=<file>` | Write the held-out manifest to this file. |

```bash
# Check the split and write the held-out manifest, without touching storage.
uv run python -m experiments.guideline_pipeline mine --adapter vakra --input path/to/vakra_run \
    --namespace vakra --processing-profile vakra-guidelines \
    --adapter-option dev_frac=0.3 --adapter-option heldout_out=vakra_heldout.json --dry-run

# Mine the train split with the same options.
uv run python -m experiments.guideline_pipeline mine --adapter vakra --input path/to/vakra_run \
    --namespace vakra --processing-profile vakra-guidelines --adapter-option dev_frac=0.3
```

The CUGA sample profile works here too (publish it under another ID). Evaluating
retrieval on the held-out queries is not part of this stage; a later eval stage
will read the manifest.

## Writing an adapter

An adapter is any object with a `name` and a `records(path)` method that yields
`AdapterRecord`s lazily. Register an instance in `adapters/__init__.py`:

```python
from collections.abc import Iterator
from pathlib import Path

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord


class MyDatasetAdapter:
    name = "my-dataset"

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        for task in read_tasks(path):  # stream; don't load the whole dataset
            yield AdapterRecord(
                messages=task.messages,          # this attempt's conversation
                context_messages=[],             # supporting history, not counted as new material
                tools=task.tools,
                model=task.model,
                trace_id=task.id,                # the source task id
                metadata={"split": task.split},
                batch=TrajectoryBatch(
                    source="my-dataset",
                    conversation_id=task.id,
                    batch_id=f"attempt-{task.attempt}",
                    revision="1",                # bump when this record's content or outcome changes
                ),
                outcome=TrajectoryOutcome(success=task.passed, failed_checks=tuple(task.failed_checks)),
            )


ADAPTERS = {adapter.name: adapter for adapter in (MyDatasetAdapter(),)}
```

`AdapterRecord` is an `altk_evolve.processing.Trajectory` with `batch` and
`trace_id` required, so every field means what it means to Evolve:

- **Batch identity** must be stable across runs and unique per record; it is what
  makes re-runs skip completed work. Never derive it from a position in the file
  or from message count.
- **Outcome** is optional. When set, the built-in `evolve.guidelines` processor
  sets `evidence` to `success` or `failure` on every guideline it generates from
  the record. An outcome added after a record was
  mined needs a new `batch.revision`, or the record is skipped.
- Messages must be JSON-serializable; `--dry-run` checks this.

To take `--adapter-option` settings, also implement `configure(options)`, which
validates the `KEY=VALUE` strings and returns a configured copy (see
`ConfigurableAdapter` in `adapters/base.py` and `adapters/cuga.py`).

If long runs can exceed the extractor's 50-step cap, keep their ending with
`adapters/elision.py`'s `elide_middle`.

Each adapter needs unit tests under `tests/` that use a tiny synthetic fixture,
never a copy of the real dataset. See `tests/fakes.py` and `tests/fixtures/`.

## Tests

```bash
uv run pytest -v experiments/
```

They use a filesystem backend and an echo processor: no LLM calls, no network.
