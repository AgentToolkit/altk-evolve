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
- On macOS, `make_client()` first calls `runtime.stabilize_runtime()` to keep
  the embedding model off MPS. This is a temporary workaround.

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

Each adapter needs unit tests under `tests/` that use a tiny in-test fixture,
never a copy of the real dataset. See `tests/fakes.py` for the pattern.

## Tests

```bash
uv run pytest -v experiments/
```

They use a filesystem backend and an echo processor: no LLM calls, no network.
