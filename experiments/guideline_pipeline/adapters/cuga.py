"""CUGA benchmark runs: one record per task.

Two run layouts are supported:

- **Legacy run directory**: ``<run>/{<task_id>.json, results.json, metadata.json}``.
  ``metadata.json``'s ``task_ids`` is the authoritative task manifest. The partition is
  parsed from the directory name (``cuga_all_<split>_<model>``).
- **Evaluation bundle**: ``<bundle>/{metadata.json, trajectories/<task_id>.json, results/
  appworld_sdk_*_final_report.json}``. ``metadata.json``'s ``bundle_version`` marks it as a
  bundle; ``run.eval_key`` becomes the partition, and ``runtime_config.env_vars.MODEL_NAME``
  the default model when the adapter's ``model`` option is unset.

``--input`` is either a run (bundle or legacy) or a parent of them.

The adapter emits one ``AdapterRecord`` per task. ``conversion=steps`` (default) is the
original flattened shape — every action is an ``execute_ipython`` function call and every
observation is an assistant ``OBSERVATION:`` message. ``conversion=agent_view`` is a
single-agent reconstruction built with :mod:`.cuga_agent_view`: only the main agent's
calls are assistant turns and everything the agent received stays in user turns.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord, option_boolean, option_names
from experiments.guideline_pipeline.adapters.cuga_agent_view import agent_messages

logger = logging.getLogger(__name__)

SOURCE = "cuga"
STEP_CHARS = 2000  # the extractor truncates each step to 2000 characters; slice early to keep payloads small
DETAIL_CHARS = 2000
FAIL_TRACE_CHARS = 600
_ACTION_STEPS = ("Raw_Assistant_Response", "Assistant_response")
_OBSERVATION_STEPS = ("User_output", "Observation", "Tool_output")
# agent_view produces strict ``system, user, (assistant, user)*, assistant`` alternation.
# Over this many agent calls the library's extractor would elide the middle (#437), and
# its marker would break the alternation agent_view relies on, so the task is skipped.
# Matches the library default `trajectory_max_steps`.
_AGENT_VIEW_STEP_LIMIT = 50
_NOT_TASKS = ("results", "metadata")
# Bundle layout: these subdirectories live beside the task files, never contain them.
_BUNDLE_TRAJECTORIES = "trajectories"
_BUNDLE_RESULTS = "results"
_CONVERSIONS = ("steps", "agent_view")
# Companion files written next to a bundle task file. Matched by exact suffix so a legacy
# task id that itself contains a dot ("appworld.v2_1.json") stays readable as a task.
_COMPANION_SUFFIXES = (".calls.json", ".agent_messages.json")


@dataclass
class CugaAdapter:
    """Yields one AdapterRecord per CUGA task. Configure with ``--adapter-option KEY=VALUE``.

    Options:

    - ``conversion``: ``steps`` (default, the flattened shape every action/observation
      becomes an assistant turn) or ``agent_view`` (a single-agent reconstruction where
      only the main agent's calls are assistant turns and everything it received stays
      in user turns, built with :mod:`.cuga_agent_view`).
    - ``include_system_prompt``, ``include_summaries``: in ``steps`` only, add the
      agent's system prompt and reflection summaries to ``context_messages``. Rejected
      in ``agent_view`` where that content already lives in ``messages``.
    - ``task_ids``, ``task_manifest``, ``partitions``, ``model``: filter tasks or
      record the agent's model on each trajectory.
    """

    name: str = "cuga"
    conversion: Literal["steps", "agent_view"] = "steps"
    include_system_prompt: bool = False
    include_summaries: bool = False
    task_ids: frozenset[str] = field(default_factory=frozenset)
    partitions: frozenset[str] = field(default_factory=frozenset)
    model: str | None = None

    def configure(self, options: Mapping[str, str]) -> CugaAdapter:
        changes: dict[str, Any] = {}
        task_ids = set(self.task_ids)
        for key, value in options.items():
            if key in ("include_system_prompt", "include_summaries"):
                changes[key] = option_boolean(SOURCE, key, value)
            elif key == "task_ids":
                task_ids.update(option_names(SOURCE, key, value))
            elif key == "task_manifest":
                task_ids.update(load_task_manifest(Path(value)))
            elif key == "partitions":
                changes[key] = frozenset(option_names(SOURCE, key, value))
            elif key == "model":
                changes[key] = value or None
            elif key == "conversion":
                if value not in _CONVERSIONS:
                    raise ValueError(f"cuga option conversion must be one of {_CONVERSIONS}, not {value!r}")
                changes[key] = value
            else:
                raise ValueError(
                    f"Unknown cuga option {key!r} (expected conversion, include_system_prompt, include_summaries, task_ids, task_manifest, partitions, model)"
                )
        merged = replace(self, **changes, task_ids=frozenset(task_ids))
        # agent_view's messages already carry the system prompt (verbatim) and the reflection
        # summaries (inside user turns), so adding them to context_messages would duplicate
        # them in every generation prompt, and the summary case would re-role reflection
        # output as the main agent's own reasoning — exactly what agent_view exists to avoid.
        if merged.conversion == "agent_view" and (merged.include_system_prompt or merged.include_summaries):
            raise ValueError(
                "cuga option conversion=agent_view cannot be combined with include_system_prompt or include_summaries"
                " (the system prompt and reflection summaries are already in agent_view messages)"
            )
        return merged

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        run_dirs = _run_dirs(path)
        if not run_dirs:
            raise ValueError(f"No CUGA run directory at {path}: neither it nor a child holds a task file with an intent")
        if self.partitions:
            found_partitions = {_eval_key(run_dir) for run_dir in run_dirs}
            if missing := sorted(self.partitions - found_partitions):
                raise ValueError(f"Partitions not found beneath {path}: {missing} (found {sorted(found_partitions)})")
            run_dirs = [run_dir for run_dir in run_dirs if _eval_key(run_dir) in self.partitions]
        if self.task_ids:
            found = {task_file.stem for run_dir in run_dirs for task_file in _task_files(run_dir)}
            if missing := sorted(self.task_ids - found):
                raise ValueError(f"Task IDs not found beneath {path}: {missing}")
        read = 0
        for run_dir in run_dirs:
            if self.conversion == "agent_view" and _context_summarization_enabled(run_dir):
                logger.warning(
                    "%s has context summarization enabled; agent_view messages may not reflect the full conversation",
                    run_dir.name,
                )
            results = _load_results(run_dir)
            metadata = _metadata(run_dir)
            experiment = metadata.get("experiment_name")
            partition = _eval_key(run_dir)
            default_model = _default_model(metadata)
            for task_file in _task_files(run_dir):
                if self.task_ids and task_file.stem not in self.task_ids:
                    continue
                task = _read_json(task_file)
                if not (isinstance(intent := task.get("intent"), str) and intent.strip()):
                    logger.warning("skipped %s/%s: no intent", run_dir.name, task_file.stem)
                    continue
                record = self.record(
                    run_dir.name,
                    task_file.stem,
                    task,
                    results.get(task_file.stem),
                    experiment=experiment,
                    partition=partition,
                    default_model=default_model,
                )
                if record is None:
                    continue
                read += 1
                yield record
        if not read:
            logger.warning("read no CUGA task beneath %s", path)

    def record(
        self,
        run: str,
        task_id: str,
        task: dict,
        result: dict | None,
        *,
        experiment: str | None = None,
        partition: str | None = None,
        default_model: str | None = None,
    ) -> AdapterRecord | None:
        """Build one record from a parsed task file and its evaluation entry (if any).

        Returns ``None`` when ``conversion=agent_view`` cannot build messages for this
        task (the trajectory has no main-agent call with prompts, the intent is missing,
        or the recovered prompt is unsplittable); a warning naming the task is logged.
        A long run (>50 agent calls) is skipped the same way.
        """
        steps = [step for step in task.get("steps") or [] if isinstance(step, dict)]
        evaluation = _evaluation(steps, result)
        resolved_model = self.model if self.model is not None else default_model
        metadata = {
            "run": run,
            "partition": partition if partition is not None else _partition(run),
            "dataset": task.get("dataset_name") or None,
            "experiment": experiment,
            "model": resolved_model,  # also in metadata: the guideline processor stores metadata, not Trajectory.model
            "score": task.get("score"),
            "pass_percentage": evaluation.get("pass_percentage"),
        }
        if self.conversion == "agent_view":
            built = self._agent_view_messages(run, task_id, task, steps)
            if built is None:
                return None
            messages, context_messages = built
        else:
            messages = [{"role": "user", "content": task.get("intent") or ""}, *agent_steps(steps)]
            context_messages = self._context(steps)
        content: dict[str, Any] = {
            "messages": messages,
            "context_messages": context_messages,
            "trace_id": task_id,
            "model": resolved_model,
            "metadata": {key: value for key, value in metadata.items() if value is not None},
            "outcome": _outcome(_success(task, steps, result), evaluation),
        }
        # The revision is a digest of exactly what processors see, so a corrected
        # task file, results entry or option change is reprocessed; nothing else is.
        digest = hashlib.sha256(json.dumps(_plain(content), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        batch = TrajectoryBatch(source=SOURCE, conversation_id=f"{run}/{task_id}", batch_id=task_id, revision=digest)
        return AdapterRecord(**content, batch=batch)

    def _agent_view_messages(
        self, run: str, task_id: str, task: dict, steps: list[dict]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """The agent-view messages and (empty) context_messages for one task, or None.

        Passes the filtered ``steps`` into ``agent_messages`` rather than the raw task,
        so a malformed ``steps`` field (``null``, non-dict entries, non-list) is caught
        in ``record()`` and does not reach the helpers. Shape errors from the heuristic
        splitters (``agent_turns`` iterating prompts, ``call_input_output`` indexing
        role/value) are caught alongside the usual ``ValueError`` and reported as a
        per-task skip, so one bad task does not stop the generator.
        """
        try:
            messages = agent_messages({**task, "steps": steps}, drop_examples=True, strip_last_available_variables=True)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            logger.warning("skipped %s/%s: %s", run, task_id, exc)
            return None
        assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
        if assistant_count > _AGENT_VIEW_STEP_LIMIT:
            logger.warning(
                "skipped %s/%s: %d agent calls exceed the parser cap of %d",
                run,
                task_id,
                assistant_count,
                _AGENT_VIEW_STEP_LIMIT,
            )
            return None
        return messages, []

    def _context(self, steps: list[dict]) -> list[dict[str, Any]]:
        context: list[dict[str, Any]] = []
        if self.include_system_prompt and (prompt := _system_prompt(steps)):
            context.append({"role": "system", "content": prompt[:STEP_CHARS]})
        if self.include_summaries:
            actions = 0
            for step in steps:
                if step.get("name") in _ACTION_STEPS and _data(step):
                    actions += 1
                elif step.get("name") == "User_return" and (summary := _summary(_data(step))):
                    context.append({"role": "assistant", "content": f"SUMMARY after action {actions}:\n{summary[:STEP_CHARS]}"})
        return context


def agent_steps(steps: list[dict]) -> list[dict[str, Any]]:
    """Actions become function calls and observations become reasoning steps, one extractor step each.

    role "tool" would be dropped by the extractor, so observations are assistant messages.
    Each step is cut to STEP_CHARS; steps with no data are left out.
    """
    messages: list[dict[str, Any]] = []
    calls = 0
    for step in steps:
        data = _data(step)[:STEP_CHARS]
        if not data:
            continue
        if step.get("name") in _ACTION_STEPS:
            call = {
                "type": "function_call",
                "id": f"call_{calls}",
                "function": {"name": "execute_ipython", "arguments": json.dumps({"code": data})},
            }
            messages.append({"role": "assistant", "content": [call]})
            calls += 1
        elif step.get("name") in _OBSERVATION_STEPS:
            messages.append({"role": "assistant", "content": "OBSERVATION:\n" + data})
    return messages


def _evaluation(steps: list[dict], result: dict | None) -> dict:
    """The task's evaluation report, with the richest fields winning.

    The AppWorld-SDK ``results`` entry is the base: ``success`` and ``match_rate`` but no
    ``evaluation`` sub-report. A legacy ``results.json`` entry (and the ``EvaluationResult``
    step) carries the full report, so its fields overwrite the SDK's. ``_success`` reads
    the two in a different, documented order, so verdict precedence is not decided here.
    """
    return {
        **_loads((result or {}).get("sdk_eval")),
        **_step_evaluation(steps),
        **_loads((result or {}).get("eval")),
    }


def _step_evaluation(steps: list[dict]) -> dict:
    return next((_loads(step.get("data")) for step in steps if step.get("name") == "EvaluationResult"), {})


def _success(task: dict, steps: list[dict], result: dict | None) -> bool | None:
    """The verdict from the first readable source: an SDK-bundle entry, a legacy
    ``results.json`` entry, the ``EvaluationResult`` step, then ``score >= 1``.

    ``result`` carries both an ``eval`` payload (the legacy entry) and an
    ``sdk_eval`` payload (the AppWorld-SDK ``results[*]`` entry) when both are
    present. Each is consulted in the order the README documents, so a legacy
    entry with no readable ``success`` field falls through to the SDK verdict
    rather than hiding it.
    """
    sources = (
        _loads((result or {}).get("sdk_eval")),
        _loads((result or {}).get("eval")),
        _step_evaluation(steps),
    )
    for evaluation in sources:
        if (found := verdict(evaluation.get("success"))) is not None:
            return found
    return task["score"] >= 1.0 if isinstance(task.get("score"), (int, float)) else None


def verdict(value: Any) -> bool | None:
    """A verdict stored as a bool, the integer 0 or 1, or the string "true"/"false" (any case); None for anything else.

    Runs are stringly typed, and bool("False") is True, so nothing else is coerced.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _outcome(success: bool | None, evaluation: dict) -> TrajectoryOutcome | None:
    if success is None:
        return None
    failed = (str(check.get("requirement") or "").strip().replace("\n", " ") for check in _checks(evaluation, "failures"))
    detail = _evaluation_report(evaluation)
    return TrajectoryOutcome(
        success=success,
        failed_checks=tuple(dict.fromkeys(check for check in failed if check)),
        detail=detail[:DETAIL_CHARS] if detail else None,
    )


