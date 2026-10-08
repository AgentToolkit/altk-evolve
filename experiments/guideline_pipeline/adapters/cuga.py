"""CUGA benchmark runs: one record per task, from per-task JSON plus the run's results.json.

A run directory holds one ``<task_id>.json`` per task, ``{"intent", "task_id",
"score", "steps": [{"name", "data", "prompts"?}, ...]}``, a ``results.json``
mapping task IDs to their evaluation, and optionally a ``metadata.json`` whose
``task_ids`` list is the authoritative task manifest. ``--input`` is either a
run directory or a parent of run directories.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord
from experiments.guideline_pipeline.adapters.elision import elide_middle

logger = logging.getLogger(__name__)

SOURCE = "cuga"
STEP_CHARS = 2000  # the extractor truncates each step to 2000 characters; slice early to keep payloads small
DETAIL_CHARS = 2000
FAIL_TRACE_CHARS = 600
_ACTION_STEPS = ("Raw_Assistant_Response", "Assistant_response")
_OBSERVATION_STEPS = ("User_output", "Observation", "Tool_output")
_NOT_TASKS = ("results", "metadata")


@dataclass
class CugaAdapter:
    """Yields one AdapterRecord per CUGA task. Configure with ``--adapter-option KEY=VALUE``.

    Options: include_system_prompt, include_summaries (booleans; add them as
    context_messages), task_ids (comma-separated), task_manifest (a JSON file with
    ``task_ids`` or ``tasks[].task_id``, or one ID per line) and model (the agent's
    model, recorded on the trajectory).
    """

    name: str = "cuga"
    include_system_prompt: bool = False
    include_summaries: bool = False
    task_ids: frozenset[str] = field(default_factory=frozenset)
    model: str | None = None

    def configure(self, options: Mapping[str, str]) -> CugaAdapter:
        changes: dict[str, Any] = {}
        task_ids = set(self.task_ids)
        for key, value in options.items():
            if key in ("include_system_prompt", "include_summaries"):
                changes[key] = _boolean(key, value)
            elif key == "task_ids":
                task_ids.update(item.strip() for item in value.split(",") if item.strip())
            elif key == "task_manifest":
                task_ids.update(load_task_manifest(Path(value)))
            elif key == "model":
                changes[key] = value or None
            else:
                raise ValueError(
                    f"Unknown cuga option {key!r} (expected include_system_prompt, include_summaries, task_ids, task_manifest, model)"
                )
        return replace(self, **changes, task_ids=frozenset(task_ids))

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        run_dirs = [path] if _is_run_dir(path) else sorted(child for child in path.iterdir() if _is_run_dir(child)) if path.is_dir() else []
        if not run_dirs:
            raise ValueError(f"No CUGA run directory at {path}")
        if self.task_ids:
            found = {task_file.stem for run_dir in run_dirs for task_file in _task_files(run_dir)}
            if missing := sorted(self.task_ids - found):
                raise ValueError(f"Task IDs not found beneath {path}: {missing}")
        for run_dir in run_dirs:
            results = _load_results(run_dir)
            experiment = _read_json(run_dir / "metadata.json").get("experiment_name") if (run_dir / "metadata.json").exists() else None
            for task_file in _task_files(run_dir):
                if self.task_ids and task_file.stem not in self.task_ids:
                    continue
                yield self.record(run_dir.name, task_file.stem, _read_json(task_file), results.get(task_file.stem), experiment=experiment)

    def record(self, run: str, task_id: str, task: dict, result: dict | None, *, experiment: str | None = None) -> AdapterRecord:
        """Build one record from a parsed task file and its results.json entry (if any)."""
        steps = [step for step in task.get("steps") or [] if isinstance(step, dict)]
        evaluation = _evaluation(steps, result)
        metadata = {
            "run": run,
            "partition": run.split("_all_")[1].split("_")[0] if "_all_" in run else run,
            "dataset": task.get("dataset_name") or None,
            "experiment": experiment,
            "model": self.model,  # also in metadata: the guideline processor stores metadata, not Trajectory.model
            "score": task.get("score"),
            "pass_percentage": evaluation.get("pass_percentage"),
        }
        content: dict[str, Any] = {
            "messages": [{"role": "user", "content": task.get("intent") or ""}, *elide_middle(_agent_steps(steps))],
            "context_messages": self._context(steps),
            "trace_id": task_id,
            "model": self.model,
            "metadata": {key: value for key, value in metadata.items() if value is not None},
            "outcome": _outcome(task, evaluation),
        }
        # The revision is a digest of exactly what processors see, so a corrected
        # task file, results entry or option change is reprocessed; nothing else is.
        digest = hashlib.sha256(json.dumps(_plain(content), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        batch = TrajectoryBatch(source=SOURCE, conversation_id=f"{run}/{task_id}", batch_id=task_id, revision=digest)
        return AdapterRecord(**content, batch=batch)

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


def _agent_steps(steps: list[dict]) -> list[dict[str, Any]]:
    """Actions become function calls and observations become reasoning steps, one extractor step each.

    role "tool" would be dropped by the extractor, so observations are assistant messages.
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
    """The task's evaluation: the EvaluationResult step, overridden by the authoritative results.json eval."""
    step = next((_loads(step.get("data")) for step in steps if step.get("name") == "EvaluationResult"), {})
    return {**step, **_loads((result or {}).get("eval"))}


