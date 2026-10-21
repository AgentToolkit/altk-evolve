"""τ-bench retail: one human-readable ``task_*.txt`` trajectory per task.

Each file is a run of sections separated by ``=====`` rules (five or more), each
named by its first line and optionally underlined with ``----``::

    TASK <n>  reward=<r>  breakdown={...}  [termination_reason=... model=... ...]
    USER SCENARIO                      the user simulator's hidden instructions
    GROUND-TRUTH EVALUATION CRITERIA   the expected tool calls
    AGENT DOMAIN POLICY                the policy handed to the agent
    CONVERSATION                       [ASSISTANT] / [USER] turns, and tool I/O as
                                       ``-> CALL name(...)`` + ``args: {...}`` and
                                       ``<- RESULT ...`` + the result body
    FINAL ANSWER
    JUDGE / REWARD DETAILS             per-action matches and assertion results

The TASK header (with a reward) and a CONVERSATION with a user turn are required; a
file without them, or with a section that appears twice, is skipped with a warning
(see ``TauRetailAdapter.records``).

The task is the conversation's first user turn: that is what the agent was asked.
The USER SCENARIO is what the simulator was told (reason_for_call, known_info,
task_instructions), facts the agent never saw, so it is not part of the messages.

In the judge section, a check is recognised by tau2's result field names, one
check per line: ``action_match=False`` marks an expected action the agent did not
perform, and ``met=False`` an NL assertion (or communicate check) that failed.
When the flag is alone on its line (pretty-printed JSON or dict reprs), the check
is named from the object around it: tau2's action name and arguments, or the
``nl_assertion`` text. The criteria and final answer sections are not used: the outcome carries the
verdict, and the final answer is the conversation's last turn.
"""

from __future__ import annotations

import ast
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
from experiments.guideline_pipeline.adapters.cuga import elide_middle

logger = logging.getLogger(__name__)

SOURCE = "tau-retail"
SUCCESS_REWARD = 1.0  # τ-bench rewards are 0 or 1; anything below full reward is a failure
DETAIL_CHARS = 2000
# render_supporting_context truncates each JSON-rendered context message at 2000 chars;
# leave room for the JSON envelope and escaped newlines.
CONTEXT_CHUNK_CHARS = 1500

# parse_openai_agents_trajectory's default window (EVOLVE_TRAJECTORY_*): the first 50
# agent steps, each truncated to 2000 chars. Long turns are split under the per-step
# cap, and a long conversation keeps its first ELISION_HEAD steps and its ending
# around one marker (the CUGA adapter's elide_middle), so the ending reaches the
# default head-only window.
PARSER_STEP_CHARS = 2000
ELISION_HEAD = 25

_SECTIONS = (
    ("TASK ", "HEADER"),
    ("USER SCENARIO", "SCENARIO"),
    ("GROUND-TRUTH EVALUATION", "CRITERIA"),
    ("AGENT DOMAIN POLICY", "POLICY"),
    ("CONVERSATION", "CONVERSATION"),
    ("FINAL ANSWER", "FINAL"),
    ("JUDGE", "JUDGE"),
)
# A rule separates sections only when a section title (or the end of the file) follows it,
# so a ===== line inside a conversation turn stays part of that turn.
_RULE = re.compile(
    r"^={5,}[ \t]*(?:\n|\Z)(?=(?:[ \t]*\n)*[ \t]*(?:" + "|".join(re.escape(prefix) for prefix, _ in _SECTIONS) + r"|\Z))", re.M
)
_HEADER_KEYS = ("termination_reason", "duration", "messages", "agent_cost", "user_cost", "sim_id", "trial", "seed", "model", "split")
_SPEAKER = re.compile(r"^\[(ASSISTANT|USER)\](?:\s*@\S+)?\s*(.*)$")
_CALL = re.compile(r"^\s*->\s*CALL\s+([A-Za-z0-9_]+)\s*(\(.*)$")
_RESULT = re.compile(r"^\s*<-\s*RESULT\b\s*(.*)$")
# key=False, or key: false as JSON or a Python dict repr writes it (quoted key, optionally quoted value).
_ACTION_MATCH = re.compile(r"""(?<!\w)(["']?)action_match\1\s*[=:]\s*["']?(true|false)\b["']?,?""", re.I)
_MET = re.compile(r"""(?<!\w)(["']?)met\1\s*[=:]\s*["']?(true|false)\b["']?,?""", re.I)


