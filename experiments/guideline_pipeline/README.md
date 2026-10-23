# Guideline pipeline

Turns benchmark trajectories into guidelines with this checkout of Evolve. A
dataset **adapter** reads one dataset format and yields records; a **stage**
consumes them. `mine` writes guidelines into a namespace; `consolidate`,
`export` and `lineage` read that namespace back and turn it into the static
files a benchmark harness loads.

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

## Consolidating, exporting and lineage

These stages read a namespace through `EvolveClient.get_all_entities`, the public
read path, so read hooks apply. Every output path comes from `--out`; nothing is
written into the repository by default. Files are written atomically (a temp
file in the same directory, then a rename), so a failed run leaves the previous
file intact. Each run prints one summary line; the exit code is 1 when reading
`--input` or writing `--out` fails, and 2 for a usage error (unknown namespace,
invalid thresholds, unrecognized evidence).

```bash
# Merge similar guidelines in place, with Evolve's consolidation (makes LLM calls).
uv run python -m experiments.guideline_pipeline consolidate --namespace <ns> [--mode lossless] [--threshold 0.8] \
    [--cluster-key task_description|guideline_text] [--max-cluster-size N] [--max-rounds N]

# Playbook: {"entries": [{"r": rule, "n": support, "e": evidence}]}
uv run python -m experiments.guideline_pipeline export playbook --namespace <ns> --out <dir>/playbook.json [--min-support N]

# Retrieval index: {"core": [rule], "singletons": [{"rule", "source_task", "source_instruction"}]}
uv run python -m experiments.guideline_pipeline export retrieval-index --namespace <ns> --out <dir>/index.json \
    [--core-support N] [--min-support N] [--adapter <name> --input <path> [--adapter-option KEY=VALUE ...]]

# Lineage: each guideline's metadata.sources and source task ids
uv run python -m experiments.guideline_pipeline lineage --namespace <ns> --out <dir>/lineage.json \
    [--adapter <name> --input <path> [--adapter-option KEY=VALUE ...]]
```

**Support and thresholds.** `n` is the guideline's support with Evolve's own
reading: the number of distinct source tasks among its supporting `sources`, so
rewordings merged from one task count once. A guideline whose provenance is
partial (sources that record no task, or `provenance_incomplete`) falls back to
its stored `support`, but never below its known task count; missing or unusable
values count as 1. `--min-support` and
`--core-support` mean what `EVOLVE_MIN_SUPPORT` and `EVOLVE_CORE_SUPPORT` mean
for retrieval (a guideline is kept at `support >= min`, and is core at
`support >= core`), and default to those settings (1 and 3). `min-support` must
not exceed `core-support`. How much to inject depends on the model, so there is
no recommended value beyond those defaults.

**Order.** Playbook entries, core rules and singletons are sorted by support
descending, then by rule text (case-insensitive), then evidence and id, so the
same namespace always exports the same file. The ordering lives in one function
in `guidelines.py`.

**Evidence.** `e` comes from `EVIDENCE_CODES` in `stages/export.py`: `success`
is `s`, `failure` is `f`, and `both` and unknown (no outcome) are both `b`. Any
other value is an error. Whether a harness reads `b` as "seen in both
successes and failures" rather than "unknown" still needs to be confirmed
before anything orders or filters on `e`.

