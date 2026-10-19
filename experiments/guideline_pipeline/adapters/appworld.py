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
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord, option_names
from experiments.guideline_pipeline.adapters.cuga import agent_steps, load_task_manifest, verdict

logger = logging.getLogger(__name__)

SOURCE = "appworld"
# How far before the last "Task:" the user's "My name is:" line may start and still be kept.
IDENTITY_WINDOW = 600
# Without that line, the instruction starts this far before "Task:".
TASK_CONTEXT = 200

# The log is split at its "### Environment Interaction N" headers, so a block missing a
# fence cannot swallow the next interaction. Each block is an optional dashes line, a
# ```python fenced action, then a plain fenced observation. An opening fence ends at its
# own line ([ \t]*, not \s*), so an empty block cannot swallow the next fence. The
# observation runs to the block's last fence, so printed Markdown with fences of its
# own is kept whole.
_HEADER = re.compile(r"^### Environment Interaction \d+[ \t]*$", re.MULTILINE)
_INTERACTION = re.compile(
    r"\A\n(?:-+[ \t]*\n)?```python[ \t]*\n(?P<action>.*?)\n```[ \t]*\n\s*```[ \t]*\n(?P<observation>.*)\n```\s*\Z",
    re.DOTALL,
)


@dataclass
class AppWorldAdapter:
    """Yields one AdapterRecord per AppWorld task. Configure with ``--adapter-option KEY=VALUE``.

    Options: task_ids (comma-separated), task_manifest (a JSON file with
    ``task_ids`` or ``tasks[].task_id``, or one ID per line), splits
    (comma-separated; only tasks evaluated in a listed ``evaluations/<split>.json``;
    without it, every task but those in a ``test*`` split),
    model (the agent's model, recorded on the trajectory) and appworld_root (an
    AppWorld checkout or data root whose ``data/tasks/<task_id>/specs.json`` is the
    last instruction source).
    """

    name: str = "appworld"
    task_ids: frozenset[str] = field(default_factory=frozenset)
    splits: frozenset[str] = field(default_factory=frozenset)
    model: str | None = None
    appworld_root: Path | None = None

    def configure(self, options: Mapping[str, str]) -> AppWorldAdapter:
        changes: dict[str, Any] = {}
        task_ids = set(self.task_ids)
        for key, value in options.items():
            if key == "task_ids":
                task_ids.update(option_names(SOURCE, key, value))
            elif key == "task_manifest":
                task_ids.update(load_task_manifest(Path(value)))
            elif key == "splits":
                changes[key] = frozenset(option_names(SOURCE, key, value))
            elif key == "model":
                changes[key] = value or None
            elif key == "appworld_root":
                root = Path(value.strip()).expanduser()
                if not value.strip() or not root.is_dir():  # Path("") is the working directory
                    raise ValueError(f"appworld option appworld_root must be a directory, not {value!r}")
                changes[key] = root
            else:
                raise ValueError(f"Unknown appworld option {key!r} (expected task_ids, task_manifest, splits, model, appworld_root)")
        return replace(self, **changes, task_ids=frozenset(task_ids))

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        run_dirs = _run_dirs(path)
        if not run_dirs:
            raise ValueError(f"No AppWorld run directory (one containing tasks/) at {path}")
        runs = [(run_dir, _load_evaluations(run_dir)) for run_dir in run_dirs]
        if self.splits:
            found_splits = {split for _, evaluations in runs for split, _ in evaluations.values()}
            if missing := sorted(self.splits - found_splits):
                raise ValueError(f"Splits not found beneath {path}: {missing} (found {sorted(found_splits)})")
        tasks = [
            (run_dir.name, task_dir, evaluations.get(task_dir.name)) for run_dir, evaluations in runs for task_dir in _task_dirs(run_dir)
        ]
        if not self.splits:
            # Guidelines mined from a test split leak into any later evaluation on it, so a
            # test split is mined only when splits= names it.
            test_splits: dict[str, set[str]] = {}
            for run, task_dir, evaluation in tasks:
                if evaluation is not None and evaluation[0].startswith("test") and (not self.task_ids or task_dir.name in self.task_ids):
                    test_splits.setdefault(run, set()).add(evaluation[0])
            for run, splits in test_splits.items():
                logger.warning("skipped test-split tasks in %s (%s); name the split in splits= to mine it", run, ", ".join(sorted(splits)))
        # A task with no evaluation has no known split, so a split filter leaves it out.
        tasks = [task for task in tasks if self._in_splits(task[2])]
        if self.task_ids:
            if missing := sorted(self.task_ids - {task_dir.name for _, task_dir, _ in tasks}):
                in_splits = f" in splits {sorted(self.splits)}" if self.splits else " outside test splits"
                raise ValueError(f"Task IDs not found beneath {path}{in_splits}: {missing}")
            tasks = [task for task in tasks if task[1].name in self.task_ids]
        read = 0
        for run, task_dir, evaluation in tasks:
            if (record := self.task_record(run, task_dir, evaluation)) is not None:
                read += 1
                yield record
        if not read:
            logger.warning("read no AppWorld task beneath %s", path)

    def _in_splits(self, evaluation: tuple[str, dict] | None) -> bool:
        """Whether a task with this (split, entry) is selected: in a listed split, else in any but a test split."""
        if self.splits:
            return evaluation is not None and evaluation[0] in self.splits
        return evaluation is None or not evaluation[0].startswith("test")

    def task_record(self, run: str, task_dir: Path, evaluation: tuple[str, dict] | None) -> AdapterRecord | None:
        """Read one task directory; None (with a warning) for a task that cannot be mined."""
        environment_io = task_dir / "logs" / "environment_io.md"
        if not environment_io.exists():
            logger.warning("skipped %s/%s: no logs/environment_io.md", run, task_dir.name)
            return None
        try:
            text = environment_io.read_text()
        except (OSError, ValueError) as exc:  # ValueError: not UTF-8
            logger.warning("skipped %s/%s: cannot read logs/environment_io.md (%s)", run, task_dir.name, exc)
            return None
        if (found := self._instruction(run, task_dir)) is None:
            logger.warning("skipped %s/%s: no task instruction found", run, task_dir.name)
            return None
        instruction, instruction_source = found
        record = self.record(
            run,
            task_dir.name,
            instruction=instruction,
            instruction_source=instruction_source,
            environment_io=text,
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
        success = verdict(entry.get("success"))
        content: dict[str, Any] = {
            "messages": [{"role": "user", "content": instruction}, *agent_steps(parse_environment_io(environment_io))],
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

    def _instruction(self, run: str, task_dir: Path) -> tuple[str, str] | None:
        """(instruction, source) from the first source that has it: tips, the first LM prompt, logger.jsonl, specs.json.

        A source that cannot be read (a run killed mid-write leaves a truncated line) is
        reported and the next one is tried, so one bad file never ends the run.
        """
        sources: list[tuple[str, Path, Callable[[Path], str | None]]] = [
            ("tips_subtask", task_dir / "tips_subtask.json", lambda path: _text(_read_json(path).get("task_instruction"))),
            ("lm_calls", task_dir / "logs" / "lm_calls.jsonl", _first_prompt_task),
            ("logger", task_dir / "logs" / "logger.jsonl", _logger_task),
        ]
        if self.appworld_root is not None:
            specs = self.appworld_root / "data" / "tasks" / task_dir.name / "specs.json"
            sources.append(("specs", specs, lambda path: _text(_read_json(path).get("instruction"))))
        for source, path, read in sources:
            if not path.exists():
                continue
            try:
                text = read(path)
            except (OSError, ValueError) as exc:  # ValueError: malformed JSON, a non-object, or not UTF-8
                logger.warning("%s/%s: cannot read %s (%s); trying the next instruction source", run, task_dir.name, path.name, exc)
                continue
            if text is not None:
                return text, source
        return None


def parse_environment_io(text: str) -> list[dict[str, str]]:
    """Each interaction's code and output, as CUGA-shaped action and observation steps."""
    steps: list[dict[str, str]] = []
    for block in _HEADER.split(text)[1:]:
        if (match := _INTERACTION.match(block)) is None:
            continue
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
    with path.open() as handle:
        line = next((line for line in handle if line.strip()), None)
    if line is None:
        return None
    call = json.loads(line)
    request = call.get("input") if isinstance(call, dict) else None
    messages = request.get("messages") if isinstance(request, dict) else None
    # Anything but a list has no user message, so the next instruction source is tried.
    users = [
        message
        for message in (messages if isinstance(messages, list) else [])
        if isinstance(message, dict) and message.get("role") == "user"
    ]
    return task_from_prompt(_message_text(users[-1].get("content"))) if users else None


def _logger_task(path: Path) -> str | None:
    """The first non-blank role "task" entry in logger.jsonl."""
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
        try:
            individual = _read_json(path).get("individual")
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read {path}: {exc}") from exc
        if not isinstance(individual, dict):  # outcomes would silently go missing for every task
            raise ValueError(f"Cannot read {path}: no 'individual' mapping of task IDs to evaluations")
        for task_id, entry in individual.items():
            if task_id in evaluations:
                raise ValueError(f"Task {task_id} is evaluated in both {evaluations[task_id][0]}.json and {path.name} in {run_dir}")
            evaluations[task_id] = (path.stem, entry if isinstance(entry, dict) else {})
    return evaluations


def _run_dirs(path: Path) -> list[Path]:
    """path itself when it is a run, else its child runs; children that are not runs are reported, not silently passed over.

    AppWorld's legacy agents nest runs deeper (``<agent>/<provider>/<model>/<split>``),
    so such a child is named in a warning: pass the run, or its parent, as --input.
    """
    if _is_run_dir(path):
        return [path]
    if not path.is_dir():
        return []
    children, other = [], []
    for child in sorted(path.iterdir()):
        if _is_run_dir(child):
            children.append(child)
        elif child.is_dir():
            other.append(child.name)
    if children and other:
        logger.warning("ignored %s in %s: no tasks/ directory", ", ".join(other), path)
    return children


def _task_dirs(run_dir: Path) -> list[Path]:
    return sorted(child for child in (run_dir / "tasks").iterdir() if child.is_dir())


def _is_run_dir(path: Path) -> bool:
    return (path / "tasks").is_dir()


def _integer(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _message_text(content: Any) -> str:
    """A chat message's text, whether content is a string or a list of text parts; "" for anything else.

    Nothing else is stringified: the repr of a dict could still contain "Task:".
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    return ""


def _text(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _read_json(path: Path) -> dict:
    with path.open() as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value