class MalformedTrajectory(ValueError):
    """A task file that lacks a section the adapter needs."""


@dataclass(frozen=True)
class Turn:
    kind: Literal["agent", "user", "call", "result"]
    text: str
    name: str = ""  # the tool name, for calls


def split_sections(text: str) -> dict[str, str]:
    """Map each recognised section name to its block, title line included.

    Raises MalformedTrajectory when a section appears twice: a ===== line inside a
    turn, followed by a line that starts like a section title, splits the file
    there, and keeping either copy would silently drop or replace turns.
    """
    sections: dict[str, str] = {}
    for block in _RULE.split(text):
        first = next((line.strip() for line in block.splitlines() if line.strip()), "")
        for prefix, name in _SECTIONS:
            if first.startswith(prefix):
                if name in sections:
                    raise MalformedTrajectory(
                        f"section {name} appears twice (is a ===== line inside a turn followed by a line starting {prefix.strip()!r}?)"
                    )
                sections[name] = block.strip("\n")
                break
    return sections


def section_body(block: str) -> str:
    """A section without its title line and ``----`` underline."""
    lines = block.strip("\n").splitlines()[1:]
    if lines and lines[0].strip() and set(lines[0].strip()) <= {"-"}:
        lines = lines[1:]
    return "\n".join(lines).strip()


def parse_header(block: str) -> dict[str, Any]:
    """The TASK line's fields: reward as a float, breakdown and the rest as raw strings."""
    header: dict[str, Any] = {}
    if match := re.search(r"(?<!\w)reward=(-?[0-9.]+)", block):
        try:
            header["reward"] = float(match.group(1))
        except ValueError:
            pass
    if match := re.search(r"(?<!\w)breakdown=(\{.*?\})", block):
        header["breakdown"] = match.group(1)
    for key in _HEADER_KEYS:
        if match := re.search(rf"(?<!\w){key}=(\S+)", block):
            header[key] = match.group(1)
    return header


def parse_conversation(block: str) -> list[Turn]:
    """The CONVERSATION section as ordered turns; a marker's line may carry text too."""
    turns: list[Turn] = []
    kind: Literal["agent", "user", "call", "result"] | None = None
    name = ""
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if kind == "call":
            # Arguments follow "args:", on the CALL line or below it; otherwise they are the call's parentheses.
            if args := re.search(r"args:\s*(.*)", text, re.S):
                text = args.group(1).strip()
            elif text.startswith("(") and text.endswith(")"):
                text = text[1:-1].strip()
            turns.append(Turn("call", text, name))
        elif kind is not None and text:
            turns.append(Turn(kind, text))
        buffer.clear()

    for line in section_body(block).splitlines():
        if speaker := _SPEAKER.match(line):
            flush()
            kind = "agent" if speaker.group(1) == "ASSISTANT" else "user"
            rest = speaker.group(2)
        elif call := _CALL.match(line):
            flush()
            kind, name, rest = "call", call.group(1), call.group(2)
        elif result := _RESULT.match(line):
            flush()
            kind, rest = "result", result.group(1)
        else:
            if kind is not None:
                buffer.append(line)
            continue
        if rest.strip():
            buffer.append(rest)
    flush()
    return turns