**Source instructions.** A singleton needs the instruction of the task it came
from, and the store does not keep one per source: `metadata.sources` records
the source task id (the adapter record's `trace_id`), conversation, user,
agent and status, but no text. With `--adapter` and `--input` (the dataset the
namespace was mined from, plus any `--adapter-option` it was mined with), each
source task's instruction is the first user message of that record, and a
singleton backed by several tasks also lists them all under `source_tasks`. Without them, the guideline's own `task_description` is used,
but only for a guideline with exactly one source task. Singletons that still
can't be resolved are skipped, counted by reason in the summary, and listed on
stderr, never dropped silently. Superseded sources are ignored.

**Lineage** lists, per guideline, its stored `sources` verbatim (each gaining
an `instruction` when `--input` has it), the supporting task ids, and whether
the sources were `recorded`, `derived` from a legacy `source_task_id`, or
`missing`. A guideline written by `consolidate` carries the union of whatever
sources the guidelines merged into it had. It shows up as `recorded` with their
task ids only when at least one of them had source provenance; otherwise it
has no `sources` and shows up as `missing`.

## CUGA

The `cuga` adapter reads CUGA benchmark runs (AppWorld tasks). `--input` is one
run directory or a directory of them:

```text
<input>/
  <run>/                      # e.g. test_normal_dev; becomes metadata.run and metadata.partition
                              # (partition is dev for cuga_all_dev_<model>, else the run name)
    metadata.json             # optional: {"task_ids": [...], "experiment_name": ...}
    results.json              # optional: {"<task_id>": {"eval": <JSON string or object>, ...}}
    <task_id>.json            # {"intent", "score", "dataset_name", "steps": [{"name", "data", "prompts"?}]}
    appworld_sdk_*.json       # ignored
```

A directory is a run when it holds at least one task file (as defined below)
with an intent. `results.json`, `metadata.json`, `appworld_sdk_*.json` and
subdirectories (logs, traces) neither make a directory a run nor stop it being
one. Otherwise `--input` is read as the parent of the runs directly inside it.
Anything left unread gets a warning: child runs beneath a directory that is itself
a run, a parent's own JSON files (a sweep's metadata, results or summary, even a
malformed one), and a child directory with JSON but no readable task with an
intent. A path where neither it nor a child directory holds a task is an error,
and a selection that reads no task logs a warning. Reading a run whose
`metadata.json` is malformed, or whose `task_ids` is not a list, is an error.

When `metadata.json` lists `task_ids`, those are the tasks; otherwise every JSON
file other than `results`, `metadata` and `appworld_sdk_*` is one. A task whose
`intent` is missing, blank or not a string is skipped with a warning. Each other task becomes one record:

- **messages**: the intent as the user message, then each `Raw_Assistant_Response`
  as an `execute_ipython` function call and each `User_output` / `Observation` /
  `Tool_output` as an `OBSERVATION:` assistant message (the extractor ignores
  `role: "tool"`). Steps are cut to 2000 characters. CUGA's duplicate logs
  (`Assistant_code`, `Assistant_nl`), the final-answer step and the evaluation
  are left out.
- **outcome**: `success` from `results.json` (falling back to the task's
  `EvaluationResult` step, then to `score >= 1`; a verdict counts only as a bool,
  the integer `0`/`1` or the string `"true"`/`"false"`, and anything else falls through), the failed ground-truth
  requirements as `failed_checks`, and the first 2000 characters of the
  evaluation report as `detail`. A task with no evaluation and no score has no outcome.
- **batch**: source `cuga`, conversation `<run>/<task_id>`, batch `<task_id>`, and
  a revision that digests the record's content. Correcting a task file or its
  `results.json` entry, or changing an option that changes the content, reprocesses
  that task; re-running unchanged input skips it. Renaming a run directory changes
  its identity.
- **Long runs**: every step is passed on. The extractor's window decides what it
  renders: by default the first 50 steps, so a longer run loses its ending unless
  `trajectory_tail_steps` is set (the sample profile below keeps the last 43).

Options (`--adapter-option KEY=VALUE`):

| Key | Effect |
| --- | --- |
| `include_system_prompt=true` | Add the agent's system prompt (the first `system` message in any step's `prompts`) as a `system` context message. |
| `include_summaries=true` | Add each reflective `Summary` block from `User_return` steps as an assistant context message. |
| `task_ids=a_1,b_2` | Only these tasks. A listed ID that is not found ends the run with an error, and so does a value that lists no IDs. |
| `task_manifest=<file>` | Only the tasks in a JSON file (`task_ids` or `tasks[].task_id`) or a text file with one ID per line (`#` comments). Combines with `task_ids`. |
| `partitions=dev,test` | Only runs whose `metadata.partition` is listed. A listed partition that is not found ends the run with an error, and so does a value that lists none. |
| `model=<name>` | The agent's model, recorded on the trajectory and in metadata. |