def _outcome(task: dict, evaluation: dict) -> TrajectoryOutcome | None:
    success = evaluation.get("success")
    if success is None and isinstance(task.get("score"), (int, float)):
        success = task["score"] >= 1.0
    if success is None:
        return None
    report = evaluation.get("evaluation") or {}
    failed = (str(check.get("requirement") or "").strip().replace("\n", " ") for check in report.get("failures") or [])
    detail = _evaluation_report(evaluation)
    return TrajectoryOutcome(
        success=bool(success),
        failed_checks=tuple(dict.fromkeys(check for check in failed if check)),
        detail=detail[:DETAIL_CHARS] if detail else None,
    )


def _evaluation_report(evaluation: dict) -> str | None:
    """Every ground-truth check that ran, with the start of each failure's trace."""
    report = evaluation.get("evaluation")
    if not isinstance(report, dict):
        return None
    lines = [f"num_tests={report.get('num_tests')} pass_count={report.get('pass_count')} pass_percentage={report.get('pass_percentage')}"]
    for heading, checks in (("PASSED checks:", report.get("passes") or []), ("FAILED checks:", report.get("failures") or [])):
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
    path = run_dir / "results.json"
    results = _read_json(path) if path.exists() else {}
    return {task_id: entry for task_id, entry in results.items() if isinstance(entry, dict)}


def _task_files(run_dir: Path) -> list[Path]:
    """The manifest's tasks (metadata.json task_ids), else every JSON that is not a run-level file."""
    manifest = _read_json(run_dir / "metadata.json").get("task_ids") if (run_dir / "metadata.json").exists() else None
    if manifest:
        files = []
        for task_id in manifest:
            task_file = run_dir / f"{task_id}.json"
            if task_file.exists():
                files.append(task_file)
            else:
                logger.warning("manifest task %s has no trajectory file in %s", task_id, run_dir.name)
        return sorted(files)
    return sorted(f for f in run_dir.glob("*.json") if f.stem not in _NOT_TASKS and not f.stem.startswith("appworld_sdk_"))


def load_task_manifest(path: Path) -> list[str]:
    """Task IDs from a JSON file (``task_ids`` or ``tasks[].task_id``) or one ID per line (``#`` comments)."""
    if path.suffix == ".json":
        payload = json.loads(path.read_text())
        task_ids = payload.get("task_ids")
        if task_ids is None:
            task_ids = [item.get("task_id") for item in payload.get("tasks", [])]
    else:
        task_ids = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not task_ids or not all(isinstance(task_id, str) and task_id for task_id in task_ids):
        raise ValueError(f"No valid task IDs found in {path}")
    return list(task_ids)


def _is_run_dir(path: Path) -> bool:
    return path.is_dir() and any(path.glob("*.json"))


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


def _boolean(key: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"cuga option {key} must be true or false, not {value!r}")