def _reasoning(content: str) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def _chunks(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _labelled_steps(label: str, text: str) -> list[dict[str, Any]]:
    """Text as reasoning steps short enough that the parser's per-step truncation drops nothing."""
    pieces = _chunks(text, PARSER_STEP_CHARS - len(label) - 20)  # room for " (part i/n):\n"
    if len(pieces) == 1:
        return [_reasoning(f"{label}:\n{text}")]
    return [_reasoning(f"{label} (part {i}/{len(pieces)}):\n{piece}") for i, piece in enumerate(pieces, 1)]


def _call_step(index: int, name: str, raw_args: str) -> dict[str, Any]:
    """A tool call in the function_call shape the parser reads; args that aren't a JSON object are kept under _raw."""
    try:
        parsed = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        parsed = None
    arguments = json.dumps(parsed) if isinstance(parsed, dict) else json.dumps({"_raw": raw_args})
    return {
        "role": "assistant",
        "content": [{"type": "function_call", "id": f"call_{index}", "function": {"name": name, "arguments": arguments}}],
    }


_LABELS = {"result": "OBSERVATION", "user": "USER MESSAGE", "agent": "AGENT MESSAGE"}


def conversation_steps(turns: list[Turn]) -> list[dict[str, Any]]:
    """Calls become function calls; results and both speakers' turns become assistant reasoning.

    The parser ignores tool messages and every user message after the first, so
    observations and user turns are carried as labelled assistant steps.
    """
    steps: list[dict[str, Any]] = []
    calls = 0
    for turn in turns:
        if turn.kind == "call":
            steps.append(_call_step(calls, turn.name, turn.text))
            calls += 1
        else:
            steps.extend(_labelled_steps(_LABELS[turn.kind], turn.text))
    return steps


def task_messages(turns: list[Turn]) -> list[dict[str, Any]]:
    """The first user turn as the task (what the agent was actually asked), the other turns as steps, in order.

    Turns before it (usually the agent's greeting) stay before it: the parser takes the
    first user message as the task wherever it is, and reads every assistant message as
    a step. Raises MalformedTrajectory when the conversation has no user turn.
    """
    first = next((i for i, turn in enumerate(turns) if turn.kind == "user"), None)
    if first is None:
        raise MalformedTrajectory("conversation has no user turn")
    leading = conversation_steps(turns[:first])
    steps = leading + conversation_steps(turns[first + 1 :])
    kept = elide_middle(steps, head=ELISION_HEAD)
    # Elision keeps the head, so the task stays right after the leading steps it kept.
    position = len(leading) if kept is steps else min(len(leading), ELISION_HEAD)
    return [*kept[:position], {"role": "user", "content": turns[first].text}, *kept[position:]]


def _enclosing_object(text: str, start: int, end: int) -> dict[str, Any] | None:
    """The JSON object or Python dict repr around text[start:end], parsed; None when there is none or it does not parse."""
    # JSON: the nearest "{" before the flag whose object reaches past it. The decoder skips braces inside strings.
    decoder = json.JSONDecoder()
    begin = text.rfind("{", 0, start)
    while begin != -1:
        try:
            value, stop = decoder.raw_decode(text, begin)
        except ValueError:
            value, stop = None, -1
        if stop >= end and isinstance(value, dict):
            return value
        begin = text.rfind("{", 0, begin)
    return _enclosing_repr(text, start, end)


def _enclosing_repr(text: str, start: int, end: int) -> dict[str, Any] | None:
    """The Python dict repr around text[start:end], found by brace counting (best effort: braces in strings confuse it)."""
    depth, begin = 0, start
    while True:
        if begin == 0:
            return None
        begin -= 1
        if text[begin] == "}":
            depth += 1
        elif text[begin] == "{":
            if depth == 0:
                break
            depth -= 1
    depth, finish = 0, end
    while True:
        if finish == len(text):
            return None
        if text[finish] == "{":
            depth += 1
        elif text[finish] == "}":
            if depth == 0:
                break
            depth -= 1
        finish += 1
    try:
        value = ast.literal_eval(text[begin : finish + 1])
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _describe_check(check: dict[str, Any], flag: re.Pattern[str]) -> str:
    """What a check object is about: tau2's action name and arguments, or the NL assertion's text."""
    if flag is _ACTION_MATCH:
        action = check["action"] if isinstance(check.get("action"), dict) else check
        if isinstance(name := action.get("name"), str) and name:
            arguments = action.get("arguments")
            return name if arguments is None else f"{name}({json.dumps(arguments, ensure_ascii=False)})"
    elif isinstance(assertion := check.get("nl_assertion"), str) and assertion:
        return assertion
    flag_key = "action_match" if flag is _ACTION_MATCH else "met"
    return json.dumps({key: value for key, value in check.items() if key != flag_key}, ensure_ascii=False, default=str)


def _check_text(judge: str, offset: int, line: str, flag: re.Pattern[str]) -> str:
    """The check a flag line reports; when the line holds only the flag (pretty-printed JSON), its enclosing object's."""
    text = " ".join(flag.sub("", line).split()).strip(" ,;-")
    if not text.strip("{}[] ") and (match := flag.search(line)):
        if (check := _enclosing_object(judge, offset + match.start(), offset + match.end())) is not None:
            return _describe_check(check, flag)[:300]
    return text[:300] or line.strip()


def judge_outcome(reward: float, judge: str) -> TrajectoryOutcome:
    """success from the reward; failed checks and the start of the report from the judge section."""
    failed: list[str] = []
    offset = 0
    for raw in judge.splitlines(keepends=True):
        line = raw.rstrip("\r\n")
        if (match := _ACTION_MATCH.search(line)) and match.group(2).lower() == "false":
            failed.append(f"expected action not matched: {_check_text(judge, offset, line, _ACTION_MATCH)}")
        elif (match := _MET.search(line)) and match.group(2).lower() == "false":
            failed.append(f"assertion not met: {_check_text(judge, offset, line, _MET)}")
        offset += len(raw)
    return TrajectoryOutcome(success=reward >= SUCCESS_REWARD, failed_checks=tuple(failed), detail=judge[:DETAIL_CHARS] or None)


def _system_context(label: str, text: str) -> list[dict[str, Any]]:
    """Text as system context, chunked: never the task instruction, never counted as new material."""
    pieces = _chunks(text, CONTEXT_CHUNK_CHARS)
    if len(pieces) == 1:
        return [{"role": "system", "content": f"{label}:\n{text}"}]
    return [{"role": "system", "content": f"{label} (part {i}/{len(pieces)}):\n{piece}"} for i, piece in enumerate(pieces, 1)]


def _task_number(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)$", path.stem)
    return (int(match.group(1)) if match else -1, path.name)


def _task_files(path: Path) -> list[Path]:
    """path itself when it is a file, else its task_*.txt files in task-number order; a FileNotFoundError when there are none."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"no such file or directory: {path}")
    files = sorted(path.glob("task_*.txt"), key=_task_number)
    if not files:
        nested = sorted(child.name for child in path.iterdir() if child.is_dir() and any(child.glob("task_*.txt")))
        hint = f"; found some in {', '.join(nested)}, so pass that directory as --input" if nested else ""
        raise FileNotFoundError(f"no task_*.txt files in {path}{hint}")
    return files


def _header_split(file: Path) -> str | None:
    """The split the task's header records, if any; None for a file without a header. Raises MalformedTrajectory."""
    header = split_sections(file.read_bytes().decode("utf-8", errors="replace")).get("HEADER")
    return parse_header(header).get("split") if header else None


def _task_key(task_id: str) -> str:
    """A task ID as split files and file names are matched on: a number without leading zeros, else the ID itself."""
    task_id = task_id.strip()
    return str(int(task_id)) if task_id.isdecimal() else task_id


def _file_task_key(file: Path) -> str:
    number, _ = _task_number(file)
    return str(number) if number >= 0 else file.stem


def load_split_file(path: Path) -> dict[str, frozenset[str]]:
    """tau2's ``split_tasks.json``: each split's name and task IDs. A ValueError naming the file if it is not one."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"Cannot read {SOURCE} split file {path}: {exc}") from exc
    if not (
        isinstance(payload, dict)
        and payload
        and all(
            isinstance(ids, list) and all(isinstance(task_id, (str, int)) and not isinstance(task_id, bool) for task_id in ids)
            for ids in payload.values()
        )
    ):
        raise ValueError(
            f"{SOURCE} split file {path} must be a JSON object mapping each split to a list of task IDs, like tau2's split_tasks.json"
        )
    return {name: frozenset(_task_key(str(task_id)) for task_id in ids) for name, ids in payload.items()}


@dataclass
class TauRetailAdapter:
    """Reads a directory of ``task_*.txt`` files, or one such file, into one record per task.

    Options (``--adapter-option KEY=VALUE``): include_policy and include_scenario
    (booleans; add the agent domain policy or the user simulator's scenario as
    context_messages), splits (comma-separated; only tasks in these splits) and
    split_file (tau2's ``split_tasks.json``). With split_file, a task is in a split
    when the file lists its number (``task_12.txt`` is task ``12``); without it,
    when its header records that ``split=``. Without explicit splits, either
    source marking a task ``test*`` excludes it (case-insensitive); tasks without
    train/test membership are retained with a summary warning.
    """

    name: str = SOURCE
    include_policy: bool = False
    include_scenario: bool = False
    splits: frozenset[str] = field(default_factory=frozenset)
    split_file: Path | None = None
    # Explicit split_file selection; None uses header selection or the default test exclusion.
    split_task_keys: frozenset[str] | None = field(default=None, init=False)

    default_test_keys: frozenset[str] = field(default_factory=frozenset, init=False)
    known_split_keys: frozenset[str] = field(default_factory=frozenset, init=False)

    def configure(self, options: Mapping[str, str]) -> TauRetailAdapter:
        changes: dict[str, Any] = {}
        for key, value in options.items():
            if key in ("include_policy", "include_scenario"):
                changes[key] = option_boolean(SOURCE, key, value)
            elif key == "splits":
                changes[key] = frozenset(option_names(SOURCE, key, value))
            elif key == "split_file":
                changes[key] = Path(value)
            else:
                raise ValueError(f"Unknown {SOURCE} option {key!r} (expected include_policy, include_scenario, splits, split_file)")
        return replace(self, **changes)

    def __post_init__(self) -> None:
        if self.split_file is None:
            return
        table = load_split_file(self.split_file)
        if missing := sorted(self.splits - table.keys()):
            raise ValueError(f"Splits not found in {SOURCE} split file {self.split_file}: {missing} (found {sorted(table)})")
        self.split_task_keys = frozenset().union(*(table[split] for split in self.splits)) if self.splits else None
        self.default_test_keys = frozenset().union(*(keys for name, keys in table.items() if name.lower().startswith("test")))
        self.known_split_keys = frozenset().union(
            *(keys for name, keys in table.items() if name.lower() == "train" or name.lower().startswith("test"))
        )

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        """Yield one record per task file after selecting files by split.

        Default and explicit header selection read headers before yielding records.
        The default summary counts unknown tasks only after successful parsing.

        A malformed file is logged and skipped, so one bad task doesn't end the run
        (an exception here would stop mine's iteration); a missing or empty input
        raises, because then nothing can be read at all, and so does a header split
        filter naming a split no task records. Reading no task at all logs a warning.
        """
        files = _task_files(path)
        skipped = unknown = 0
        unknown_files: set[Path] = set()
        if self.split_task_keys is not None:
            files = [file for file in files if _file_task_key(file) in self.split_task_keys]
        elif self.splits:
            files = self._by_header_split(path, files)
        else:
            kept = []
            for file in files:
                try:
                    header_split = _header_split(file)
                except MalformedTrajectory as exc:
                    logger.warning("skipping %s: %s", file, exc)
                    continue
                key = _file_task_key(file)
                is_test = key in self.default_test_keys or bool(header_split and header_split.lower().startswith("test"))
                known = key in self.known_split_keys or bool(header_split and header_split.lower() == "train")
                if is_test:
                    skipped += 1
                else:
                    kept.append(file)
                    if not known:
                        unknown_files.add(file)
            files = kept
        read = 0
        for file in files:
            try:
                record = self.record(file)
            except MalformedTrajectory as exc:
                logger.warning("skipping %s: %s", file, exc)
                continue
            read += 1
            unknown += file in unknown_files
            yield record
        if skipped:
            logger.warning(
                "skipped test-split tasks in %s: %s; kept %s tasks with unknown split; "
                "use splits= to opt in to test splits; use split_file= to classify base or unknown tasks",
                path,
                skipped,
                unknown,
            )
        elif unknown:
            logger.warning(
                "kept %s tasks with unknown split in %s; use split_file= to classify base or unknown tasks",
                unknown,
                path,
            )
        if not read:
            logger.warning("read no %s task in %s", SOURCE, path)

    def _by_header_split(self, path: Path, files: list[Path]) -> list[Path]:
        """The files whose header records one of self.splits; a ValueError when a listed split is recorded nowhere."""
        splits: dict[Path, str | None] = {}
        for file in files:
            try:
                splits[file] = _header_split(file)
            except MalformedTrajectory as exc:
                logger.warning("skipping %s: %s", file, exc)
        found = {split for split in splits.values() if split is not None}
        if missing := sorted(self.splits - found):
            raise ValueError(f"Splits not found in {path}: {missing} (found {sorted(found)})")
        for file, split in splits.items():
            if split is None:
                logger.warning(
                    "skipping %s: its header records no split, so splits=%s cannot select it", file, ",".join(sorted(self.splits))
                )
        return [file for file, split in splits.items() if split in self.splits]

    def record(self, file: Path) -> AdapterRecord:
        """Build the record for one task file; raises MalformedTrajectory if a required section is missing."""
        sections = split_sections(file.read_bytes().decode("utf-8", errors="replace"))
        missing = [name for name in ("HEADER", "CONVERSATION") if name not in sections]
        if missing:
            raise MalformedTrajectory(f"missing section(s): {', '.join(missing)}")
        header = parse_header(sections["HEADER"])
        if "reward" not in header:
            raise MalformedTrajectory("TASK header has no reward")
        turns = parse_conversation(sections["CONVERSATION"])
        if not turns:
            raise MalformedTrajectory("conversation has no turns")
        context: list[dict[str, Any]] = []
        if self.include_policy and (policy := section_body(sections.get("POLICY", ""))):
            context.extend(_system_context("AGENT DOMAIN POLICY", policy))
        if self.include_scenario and (scenario := section_body(sections.get("SCENARIO", ""))):
            # Last, because the context renderer keeps the most recent messages within its budget.
            context.extend(_system_context("USER SCENARIO (the user simulator's brief; the agent did not see it)", scenario))

        task_id = file.stem
        # Splits can reuse task numbers (tau-bench's train and test sets both start at 0), so a recorded
        # split is part of the task's identity: the trace_id (each guideline's source_task_id, which
        # lineage and export key on) and the conversation.
        source_task = f"{header['split']}/{task_id}" if "split" in header else task_id
        outcome = judge_outcome(header["reward"], section_body(sections.get("JUDGE", "")))
        content: dict[str, Any] = {
            "messages": task_messages(turns),
            "context_messages": context,
            "trace_id": source_task,
            "model": header.get("model"),
            "metadata": {key: value for key, value in header.items() if key != "model"},
        }
        # The revision digests exactly what processors see, so an edited task or an option that
        # changes the content is reprocessed, and nothing else is.
        plain = {**content, "outcome": outcome.model_dump(mode="json")}
        revision = hashlib.sha256(json.dumps(plain, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        batch = TrajectoryBatch(source=SOURCE, conversation_id=source_task, batch_id=task_id, revision=revision)
        return AdapterRecord(**content, outcome=outcome, batch=batch)