A directory of runs is mined into one namespace whatever its partitions, so mining
train and dev runs together puts dev-derived guidelines into a namespace you may
later evaluate on dev. Pass `partitions=` to mine one split.

Context messages are supporting history: the extractor sees them, but they are
not counted as steps and are rendered within a character budget that keeps the
most recent ones.

A sample profile, [profiles/cuga.json](profiles/cuga.json), runs the built-in
`evolve.guidelines` processor in `standard` mode with segmentation off. Its
trajectory window is 50 steps with the last 43 kept for the ending
(`trajectory_max_steps`, `trajectory_tail_steps`; see
[the trajectory limits](../../docs/guides/configuration.md#trajectory-limits)). Its
model and provider are omitted, so they are captured from the `EVOLVE_*` / LLM
settings when the profile is published.

```bash
uv run evolve processing-profiles apply cuga-guidelines \
    --file experiments/guideline_pipeline/profiles/cuga.json --expected-revision 0

uv run python -m experiments.guideline_pipeline mine --adapter cuga --input path/to/cuga_runs \
    --namespace cuga --processing-profile cuga-guidelines \
    --adapter-option include_summaries=true --adapter-option model=gpt-4.1 --dry-run
```

Drop `--dry-run` to mine. The built-in processor sets each guideline's `evidence`
from the outcome, and its standard generator grounds extraction in the outcome's
`failed_checks` and `detail`.

## AppWorld

The `appworld` adapter reads the output tree of AppWorld's own ReAct runner as
it is. `--input` is one run directory or a directory of them:

```text
<input>/
  <run>/                          # becomes metadata.run; any directory with a tasks/ child is a run
    evaluations/<split>.json      # optional: {"individual": {"<task_id>": {"success", "difficulty", ...}}}
    tasks/<task_id>/
      tips_subtask.json           # optional: {"task_instruction", ...}
      logs/environment_io.md      # required: the agent's code and the environment's output
      logs/lm_calls.jsonl         # optional: one LM call per line, {"input": {"messages": [...]}}
      logs/logger.jsonl           # optional: {"role", "content", ...} per line
```

When `--input` is a directory of runs, any child directory without `tasks/` is
named in an `ignored ...` warning. AppWorld's legacy agents nest their runs as
`<agent>/<provider>/<model>/<split>/`, so pass the directory that directly holds
them. A selection that reads no task logs a warning.

Each task becomes one record:

- **Instruction** (the user message), from the first source that has it, recorded
  in `metadata.instruction_source`:
  1. `tips_subtask` — `task_instruction` in `tips_subtask.json`.
  2. `lm_calls` — the last user message of the first LM call. Its few-shot examples
     and any injected playbook come first, so the adapter takes the text from the
     last `Task:`, starting at the `My name is:` line before it when that is within
     600 characters (keeping the user's identity), else 200 characters before
     `Task:`. A prompt with no `Task:` has no instruction.
  3. `logger` — the first `role: "task"` entry in `logger.jsonl`.
  4. `specs` — `instruction` in `<appworld_root>/data/tasks/<task_id>/specs.json`,
     only when the `appworld_root` option is set.
- **messages**: after the instruction, each `### Environment Interaction N` block in
  `environment_io.md` gives its code as an `execute_ipython` function call and its
  output as an `OBSERVATION:` assistant message, rendered and cut to 2000 characters
  exactly as the CUGA adapter does. Long runs are passed on whole, as there.
- **outcome**: `success` from the task's entry in `evaluations/<split>.json`. As for
  CUGA, a verdict counts only as a bool, the integer `0`/`1` or the string
  `"true"`/`"false"` in any case. Any other value, or a task missing from the
  evaluations, gives no outcome. Failed requirements are not yet mapped to
  `failed_checks` or `detail`.
- **metadata**: `run`, `split` (the evaluations file name, when the task is
  evaluated), `difficulty`, `model` and `instruction_source`.
- **batch**: source `appworld`, conversation `<run>/<task_id>`, batch `<task_id>`,
  and a revision that digests the record's content, as for CUGA. A task listed in
  two evaluations files of one run is an error.

A task with no readable `environment_io.md`, no interactions in it, or no
recoverable instruction is skipped with a `skipped <run>/<task_id>: ...` warning on
stderr, so the rest of the run is still mined; it is never mined with an empty
instruction. An instruction source that cannot be read (malformed JSON, such as the
truncated last line a killed run leaves, or a non-object) gets a
`<run>/<task_id>: cannot read <file>` warning and the next source is tried. An
evaluations file that is malformed or has no `individual` mapping ends the run with
an error that names it, since every outcome in it would otherwise be lost.

Options (`--adapter-option KEY=VALUE`):

| Key | Effect |
| --- | --- |
| `task_ids=a_1,b_2` | Only these tasks. A listed ID with no task directory ends the run with an error, and so does a value that lists no IDs. |
| `task_manifest=<file>` | Only the tasks in a JSON file (`task_ids` or `tasks[].task_id`) or a text file with one ID per line (`#` comments). Combines with `task_ids`. |
| `splits=train,dev` | Only tasks evaluated in a listed split (an `evaluations/<split>.json` name, such as `test_normal`). A task with no evaluation has no known split, so it is left out. A listed split that is not found ends the run with an error, and so does a value that lists none. |
| `model=<name>` | The agent's model, recorded on the trajectory and in metadata. |
| `appworld_root=<dir>` | An AppWorld root whose `data/tasks/<task_id>/specs.json` is the last instruction source. The run tree does not contain it. It must be an existing directory; an empty value is an error. Pass it to `export retrieval-index` and `lineage` as well, or tasks whose instruction came from it are not found in `--input`. |

Without `splits=`, every task under `tasks/` is mined except those evaluated in a
`test*` split, because guidelines mined from a test split leak into any later
evaluation on it. A run with skipped test-split tasks gets a `skipped test-split
tasks ...` warning, and a `task_ids=` entry that is a test-split task is an error.
To mine a test split anyway, name it: `splits=test_normal`. A task with no
evaluation has no known split, so it is still read.

[profiles/appworld.json](profiles/appworld.json) is the same sample profile as
CUGA's: `evolve.guidelines` in `standard` mode, segmentation off, the same
50-step window with a 43-step tail, model and provider captured when published.

```bash
uv run evolve processing-profiles apply appworld-guidelines \
    --file experiments/guideline_pipeline/profiles/appworld.json --expected-revision 0

uv run python -m experiments.guideline_pipeline mine --adapter appworld --input path/to/appworld_runs \
    --namespace appworld --processing-profile appworld-guidelines \
    --adapter-option model=gpt-4.1 --dry-run
```

## VAKRA

The `vakra` adapter reads VAKRA benchmark output. `--input` is one run directory:

```text
<input>/
  <domain>.json               # a list of records: {"uuid", "domain", "status", "model_input": {"tools", ...},
                              #   "trajectory": [{"type": "HumanMessage" | "AIMessage" | "ToolMessage", ...}],
                              #   "output": [{"turn_id", "query", "answer", "sequence": {"tool_call": [{"name", "arguments"}]}}]}
  <domain>_tools.json         # ignored
  results.json                # optional evaluator output: {"domains": {"<domain>": {"dialogues": [{"uuid", "score", "details"}]}}}
```

VAKRA's current runner (`benchmark/runner_helpers.py::save_results_ground_truth`
at IBM/vakra `00dc68f`) writes neither `model_input` nor `trajectory`: the
trajectory goes only to `run.log`. A record whose trajectory holds no
`AIMessage` or `ToolMessage` is therefore rebuilt from its `output` turns: each
turn's `sequence.tool_call` calls, then its answer. Those records carry no
reasoning or observations, and each domain file logs how many of them it has,
plus the uuids of any record left with no agent step at all (it would be mined
as its task and score alone).

A parent of run directories is an error that names the runs beneath it, and a
run nested inside the input run is not read (with a warning). Domain files are
read one at a time, in name order. A file that is not JSON, is not a list of
objects, or has a record without a string `uuid` ends the run with an error
naming it, and so does a `results.json` without that `domains` / `dialogues`
shape (read as empty, every record would silently lose its outcome). Each record
becomes one record:

- **messages**: the first `HumanMessage` with text (else the first turn's
  `query`) as the user message; a record with neither is skipped with a warning.
  Then, as assistant steps: a `TOOL SPACE` summary (the tools the agent called,
  then the names of all tools it was offered), each `AIMessage`'s `reasoning`,
  each of its `tool_calls` as a function call with the real tool name and
  arguments, its `content` as the answer, each `ToolMessage` result as an
  `OBSERVATION:` (a structured result as JSON), and any later `HumanMessage` as a
  `USER MESSAGE:` (without a trajectory, the output turns' calls and answers, and
  each later turn's query as a `USER MESSAGE:`). Message content is a string or a list of content blocks, whose
  text parts are joined; any other type is left out rather than rendered as a
  Python repr. Text longer than a step is split into `(part i/n)` steps rather
  than truncated, and tool-call arguments are kept whole (still valid JSON). Long runs are passed on whole, as for
  CUGA; publish the CUGA sample profile to keep their ending.
- **outcome**: from the record's `results.json` dialogue, matched by the domain
  file's name and the uuid (the evaluator names each results domain after its
  file and pairs uuids only within a domain, so a uuid repeated in another domain
  keeps its own evaluation). `success` is
  `score >= 1` for a finite numeric score. Any other score (NaN, a string, a
  bool) is ignored with a warning, so it gives no outcome rather than a failure.
  The record's `status` is execution status only and is not used. A failed
  dialogue's `failed_checks` are the sub-checks (policy, exact match, answer,
  groundedness) that failed one of its turns, with the judge's explanation. As in
  VAKRA's scorer, a turn that scored 1 contributes none, and an exact match of 0
  counts only when the answer judge failed the turn too (otherwise it merely sent
  the turn to that judge). The `detail`
  is the per-turn scores and explanations, cut to 2000 characters. A dialogue
  with only a score gets just `success`; a record with no results entry has no
  outcome.
- **trace_id** and **batch**: the record's `uuid` is the trace, conversation and
  batch ID (source `vakra`); the revision digests the record's content, as for CUGA.
- **metadata**: `domain` (the record's `domain`, else its file's name), `split`
  (`train` or `heldout`) and `score`.

### Train / held-out split

A record is held out when the first 8 bytes of `sha256(uuid)`, read as a fraction
of 2^64, fall below `dev_frac` (default 0.3, so about 30% of records). Only one side
is yielded. By default that is the train side, so `mine` never sees a held-out query.

The hash looks only at the record's own uuid, so its side never changes when a
dataset revision inserts, removes or reorders records, or when another run of the
same dialogues is mined into the namespace. A record mined once can therefore
never later turn up held out, as long as `dev_frac` stays the same: keep one
`dev_frac` per namespace, because raising it moves train records (perhaps already
mined) into held-out. The cost is that each domain's split is only
approximately `dev_frac`, and a small domain can end up with nothing held out or
nothing to mine. Reading logs each file's counts to stderr
(`authors.json: 5 train, 4 held out (dev_frac=0.3)`), so check them in a dry run.

The held-out side is recorded in a manifest, `{domain: [{uuid, query, answer, score}, ...]}`,
for a later retrieval evaluation. Set `heldout_out=<file>` and the adapter writes it
when reading starts, from the same options as the split it yields, so the
manifest is exactly the complement of the mined records. Domain files whose
records share a `domain` share one list. A held-out record whose query (ignoring
case and whitespace) is also a train record's query is not really unseen, so
building the manifest logs those uuids. The file is deterministic: the same input
and options always produce the same bytes. A dry run writes it too, without an
LLM call. From Python, `VakraAdapter(...).heldout_manifest(path)` returns the same dict.

Options (`--adapter-option KEY=VALUE`):

| Key | Effect |
| --- | --- |
| `dev_frac=F` | Hold out records whose uuid hash is below F, `0 <= F < 1` (default 0.3). |
| `split=heldout` | Yield the held-out records instead of the train records (default `train`). |
| `domains=authors,hockey` | Only these domain files (by name without `.json`). A missing domain ends the run with an error, and so does a value that names none. |
| `include_tools=false` | Leave out the `TOOL SPACE` step. |
| `results=<file>` | Read the evaluation from this file instead of `<input>/results.json`. Inside the run directory, it is not read as a domain file. |
| `heldout_out=<file>` | Write the held-out manifest to this file. Inside the run directory, it is not read as a domain file. |

```bash
# Publish the CUGA sample profile under VAKRA's own ID, once.
uv run evolve processing-profiles apply vakra-guidelines \
    --file experiments/guideline_pipeline/profiles/cuga.json --expected-revision 0

# Check the split and write the held-out manifest, without touching storage.
uv run python -m experiments.guideline_pipeline mine --adapter vakra --input path/to/vakra_run \
    --namespace vakra --processing-profile vakra-guidelines \
    --adapter-option heldout_out=vakra_heldout.json --dry-run

# Mine the train split with the same options.
uv run python -m experiments.guideline_pipeline mine --adapter vakra --input path/to/vakra_run \
    --namespace vakra --processing-profile vakra-guidelines
```

The CUGA sample profile is used here as is. Evaluating
retrieval on the held-out queries is not part of this stage; a later eval stage
will read the manifest.

## tau-retail

Reads τ-bench retail runs exported as one text file per task. `--input` is a
directory of `task_*.txt` files (read in task-number order, not recursively) or
a single `task_*.txt` file. Each file has `=====`-ruled sections: a
`TASK <n>  reward=<r>  breakdown={...}` header, `USER SCENARIO`,
`GROUND-TRUTH EVALUATION CRITERIA`, `AGENT DOMAIN POLICY`, `CONVERSATION`
(`[ASSISTANT]` / `[USER]` turns, `-> CALL name(...)` with `args: {...}`, and
`<- RESULT ...`), `FINAL ANSWER`, and `JUDGE / REWARD DETAILS`. See
`tests/fixtures/tau/` for the exact layout. A rule starts a section only when a
section title follows it, so a `=====` line inside a turn stays in that turn.
A title is recognised by how its line starts, so when a `=====` line inside a
turn is followed by a line like `FINAL ANSWER: ...`, that section appears twice
and the task is skipped with a warning rather than read with turns missing.

Each task becomes one record:

- **messages:** the conversation's first `[USER]` turn as the task (what the
  agent was actually asked, so guidelines are keyed like the queries that will
  retrieve them), then the other turns in order. Each call is an assistant
  function call with its tool name and arguments; results and both speakers'
  other turns are labelled assistant messages. The `USER SCENARIO` is the user
  simulator's hidden brief (`reason_for_call`, `known_info`,
  `task_instructions`), which the agent never saw, so it is left out unless
  `include_scenario=true`.
