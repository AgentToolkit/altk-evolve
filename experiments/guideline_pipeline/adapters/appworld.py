"""AppWorld ReAct runs: one record per task, from the runner's own output tree.

A run directory holds ``tasks/<task_id>/logs/environment_io.md`` (the agent's
code and the environment's output, turn by turn) and ``evaluations/<split>.json``
(``{"individual": {"<task_id>": {"success", "difficulty", ...}}}``). ``--input``
is either a run directory or a parent of run directories.
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
from experiments.guideline_pipeline.adapters.cuga import agent_steps, elide_middle, load_task_manifest

logger = logging.getLogger(__name__)

SOURCE = "appworld"
# How far before the last "Task:" the user's "My name is:" line may start and still be kept.
IDENTITY_WINDOW = 600
# Without that line, the instruction starts this far before "Task:".
TASK_CONTEXT = 200

# One "### Environment Interaction N" block: an optional dashes line, a ```python
# fenced action, then a plain fenced observation. An opening fence ends at its own
# line ([ \t]*, not \s*), so an empty block cannot swallow the next interaction.
_INTERACTION = re.compile(
    r"^### Environment Interaction \d+\s*\n(?:-+\s*\n)?```python[ \t]*\n(?P<action>.*?)\n```\s*\n\s*```[ \t]*\n(?P<observation>.*?)\n```",
    re.MULTILINE | re.DOTALL,
)


@dataclass
class AppWorldAdapter:
    """Yields one AdapterRecord per AppWorld task. Configure with ``--adapter-option KEY=VALUE``.

    Options: task_ids (comma-separated), task_manifest (a JSON file with
    ``task_ids`` or ``tasks[].task_id``, or one ID per line), model (the agent's
    model, recorded on the trajectory) and appworld_root (an AppWorld checkout or
    data root whose ``data/tasks/<task_id>/specs.json`` is the last instruction source).
    """

    name: str = "appworld"
    task_ids: frozenset[str] = field(default_factory=frozenset)
    model: str | None = None
    appworld_root: Path | None = None

    def configure(self, options: Mapping[str, str]) -> AppWorldAdapter:
        changes: dict[str, Any] = {}
        task_ids = set(self.task_ids)
        for key, value in options.items():
            if key == "task_ids":
                task_ids.update(item.strip() for item in value.split(",") if item.strip())
            elif key == "task_manifest":
                task_ids.update(load_task_manifest(Path(value)))
            elif key == "model":
                changes[key] = value or None
            elif key == "appworld_root":
                root = Path(value).expanduser()
                if not root.is_dir():
                    raise ValueError(f"appworld option appworld_root must be a directory, not {value!r}")
                changes[key] = root
            else:
                raise ValueError(f"Unknown appworld option {key!r} (expected task_ids, task_manifest, model, appworld_root)")
        return replace(self, **changes, task_ids=frozenset(task_ids))

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        run_dirs = [path] if _is_run_dir(path) else sorted(child for child in path.iterdir() if _is_run_dir(child)) if path.is_dir() else []
        if not run_dirs:
            raise ValueError(f"No AppWorld run directory (one containing tasks/) at {path}")
        if self.task_ids:
            found = {task_dir.name for run_dir in run_dirs for task_dir in _task_dirs(run_dir)}
            if missing := sorted(self.task_ids - found):
                raise ValueError(f"Task IDs not found beneath {path}: {missing}")
        for run_dir in run_dirs:
            evaluations = _load_evaluations(run_dir)
            for task_dir in _task_dirs(run_dir):
                if self.task_ids and task_dir.name not in self.task_ids:
                    continue
                if (record := self.task_record(run_dir.name, task_dir, evaluations.get(task_dir.name))) is not None:
                    yield record

    def task_record(self, run: str, task_dir: Path, evaluation: tuple[str, dict] | None) -> AdapterRecord | None:
        """Read one task directory; None (with a warning) for a task that cannot be mined."""
        environment_io = task_dir / "logs" / "environment_io.md"
        if not environment_io.exists():
            logger.warning("skipped %s/%s: no logs/environment_io.md", run, task_dir.name)
            return None
        if (found := self._instruction(task_dir)) is None:
            logger.warning("skipped %s/%s: no task instruction found", run, task_dir.name)
            return None
        instruction, instruction_source = found
        record = self.record(
            run,
            task_dir.name,
            instruction=instruction,
            instruction_source=instruction_source,
            environment_io=environment_io.read_text(),
            evaluation=evaluation,
        )
        if len(record.messages) < 2:
            logger.warning("skipped %s/%s: no environment interactions in logs/environment_io.md", run, task_dir.name)
            return None
        return record

    def record(
        self,
        run: str,
        task_id: str,
        *,
        instruction: str,
        instruction_source: str,
        environment_io: str,
        evaluation: tuple[str, dict] | None = None,
    ) -> AdapterRecord:
        """Build one record from a task's instruction, its environment_io.md text and its (split, evaluation entry)."""
        split, entry = evaluation if evaluation is not None else (None, {})
        metadata = {
            "run": run,
            "split": split,
            "difficulty": _integer(entry.get("difficulty")),
            "model": self.model,  # also in metadata: the guideline processor stores metadata, not Trajectory.model
            "instruction_source": instruction_source,
        }
        success = _boolean_value(entry.get("success"))
        content: dict[str, Any] = {
            "messages": [{"role": "user", "content": instruction}, *elide_middle(agent_steps(parse_environment_io(environment_io)))],
            "trace_id": task_id,
            "model": self.model,
            "metadata": {key: value for key, value in metadata.items() if value is not None},
            "outcome": None if success is None else TrajectoryOutcome(success=success),
        }
        # The revision is a digest of exactly what processors see, so a corrected
        # log, evaluation entry or option change is reprocessed; nothing else is.
        plain = {**content, "outcome": None if success is None else content["outcome"].model_dump(mode="json")}
        digest = hashlib.sha256(json.dumps(plain, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        batch = TrajectoryBatch(source=SOURCE, conversation_id=f"{run}/{task_id}", batch_id=task_id, revision=digest)
        return AdapterRecord(**content, batch=batch)

    def _instruction(self, task_dir: Path) -> tuple[str, str] | None:
        """(instruction, source) from the first source that has it: tips, the first LM prompt, logger.jsonl, specs.json."""
        tips = task_dir / "tips_subtask.json"
        if tips.exists() and (text := _text(_read_json(tips).get("task_instruction"))):
            return text, "tips_subtask"
        if (text := _first_prompt_task(task_dir / "logs" / "lm_calls.jsonl")) is not None:
            return text, "lm_calls"
        if (text := _logger_task(task_dir / "logs" / "logger.jsonl")) is not None:
            return text, "logger"
        if self.appworld_root is not None:
            specs = self.appworld_root / "data" / "tasks" / task_dir.name / "specs.json"
            if specs.exists() and (text := _text(_read_json(specs).get("instruction"))):
                return text, "specs"
        return None


def parse_environment_io(text: str) -> list[dict[str, str]]:
    """Each interaction's code and output, as CUGA-shaped action and observation steps."""
    steps: list[dict[str, str]] = []
    for match in _INTERACTION.finditer(text):
        steps.append({"name": "Raw_Assistant_Response", "data": match.group("action")})
        steps.append({"name": "User_output", "data": match.group("observation")})
    return steps


def task_from_prompt(prompt: str) -> str | None:
    """The real task at the end of a prompt that may first carry a preamble, examples and a playbook.

    It starts at the last "Task:", or at the "My name is:" line before it when that is
    within IDENTITY_WINDOW characters (so the user's identity is kept), else
    TASK_CONTEXT characters before it. A prompt with no "Task:" has no task.
    """
    task = prompt.rfind("Task:")
    if task < 0:
        return None
    identity = prompt.rfind("My name is:", 0, task)
    start = identity if identity >= 0 and task - identity <= IDENTITY_WINDOW else max(0, task - TASK_CONTEXT)
    return prompt[start:].strip() or None


def _first_prompt_task(path: Path) -> str | None:
    """The task in the last user message of the first LM call; only that line of the (large) file is read."""
    if not path.exists():
        return None
    with path.open() as handle:
        line = next((line for line in handle if line.strip()), None)
    if line is None:
        return None
    call = json.loads(line)
    request = call.get("input") if isinstance(call, dict) else None
    messages = request.get("messages") if isinstance(request, dict) else None
    users = [message for message in messages or [] if isinstance(message, dict) and message.get("role") == "user"]
    return task_from_prompt(_message_text(users[-1].get("content"))) if users else None


def _logger_task(path: Path) -> str | None:
    if not path.exists():
        return None
    with path.open() as handle:
        for line in handle:
            if line.strip() and isinstance(entry := json.loads(line), dict) and entry.get("role") == "task":
                if text := _text(entry.get("content")):
                    return text
    return None


def _load_evaluations(run_dir: Path) -> dict[str, tuple[str, dict]]:
    """Each evaluated task's split (its evaluations file name) and entry."""
    evaluations: dict[str, tuple[str, dict]] = {}
    for path in sorted((run_dir / "evaluations").glob("*.json")):
        individual = _read_json(path).get("individual")
        if not isinstance(individual, dict):
            continue
        for task_id, entry in individual.items():
            if task_id in evaluations:
                raise ValueError(f"Task {task_id} is evaluated in both {evaluations[task_id][0]}.json and {path.name} in {run_dir}")
            evaluations[task_id] = (path.stem, entry if isinstance(entry, dict) else {})
    return evaluations


def _task_dirs(run_dir: Path) -> list[Path]:
    return sorted(child for child in (run_dir / "tasks").iterdir() if child.is_dir())


def _is_run_dir(path: Path) -> bool:
    return (path / "tasks").is_dir()


def _boolean_value(value: Any) -> bool | None:
    """A verdict stored as a bool or as the string "True"/"False"; None for anything else."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _integer(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _message_text(content: Any) -> str:
    """A chat message's text, whether content is a string or a list of text parts."""
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return str(content or "")


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _read_json(path: Path) -> dict:
    with path.open() as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value