def _evaluation_report(evaluation: dict) -> str | None:
    """Every ground-truth check that ran, with the start of each failure's trace."""
    report = evaluation.get("evaluation")
    if not isinstance(report, dict):
        return None
    lines = [f"num_tests={report.get('num_tests')} pass_count={report.get('pass_count')} pass_percentage={report.get('pass_percentage')}"]
    for heading, checks in (("PASSED checks:", _checks(evaluation, "passes")), ("FAILED checks:", _checks(evaluation, "failures"))):
        if checks:
            lines.append(heading)
        for check in checks:
            requirement = str(check.get("requirement") or "").strip().replace("\n", " ")
            trace = str(check.get("trace") or "").strip()
            if requirement or trace:
                lines.append(f"  - {requirement}")
            if trace and heading.startswith("FAILED"):
                lines.append(f"    trace: {trace[:FAIL_TRACE_CHARS]}")
    return "\n".join(lines)


def _checks(evaluation: dict, key: str) -> list[dict]:
    """The report's checks under key; results.json is open input, so anything but a dict entry is skipped."""
    report = evaluation.get("evaluation")
    checks = report.get(key) if isinstance(report, dict) else None
    return [check for check in checks if isinstance(check, dict)] if isinstance(checks, list) else []


def _system_prompt(steps: list[dict]) -> str | None:
    """The agent's instructions, from the first system message in any step's prompts."""
    for step in steps:
        for message in step.get("prompts") or []:
            if isinstance(message, dict) and message.get("role") == "system":
                return str(message.get("value") or message.get("content") or "").strip() or None
    return None