- **Long runs:** long turns are split into parts under the extractor's
  2000-character step cap, and every step is passed on. The extractor's default
  window is the first 50 steps, so set `trajectory_tail_steps` in the profile to
  keep a longer conversation's ending. `EVOLVE_TRAJECTORY_TAIL_STEPS` only counts
  when the profile is published, where it is captured as that profile's default;
  setting it when mining against an already-published profile changes nothing.
- **outcome:** `success` when the reward is 1.0; judge lines with
  `action_match=False` or `met=False` (or the JSON / dict forms
  `"action_match": false`, `'met': False`) become `failed_checks`, and the start
  of the judge section becomes `detail`. When pretty-printed JSON puts the flag
  on its own line, the check is named from its object: the action's `name` and
  `arguments`, or the `nl_assertion` text.
- **trace_id / batch:** the file name (`task_12`), prefixed with the split when
  the header records one (`train/task_12`), because splits can reuse task
  numbers. The conversation is the same; the batch is the file name. The revision digests
  the record's content, so editing a task, or changing an option that changes
  what is processed, reprocesses that task; re-running unchanged input skips it.
- **metadata:** the header's reward, breakdown, termination reason, `split` and
  the other fields it carries; `model=` in the header sets the record's model.

A file without the header (and its reward), a conversation or a user turn in it
is skipped with a warning on stderr, and the run continues. A run that reads no
task logs a warning too.

