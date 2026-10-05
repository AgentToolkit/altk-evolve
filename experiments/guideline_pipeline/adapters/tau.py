"""τ-bench retail: one human-readable ``task_*.txt`` trajectory per task.

Each file is a run of sections separated by ``=====`` rules (five or more), each
named by its first line and optionally underlined with ``----``::

    TASK <n>  reward=<r>  breakdown={...}  [termination_reason=... model=... ...]
    USER SCENARIO                      the task the simulated user wanted done
    GROUND-TRUTH EVALUATION CRITERIA   the expected tool calls
    AGENT DOMAIN POLICY                the policy handed to the agent
    CONVERSATION                       [ASSISTANT] / [USER] turns, and tool I/O as
                                       ``-> CALL name(...)`` + ``args: {...}`` and
                                       ``<- RESULT ...`` + the result body
    FINAL ANSWER
    JUDGE / REWARD DETAILS             per-action matches and assertion results

The TASK header, USER SCENARIO and a non-empty CONVERSATION are required; a file
without them is skipped with a warning (see ``TauRetailAdapter.records``).

In the judge section, a check is recognised by tau2's result field names, one
check per line: ``action_match=False`` marks an expected action the agent did not
perform, and ``met=False`` an NL assertion (or communicate check) that failed.
The criteria and final answer sections are not used: the outcome carries the
verdict, and the final answer is the conversation's last turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord

logger = logging.getLogger(__name__)

SOURCE = "tau-retail"
INCLUDE_POLICY_ENV = "TAU_RETAIL_INCLUDE_POLICY"
# Part of every batch revision: bump it when the records built from an unchanged file change.
FORMAT_VERSION = "1"
SUCCESS_REWARD = 1.0  # τ-bench rewards are 0 or 1; anything below full reward is a failure
DETAIL_CHARS = 2000
# render_supporting_context truncates each JSON-rendered context message at 2000 chars;
# leave room for the JSON envelope and escaped newlines.
POLICY_CHUNK_CHARS = 1500

# Temporary, until parse_openai_agents_trajectory's limits are configurable: it keeps
# only the first 50 agent steps and truncates each step to 2000 chars.
PARSER_STEP_LIMIT = 50
PARSER_STEP_CHARS = 2000

_RULE = re.compile(r"^={5,}\s*$", re.M)
_SECTIONS = (
    ("TASK ", "HEADER"),
    ("USER SCENARIO", "SCENARIO"),
    ("GROUND-TRUTH EVALUATION", "CRITERIA"),
    ("AGENT DOMAIN POLICY", "POLICY"),
    ("CONVERSATION", "CONVERSATION"),
    ("FINAL ANSWER", "FINAL"),
    ("JUDGE", "JUDGE"),
)
_HEADER_KEYS = ("termination_reason", "duration", "messages", "agent_cost", "user_cost", "sim_id", "trial", "seed", "model")
_SPEAKER = re.compile(r"^\[(ASSISTANT|USER)\](?:\s*@\S+)?\s*(.*)$")
_CALL = re.compile(r"^\s*->\s*CALL\s+([A-Za-z0-9_]+)\s*(\(.*)$")
_RESULT = re.compile(r"^\s*<-\s*RESULT\b\s*(.*)$")
_ACTION_MATCH = re.compile(r"(?<!\w)action_match\s*[=:]\s*(true|false)\b,?", re.I)
_MET = re.compile(r"(?<!\w)met\s*[=:]\s*(true|false)\b,?", re.I)


class MalformedTrajectory(ValueError):
    """A task file that lacks a section the adapter needs."""


@dataclass(frozen=True)
class Turn:
    kind: Literal["agent", "user", "call", "result"]
    text: str
    name: str = ""  # the tool name, for calls


def split_sections(text: str) -> dict[str, str]:
    """Map each recognised section name to its block, title line included."""
    sections: dict[str, str] = {}
    for block in _RULE.split(text):
        first = next((line.strip() for line in block.splitlines() if line.strip()), "")
        for prefix, name in _SECTIONS:
            if first.startswith(prefix):
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


def elide_head_tail(steps: list[dict[str, Any]], limit: int = PARSER_STEP_LIMIT) -> list[dict[str, Any]]:
    """Keep the first and last steps around a marker, so a conversation's ending survives the step cap.

    Temporary: parse_openai_agents_trajectory keeps only the first 50 steps. Remove
    this once that limit is configurable.
    """
    if len(steps) <= limit:
        return steps
    head = limit // 2
    tail = limit - head - 1  # one step for the marker
    elided = len(steps) - head - tail
    return [*steps[:head], _reasoning(f"... ({elided} conversation steps elided) ..."), *steps[-tail:]]


def _check_text(line: str, flag: re.Pattern[str]) -> str:
    return " ".join(flag.sub("", line).split()).strip(" ,;-")[:300] or line.strip()


def judge_outcome(reward: float, judge: str) -> TrajectoryOutcome:
    """success from the reward; failed checks and the start of the report from the judge section."""
    failed: list[str] = []
    for line in judge.splitlines():
        if (match := _ACTION_MATCH.search(line)) and match.group(1).lower() == "false":
            failed.append(f"expected action not matched: {_check_text(line, _ACTION_MATCH)}")
        elif (match := _MET.search(line)) and match.group(1).lower() == "false":
            failed.append(f"assertion not met: {_check_text(line, _MET)}")
    return TrajectoryOutcome(success=reward >= SUCCESS_REWARD, failed_checks=tuple(failed), detail=judge[:DETAIL_CHARS] or None)


def _policy_context(policy: str) -> list[dict[str, Any]]:
    """The domain policy as system context: never the task instruction, never counted as new material."""
    pieces = _chunks(policy, POLICY_CHUNK_CHARS)
    label = "AGENT DOMAIN POLICY"
    if len(pieces) == 1:
        return [{"role": "system", "content": f"{label}:\n{policy}"}]
    return [{"role": "system", "content": f"{label} (part {i}/{len(pieces)}):\n{piece}"} for i, piece in enumerate(pieces, 1)]


def _task_number(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)$", path.stem)
    return (int(match.group(1)) if match else -1, path.name)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class TauRetailAdapter:
    """Reads a directory of ``task_*.txt`` files, or one such file, into one record per task.

    include_policy adds the agent domain policy as context_messages. None reads
    TAU_RETAIL_INCLUDE_POLICY from the environment, which is how the CLI opts in.
    """

    name = SOURCE

    def __init__(self, *, include_policy: bool | None = None):
        self.include_policy = include_policy

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        """Yield one record per task file, reading each file only when its record is requested.

        A malformed file is logged and skipped, so one bad task doesn't end the run
        (an exception here would stop mine's iteration); a missing or empty input
        raises, because then nothing can be read at all.
        """
        include_policy = _env_flag(INCLUDE_POLICY_ENV) if self.include_policy is None else self.include_policy
        if path.is_file():
            files = [path]
        elif path.is_dir():
            files = sorted(path.glob("task_*.txt"), key=_task_number)
            if not files:
                raise FileNotFoundError(f"no task_*.txt files in {path}")
        else:
            raise FileNotFoundError(f"no such file or directory: {path}")
        for file in files:
            try:
                yield self.record(file, include_policy=include_policy)
            except MalformedTrajectory as exc:
                logger.warning("skipping %s: %s", file, exc)

    def record(self, file: Path, *, include_policy: bool = False) -> AdapterRecord:
        """Build the record for one task file; raises MalformedTrajectory if a required section is missing."""
        raw = file.read_bytes()
        sections = split_sections(raw.decode("utf-8", errors="replace"))
        missing = [name for name in ("HEADER", "SCENARIO", "CONVERSATION") if name not in sections]
        if missing:
            raise MalformedTrajectory(f"missing section(s): {', '.join(missing)}")
        header = parse_header(sections["HEADER"])
        if "reward" not in header:
            raise MalformedTrajectory("TASK header has no reward")
        scenario = section_body(sections["SCENARIO"])
        turns = parse_conversation(sections["CONVERSATION"])
        if not scenario or not turns:
            raise MalformedTrajectory("empty user scenario" if not scenario else "conversation has no turns")
        policy = section_body(sections.get("POLICY", "")) if include_policy else ""

        task_id = file.stem
        revision_input = b"\0".join([FORMAT_VERSION.encode(), b"policy" if policy else b"", raw])
        return AdapterRecord(
            messages=[{"role": "user", "content": scenario}, *elide_head_tail(conversation_steps(turns))],
            context_messages=_policy_context(policy) if policy else [],
            trace_id=task_id,
            model=header.get("model"),
            metadata={key: value for key, value in header.items() if key != "model"},
            batch=TrajectoryBatch(
                source=SOURCE, conversation_id=task_id, batch_id=task_id, revision=hashlib.sha256(revision_input).hexdigest()[:16]
            ),
            outcome=judge_outcome(header["reward"], section_body(sections.get("JUDGE", ""))),
        )