def _summary(data: str) -> str | None:
    """CUGA appends a reflective 'Summary' block to each User_return step; return it from its first line."""
    match = re.search(r"(?im)^[ \t]*Summary\b", data)
    return data[match.start() :].strip() if match else None


def _load_results(run_dir: Path) -> dict[str, dict]:
    """Per-task evaluation entries from ``results.json`` and ``results/appworld_sdk_*_final_report.json``.

    Entries carry up to two keys, each a dict:
    - ``sdk_eval``: the AppWorld-SDK ``results[*]`` payload (bundle layout).
    - ``eval``: the legacy ``results.json`` entry's ``eval`` payload.

    They are kept separate so :func:`_success` can consult them in the order the README
    documents (SDK → legacy → step → score). A legacy entry with no readable ``success``
    then falls through to the SDK verdict rather than hiding it.
    """
    results: dict[str, dict] = {}
    sdk_dir = run_dir / _BUNDLE_RESULTS
    if sdk_dir.is_dir():
        for report in sorted(sdk_dir.glob("appworld_sdk_*_final_report.json")):
            try:
                payload = _read_json(report)
            except (OSError, ValueError) as exc:
                logger.warning("skipped %s: %s", report, exc)
                continue
            entries = payload.get("results")
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("task_name")
                if isinstance(name, str) and name:
                    results.setdefault(name, {})["sdk_eval"] = {k: v for k, v in entry.items() if k != "task_name"}
    path = run_dir / "results.json"
    if path.exists():
        legacy = _read_json(path)
        for task_id, entry in legacy.items():
            if isinstance(entry, dict):
                merged = results.setdefault(task_id, {})
                merged.update(entry)
    return results