Options (`--adapter-option KEY=VALUE`):

| Key | Effect |
| --- | --- |
| `include_policy=true` | Add the agent domain policy as `system` context messages. It is the same in every task, so it is off by default. |
| `include_scenario=true` | Add the user simulator's scenario as a `system` context message. It holds facts the agent never had, so guidelines mined with it can encode them. |
| `splits=train` | Only tasks in these splits. Without `split_file`, a task's split is the `split=` its header records: a task without one is skipped with a warning, and a listed split that no task records ends the run with an error. A value that lists no split is an error. |
| `split_file=<split_tasks.json>` | Select task IDs using tau2's `split_tasks.json` (a JSON object mapping each split to its task IDs) for explicit `splits=` selection. A task is matched on the number in its file name (`task_12.txt` is task `12`). Without `splits=`, tasks marked `test*` by either the file or header are skipped (case-insensitive). A listed split that the file lacks is an error. |

Context is supporting history: the extractor sees it, but it is not counted as
new material.

**Splits and leakage.** Mining test tasks and then evaluating on them leaks the
test set into the namespace, and known `test*` tasks are skipped by default. Tasks with unknown splits are retained; one summary warning per completed run reports skipped test tasks and successfully read unknown tasks. Only `train` and `test*` labels establish train/test membership; labels such as `base` remain unknown. Use `splits=test` to opt in explicitly. tau2's retail
`train` and `test` splits are lists of task IDs over the `base` set, so a normal
run over `base` holds both in one directory and its headers can record at most
`split=base`. These headers alone cannot protect test tasks: the default retains them with an unknown-split warning. Pass tau2's split file to mine only the training tasks:

```bash
uv run python -m experiments.guideline_pipeline mine --adapter tau-retail \
    --input runs/tau-retail-base/trajectories --namespace tau_retail \
    --processing-profile guidelines-consistency \
    --adapter-option include_policy=true \
    --adapter-option split_file=tau2-bench/data/tau2/domains/retail/split_tasks.json \
    --adapter-option splits=train --dry-run
```

When the runs record their split in each header (`split=train`), `splits=train`
alone selects on it.

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
`option_boolean` and `option_names` in `adapters/base.py` parse boolean and
comma-separated options strictly: a typo or an empty filter is an error.

Pass every step on. Bounding long runs is the extractor's job: its window
(`trajectory_max_steps`, `trajectory_tail_steps`) is set in the processing
profile, and a profile for a dataset with long runs should set a tail so their
ending is kept. One path does not apply the window yet: the accurate consistency
method (`guidelines_mode` `consistency` or `all` with `consistency_method:
accurate`) renders only the first 50 steps, whatever the profile sets, so it
still loses a long run's ending. With `EVOLVE_DEBUG_DIR` set, its debug dump also
grows with the square of the run's length.

Each adapter needs unit tests under `tests/` that use a tiny synthetic fixture,
never a copy of the real dataset. See `tests/fakes.py` and `tests/fixtures/`.

## Tests

```bash
uv run pytest -v experiments/
```

They use a filesystem backend, an echo processor and a scripted guideline
processor, with consolidation's clustering and merge mocked: no LLM calls, no
network.