def _metadata(run_dir: Path) -> dict:
    """The run's metadata.json, {} if it has none; a ValueError naming the file if it is unreadable or its task_ids is not a list."""
    path = run_dir / "metadata.json"
    if not path.exists():
        return {}
    try:
        metadata = _read_json(path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(metadata.get("task_ids") or [], list):
        raise ValueError(f"Cannot read {path}: task_ids is not a list")
    return metadata


def _is_bundle(run_dir: Path, *, detecting: bool = False) -> bool:
    """A directory with ``metadata.json`` carrying ``bundle_version`` and a ``trajectories/`` child.

    An unreadable ``metadata.json`` is an error when reading a run (matching :func:`_metadata`
    and :func:`_task_files`), so a corrupted bundle is reported rather than silently read as
    a legacy run with the ``trajectories/`` subdirectory. The ``trajectories/`` child alone
    is enough to classify the directory as a bundle for this purpose: without the readable
    metadata we cannot tell its ``run.eval_key`` or whether this is an unrelated layout, and
    the right behaviour is a clear error.
    """
    try:
        metadata = _metadata(run_dir)
    except ValueError:
        if not detecting:
            raise
        # On detection, still flag the directory as a bundle so a parent sweep notices
        # the corrupted bundle via _is_run_dir rather than silently reading its
        # ``trajectories/`` subdirectory as a legacy run.
        return (run_dir / "metadata.json").exists() and (run_dir / _BUNDLE_TRAJECTORIES).is_dir()
    return bool(metadata.get("bundle_version")) and (run_dir / _BUNDLE_TRAJECTORIES).is_dir()


def _task_dir(run_dir: Path, *, detecting: bool = False) -> Path:
    """The directory holding task JSON files for ``run_dir``: ``<bundle>/trajectories`` for a bundle, the run itself otherwise."""
    return run_dir / _BUNDLE_TRAJECTORIES if _is_bundle(run_dir, detecting=detecting) else run_dir


def _eval_key(run_dir: Path) -> str:
    """The partition for ``run_dir``: ``run.eval_key`` for a bundle, else the legacy name split."""
    try:
        metadata = _metadata(run_dir)
    except ValueError:
        metadata = {}
    run = metadata.get("run")
    if isinstance(run, dict) and isinstance(key := run.get("eval_key"), str) and key:
        return key
    return _partition(run_dir.name)


def _default_model(metadata: dict) -> str | None:
    """The agent's model from a bundle's ``runtime_config.env_vars.MODEL_NAME``, or None."""
    runtime = metadata.get("runtime_config")
    if not isinstance(runtime, dict):
        return None
    env_vars = runtime.get("env_vars")
    if not isinstance(env_vars, dict):
        return None
    model = env_vars.get("MODEL_NAME")
    return model if isinstance(model, str) and model else None


def _context_summarization_enabled(run_dir: Path) -> bool:
    """True when a bundle's env records ``DYNACONF_CONTEXT_SUMMARIZATION__ENABLED=true``.

    Context summarization rewrites older messages, so the last call's input would no longer
    contain the whole run verbatim and agent_view resampling prefixes would differ from
    what the agent was asked. ``records()`` warns rather than skipping, since
    ``conversion=steps`` is unaffected.
    """
    try:
        metadata = _metadata(run_dir)
    except ValueError:
        return False
    runtime = metadata.get("runtime_config")
    env_vars = runtime.get("env_vars") if isinstance(runtime, dict) else None
    value = env_vars.get("DYNACONF_CONTEXT_SUMMARIZATION__ENABLED") if isinstance(env_vars, dict) else None
    return isinstance(value, str) and value.strip().lower() == "true"


def _task_files(run_dir: Path, *, detecting: bool = False) -> list[Path]:
    """The manifest's tasks (metadata.json task_ids), else every JSON that is not a run-level file.

    For a bundle the task files live under ``trajectories/``; for a legacy run they sit
    alongside ``metadata.json`` and ``results.json``. Reading a run with a bad
    metadata.json fails: the manifest is authoritative, and the glob could read tasks it
    leaves out. Detection falls back to the glob instead, so a parent's bad metadata.json
    neither hides its child runs nor ends the mine.
    """
    try:
        manifest = _metadata(run_dir).get("task_ids")
    except ValueError:
        if not detecting:
            raise
        manifest = None
    tasks_dir = _task_dir(run_dir, detecting=detecting)
    if manifest:
        files = []
        for task_id in manifest:
            task_file = tasks_dir / f"{task_id}.json"
            if task_file.exists():
                files.append(task_file)
            elif not detecting:
                logger.warning("manifest task %s has no trajectory file in %s", task_id, run_dir.name)
        return sorted(files)
    # Companion files beside a task use a known inner suffix (``<task>.agent_messages.json``,
    # ``<task>.calls.json``). Matching exact suffixes keeps task IDs that themselves contain
    # a dot ("appworld.v2_01.json") readable.
    return sorted(
        f
        for f in tasks_dir.glob("*.json")
        if f.stem not in _NOT_TASKS
        and not f.stem.startswith("appworld_sdk_")
        and not any(f.name.endswith(suffix) for suffix in _COMPANION_SUFFIXES)
    )


def load_task_manifest(path: Path) -> list[str]:
    """Task IDs from a JSON file (``task_ids`` or ``tasks[].task_id``) or one ID per line (``#`` comments)."""
    try:
        text = path.read_text()
        payload = json.loads(text) if path.suffix == ".json" else None
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read task manifest {path}: {exc}") from exc
    if payload is None:
        task_ids: Any = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    elif isinstance(payload, dict) and "task_ids" in payload:
        task_ids = payload["task_ids"]
    elif isinstance(payload, dict) and isinstance(tasks := payload.get("tasks"), list):
        task_ids = [item.get("task_id") if isinstance(item, dict) else None for item in tasks]
    else:
        task_ids = None
    if not (isinstance(task_ids, list) and task_ids and all(isinstance(task_id, str) and task_id for task_id in task_ids)):
        raise ValueError(f"No valid task IDs found in task manifest {path} (expected task_ids or tasks[].task_id)")
    return task_ids


def _run_dirs(path: Path) -> list[Path]:
    """path itself when it is a run, else its child runs.

    What makes a run is a task, not a marker file: neither a subdirectory of JSON inside
    a run (logs, traces, a bundle's ``trajectories/``) nor a parent's own results,
    metadata, appworld_sdk or summary file can make a directory the wrong kind. Whatever
    is left unread gets a warning: child runs beneath a run, a parent's own JSON files,
    and child directories with JSON but no readable task.
    """
    if _is_run_dir(path):
        bundle_task_dir = _task_dir(path, detecting=True) if _is_bundle(path, detecting=True) else None
        if nested := sorted(child.name for child in path.iterdir() if child != bundle_task_dir and _is_run_dir(child)):
            logger.warning("read %s as one run; not read: %s, which also hold tasks", path, ", ".join(nested))
        return [path]
    if not path.is_dir():
        return []
    children, unread = [], []
    for child in sorted(path.iterdir()):
        if _is_run_dir(child):
            children.append(child)
        elif child.is_dir() and any(child.glob("*.json")):
            unread.append(child.name)
    if unread:
        logger.warning("skipped %s in %s: no readable task file with an intent", ", ".join(unread), path)
    if children and (stray := sorted(f.name for f in path.glob("*.json"))):
        logger.warning("ignored %s in %s: it holds run directories, so it is not a run itself", ", ".join(stray), path)
    return children


def _partition(run: str) -> str:
    """The split a legacy run evaluated: "dev" in ``cuga_all_dev_<model>``, else the run's own name."""
    return run.split("_all_")[1].split("_")[0] if "_all_" in run else run


def _is_run_dir(path: Path) -> bool:
    """A directory records() would read: one with at least one task file carrying an intent.

    Covers both legacy runs (task files alongside ``metadata.json``) and bundles (task
    files under ``trajectories/``) because :func:`_task_files` resolves the right source
    for each layout.
    """
    return path.is_dir() and any(_has_intent(task_file) for task_file in _task_files(path, detecting=True))


def _has_intent(task_file: Path) -> bool:
    try:
        task = _read_json(task_file)
    except (OSError, ValueError):  # a malformed or non-object file is not a task here; reading a run reports it
        return False
    return isinstance(intent := task.get("intent"), str) and bool(intent.strip())


def _data(step: dict) -> str:
    return str(step.get("data") or "").strip()


def _read_json(path: Path) -> dict:
    with path.open() as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def _loads(value: Any) -> dict:
    """A dict from a dict or a JSON string; {} for anything else."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _plain(content: dict) -> dict:
    outcome = content["outcome"]
    return {**content, "outcome": None if outcome is None else outcome.model_dump(mode="json")}
