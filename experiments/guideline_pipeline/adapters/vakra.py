"""VAKRA benchmark output: one record per dialogue, from per-domain JSON plus the run's results.json.

A run directory holds one ``<domain>.json`` per domain, a list of records
``{"uuid", "domain", "status", "model_input": {"messages", "tools"}, "trajectory":
[{"type": "HumanMessage" | "AIMessage" | "ToolMessage", ...}], "output": [{"turn_id",
"query", "answer", "sequence": {"tool_call": [{"name", "arguments"}]}}]}``, an ignored
``<domain>_tools.json`` per domain, and optionally the evaluator's ``results.json``,
``{"domains": {<domain>: {"dialogues": [{"uuid", "score", "details"}]}}}``.

VAKRA's current runner writes no ``model_input`` or ``trajectory`` (only ``output``),
so a record without agent messages is rebuilt from its output turns' tool calls and
answers, and each domain file reports how many records that applies to.

Each record is assigned to train or held-out by a hash of its uuid, so its side
does not depend on file order or on which other records a run holds. Only one
split is yielded (train by default), so held-out queries never reach ``mine``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord
from experiments.guideline_pipeline.adapters.elision import elide_middle

logger = logging.getLogger(__name__)

SOURCE = "vakra"
STEP_CHARS = 2000  # the extractor truncates each step to 2000 characters, so longer text is chunked
DETAIL_CHARS = 2000
CHECK_CHARS = 500
DEFAULT_DEV_FRAC = 0.3
SPLITS = ("train", "heldout")
# Each turn's sub-checks: the name used in score_explanation, and the score key.
_CHECKS = (
    ("policy", "policy_adherance_score"),
    ("exactmatch", "exactmatch_score"),
    ("answer", "answer_score"),
    ("groundedness", "groundedness_score"),
)


@dataclass
class VakraAdapter:
    """Yields one AdapterRecord per VAKRA dialogue in one split. Configure with ``--adapter-option KEY=VALUE``.

    Options: dev_frac (the fraction of records held out, by uuid hash; default 0.3),
    split (train or heldout), domains (comma-separated file stems), include_tools
    (boolean; default true), results (a results.json elsewhere) and heldout_out
    (write the held-out manifest to this file when reading starts).
    """

    name: str = "vakra"
    dev_frac: float = DEFAULT_DEV_FRAC
    split: str = "train"
    domains: frozenset[str] = field(default_factory=frozenset)
    include_tools: bool = True
    results: Path | None = None
    heldout_out: Path | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.dev_frac < 1.0:
            raise ValueError(f"vakra option dev_frac must be in [0.0, 1.0), not {self.dev_frac}")
        if self.split not in SPLITS:
            raise ValueError(f"vakra option split must be train or heldout, not {self.split!r}")

    def configure(self, options: Mapping[str, str]) -> VakraAdapter:
        changes: dict[str, Any] = {}
        for key, value in options.items():
            if key == "dev_frac":
                changes[key] = _number(key, value)
            elif key == "split":
                changes[key] = value.strip()
            elif key == "domains":
                changes[key] = frozenset(_names(key, value))
            elif key == "include_tools":
                changes[key] = _boolean(key, value)
            elif key in ("results", "heldout_out"):
                if not value.strip():
                    raise ValueError(f"vakra option {key} names no file")
                changes[key] = Path(value)
            else:
                raise ValueError(f"Unknown vakra option {key!r} (expected dev_frac, split, domains, include_tools, results, heldout_out)")
        return replace(self, **changes)

    def is_heldout(self, uuid: str) -> bool:
        """Whether the record with this uuid is held out: the first 8 bytes of sha256(uuid), as a fraction of 2**64, are below dev_frac.

        Deterministic and independent of file order or of the other records, so
        inserting, removing or reordering records never moves one across the split.
        """
        return int.from_bytes(hashlib.sha256(uuid.encode()).digest()[:8], "big") < self.dev_frac * 2**64

    def split_records(self, records: list[dict]) -> tuple[list[dict], list[dict]]:
        """(train, heldout) for records that each have a uuid, in file order."""
        train = [raw for raw in records if not self.is_heldout(raw["uuid"])]
        heldout = [raw for raw in records if self.is_heldout(raw["uuid"])]
        return train, heldout

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        files = self._files(path)
        results = self._results(path)
        if self.heldout_out is not None:
            self.heldout_out.write_text(json.dumps(self.heldout_manifest(path), indent=2) + "\n")
        read = 0
        for file in files:
            train, heldout = self.split_records(_read_records(file))
            # On stderr, so a dry run shows a domain that holds out nothing (or keeps nothing to mine).
            logger.warning("%s: %d train, %d held out (dev_frac=%s)", file.name, len(train), len(heldout), self.dev_frac)
            selected = train if self.split == "train" else heldout
            _report_steps(file, self.split, [raw for raw in selected if _first_query(raw)])
            for raw in selected:
                if not _first_query(raw):
                    logger.warning("skipped %s/%s: no task (no HumanMessage text and no output query)", file.name, raw["uuid"])
                    continue
                read += 1
                yield self.record(raw, _domain(raw, file), self.split, results.get(raw["uuid"]))
        if not read:
            logger.warning("read no VAKRA %s record from %s", self.split, path)

    def heldout_manifest(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        """The held-out records of every selected domain, ``{domain: [{uuid, query, answer, score}]}``.

        Exactly the complement of the train split under the same options, in file
        order; records of one domain spread over several files share its list. A
        record with no task is left out, as records() skips it. A held-out query
        that is also a train query is reported, since it is not really unseen.
        """
        results = self._results(path)
        manifest: dict[str, list[dict[str, Any]]] = {}
        train_queries: set[str] = set()
        for file in self._files(path):
            records = _read_records(file)
            train, heldout = self.split_records(records)
            for raw in records:
                manifest.setdefault(_domain(raw, file), [])
            train_queries.update(_query_key(_first_query(raw)) for raw in train)
            for raw in heldout:
                if query := _first_query(raw):
                    answer = _first_turn(raw).get("answer")
                    score = _score((results.get(raw["uuid"]) or {}).get("score"))
                    manifest[_domain(raw, file)].append(
                        {"uuid": raw["uuid"], "query": query, "answer": "" if answer is None else answer, "score": score}
                    )
        if repeated := [item["uuid"] for items in manifest.values() for item in items if _query_key(item["query"]) in train_queries]:
            logger.warning("%d held-out record(s) repeat a train query: %s", len(repeated), ", ".join(repeated))
        return manifest

    def record(self, raw: dict, domain: str, split: str, dialogue: dict | None) -> AdapterRecord:
        """Build one record from a domain file's record and its results.json dialogue (if any)."""
        uuid = raw.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise ValueError(f"A {domain} record has no uuid")
        raw_score = (dialogue or {}).get("score")
        if (score := _score(raw_score)) is None and raw_score is not None:
            logger.warning("ignored the score of %s: %r is not a finite number", uuid, raw_score)
        metadata = {"domain": domain, "split": split, "score": score}
        content: dict[str, Any] = {
            "messages": self._messages(raw),
            "trace_id": uuid,
            "metadata": {key: value for key, value in metadata.items() if value is not None},
            "outcome": _outcome(dialogue),
        }
        # The revision is a digest of exactly what processors see, so a corrected
        # record, results entry or option change is reprocessed; nothing else is.
        digest = hashlib.sha256(json.dumps(_plain(content), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        batch = TrajectoryBatch(source=SOURCE, conversation_id=uuid, batch_id=uuid, revision=digest)
        return AdapterRecord(**content, batch=batch)

    def _messages(self, raw: dict) -> list[dict[str, Any]]:
        """The task as the user message, then one assistant step per reasoning, call, observation or answer.

        role "tool" would be dropped by the extractor, so observations are assistant messages.
        """
        steps, used = _agent_steps(raw)
        if self.include_tools:
            steps = _tool_space(raw, used) + steps
        return [{"role": "user", "content": _first_query(raw)}, *elide_middle(steps)]

    def _files(self, path: Path) -> list[Path]:
        files = _domain_files(path)
        if self.domains:
            if missing := sorted(self.domains - {file.stem for file in files}):
                raise ValueError(f"Domains not found in {path}: {missing}")
            files = [file for file in files if file.stem in self.domains]
        return files

    def _results(self, path: Path) -> dict[str, dict]:
        """uuid -> dialogue evaluation; {} when the run has no results.json and none was given.

        A file without the evaluator's {"domains": {<domain>: {"dialogues": [...]}}} shape
        is an error naming it: read as empty, every record would silently lose its outcome.
        """
        results_path = self.results or path / "results.json"
        if self.results is None and not results_path.exists():
            return {}
        payload = _read_json(results_path)
        domains = payload.get("domains") if isinstance(payload, dict) else None
        if not isinstance(domains, dict):
            raise ValueError(f'Cannot read {results_path}: expected {{"domains": {{<domain>: {{"dialogues": [...]}}}}}}')
        index: dict[str, dict] = {}
        for name, domain in domains.items():
            dialogues = domain.get("dialogues") if isinstance(domain, dict) else None
            if not isinstance(dialogues, list):
                raise ValueError(f"Cannot read {results_path}: domains.{name}.dialogues is not a list")
            for dialogue in dialogues:
                if isinstance(dialogue, dict) and isinstance(dialogue.get("uuid"), str) and dialogue["uuid"]:
                    index[dialogue["uuid"]] = dialogue
        return index


def _report_steps(file: Path, split: str, records: list[dict]) -> None:
    """Warn about records whose steps come from the output rather than a trajectory, and about records with no steps.

    Neither fails the run, but a dry run must show both: a record with no agent step
    would be mined as its task and score alone.
    """
    if untraced := sum(not _has_trajectory(raw) for raw in records):
        logger.warning(
            "%s: %d of %d %s records have no agent message in a trajectory; their steps are the output's tool calls and answers, without observations",
            file.name,
            untraced,
            len(records),
            split,
        )
    if empty := [raw["uuid"] for raw in records if not _agent_steps(raw)[0]]:
        logger.warning(
            "%s: %d %s record(s) have no agent step, so only the task is mined: %s", file.name, len(empty), split, ", ".join(empty)
        )


def _read_records(file: Path) -> list[dict]:
    """One domain file's records; a ValueError naming the file unless each is an object with a uuid."""
    records = _read_json(file)
    if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
        raise ValueError(f"{file} is not a JSON list of records")
    for index, record in enumerate(records):
        if not (isinstance(record.get("uuid"), str) and record["uuid"]):
            raise ValueError(f"{file}: record {index} has no uuid (a non-empty string)")
    return records


def _domain(raw: dict, file: Path) -> str:
    """The record's domain field, else its file's name."""
    domain = raw.get("domain")
    return domain if isinstance(domain, str) and domain else file.stem


def _query_key(query: str) -> str:
    return " ".join(query.split()).casefold()


def _domain_files(path: Path) -> list[Path]:
    """The ``<domain>.json`` record files of one run directory.

    A parent of run directories has none of its own, so it is an error that names
    the runs beneath it; runs nested inside a run are not read, and get a warning.
    """
    files = _json_files(path)
    nested = sorted(child.name for child in path.iterdir() if child.is_dir() and _json_files(child)) if path.is_dir() else []
    if not files:
        hint = f"; pass one run directory ({', '.join(nested)} hold domain files)" if nested else ""
        raise ValueError(f"No VAKRA domain files in {path}{hint}")
    if nested:
        logger.warning("read %s as one run; not read: %s, which also hold domain files", path, ", ".join(nested))
    return files


def _json_files(path: Path) -> list[Path]:
    """Every JSON file in path except the ``*_tools.json`` catalogs and results.json."""
    if not path.is_dir():
        return []
    return sorted(file for file in path.glob("*.json") if not file.stem.endswith("_tools") and file.stem != "results")


def _first_query(raw: dict) -> str:
    """The task: the first HumanMessage with text, else the first turn's query; "" when neither is text."""
    for message in _list(raw.get("trajectory")):
        if isinstance(message, dict) and message.get("type") == "HumanMessage" and (text := _text(message.get("content"))):
            return text
    query = _first_turn(raw).get("query")
    return query.strip() if isinstance(query, str) else ""


def _first_turn(raw: dict) -> dict:
    output = _list(raw.get("output"))
    return output[0] if output and isinstance(output[0], dict) else {}


def _calls(message: dict) -> list[dict]:
    return [call for call in _list(message.get("tool_calls")) if isinstance(call, dict)]


def _name(call: dict) -> str:
    name = call.get("name")
    return name.strip() if isinstance(name, str) else ""


def _observation(message: dict) -> str:
    """A ToolMessage's result, else its content; a structured result is rendered as JSON, not a Python repr."""
    result = message.get("result")
    if isinstance(result, str):
        if result.strip():
            return result.strip()
    elif result is not None and result != [] and result != {}:
        return json.dumps(result, ensure_ascii=False, default=str)
    return _text(message.get("content"))


def _trajectory(raw: dict) -> list[dict]:
    return [message for message in _list(raw.get("trajectory")) if isinstance(message, dict)]


def _has_trajectory(raw: dict) -> bool:
    """Whether the record's trajectory holds any agent message (VAKRA's current runner writes no trajectory)."""
    return any(message.get("type") in ("AIMessage", "ToolMessage") for message in _trajectory(raw))


def _agent_steps(raw: dict) -> tuple[list[dict[str, Any]], list[str]]:
    """The agent's steps, and the names of the tools it called in order of first use.

    From the trajectory when it holds agent messages. Otherwise from the output turns,
    which is all VAKRA's current runner (``save_results_ground_truth``) writes: each
    turn's ``sequence.tool_call`` calls and its answer, with no reasoning or observations.
    """
    steps: list[dict[str, Any]] = []
    used: list[str] = []
    numbers = itertools.count()  # calls are numbered across the whole record

    def call(name: str, args: Any) -> None:
        steps.append(_action(next(numbers), name, args))
        if name and name not in used:
            used.append(name)

    if _has_trajectory(raw):
        first_human_seen = False
        for message in _trajectory(raw):
            kind = message.get("type")
            if kind == "HumanMessage":
                if not first_human_seen:
                    first_human_seen = True  # already the user message
                    continue
                steps += _chunk("USER MESSAGE", _text(message.get("content")))
            elif kind == "AIMessage":
                steps += _chunk("AGENT REASONING", _text(message.get("reasoning")))
                for tool_call in _calls(message):
                    call(_name(tool_call), tool_call.get("args"))
                steps += _chunk("AGENT MESSAGE (answer)", _text(message.get("content")))
            elif kind == "ToolMessage":
                steps += _chunk("OBSERVATION", _observation(message))
        return steps, used

    for index, turn in enumerate(_list(raw.get("output"))):
        if not isinstance(turn, dict):
            continue
        if index:  # the first turn's query is already the user message
            steps += _chunk("USER MESSAGE", _text(turn.get("query")))
        for tool_call in _list(_dict(turn.get("sequence")).get("tool_call")):
            if isinstance(tool_call, dict):
                call(_name(tool_call), tool_call.get("arguments"))
        steps += _chunk("AGENT MESSAGE (answer)", _answer(turn.get("answer")))
    return steps, used


def _answer(value: Any) -> str:
    """An output turn's answer: a string as is, a structured answer as JSON, nothing for None."""
    if value is None:
        return ""
    return value.strip() if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _tool_space(raw: dict, used: list[str]) -> list[dict[str, Any]]:
    """The tools the agent chose and the names of all it was offered (names only: the catalog is large)."""
    tools = _list(_dict(raw.get("model_input")).get("tools"))
    names = [name for tool in tools if isinstance(tool, dict) and (name := _name(tool))]
    summary = ""
    if used:
        summary += "tools chosen in this trajectory: " + ", ".join(used) + "\n"
    if names:
        summary += "all available tools: " + ", ".join(names)
    return _chunk("TOOL SPACE", summary)


def _chunk(prefix: str, text: str) -> list[dict[str, Any]]:
    """text as one or more reasoning steps of at most STEP_CHARS, so the extractor's truncation drops nothing."""
    text = text.strip()
    if not text:
        return []
    room = STEP_CHARS - len(prefix) - 20  # room for the label and part counter
    if len(text) <= room:
        return [{"role": "assistant", "content": f"{prefix}:\n{text}"}]
    pieces = [text[start : start + room] for start in range(0, len(text), room)]
    return [{"role": "assistant", "content": f"{prefix} (part {i}/{len(pieces)}):\n{piece}"} for i, piece in enumerate(pieces, 1)]


def _action(index: int, name: str, args: Any) -> dict[str, Any]:
    """A tool call becomes an assistant function_call with the real tool name and arguments."""
    arguments = json.dumps(args if args is not None else {}, ensure_ascii=False)
    call = {"type": "function_call", "id": f"call_{index}", "function": {"name": name or "tool_call", "arguments": arguments[:STEP_CHARS]}}
    return {"role": "assistant", "content": [call]}


def _outcome(dialogue: dict | None) -> TrajectoryOutcome | None:
    """success is the dialogue score >= 1 (the record's status is execution status only); no readable score, no outcome."""
    score = _score((dialogue or {}).get("score"))
    if score is None:
        return None
    details = _dict((dialogue or {}).get("details"))
    success = score >= 1.0
    detail = _evaluation_report(score, details)
    return TrajectoryOutcome(
        success=success,
        failed_checks=() if success else _failed_checks(details),
        detail=detail[:DETAIL_CHARS] if detail else None,
    )


def _failed_checks(details: dict) -> tuple[str, ...]:
    """Each turn sub-check that scored 0, with the judge's explanation."""
    checks: list[str] = []
    for turn in _list(details.get("per_turn")):
        if not isinstance(turn, dict):
            continue
        metadata = _dict(turn.get("metadata"))
        explanations = _dict(metadata.get("score_explanation"))
        for check, key in _CHECKS:
            if _score(metadata.get(key)) == 0:
                label = f"{check} (turn {turn.get('turn_id')})"
                explanation = " ".join(_text(explanations.get(check)).split())
                checks.append(f"{label}: {explanation[:CHECK_CHARS]}" if explanation else label)
    return tuple(dict.fromkeys(checks))


def _evaluation_report(score: float, details: dict) -> str | None:
    """The dialogue score and each turn's sub-scores and explanations; None without per-turn details."""
    turns = [turn for turn in _list(details.get("per_turn")) if isinstance(turn, dict)]
    if not turns:
        return None
    lines = [f"dialogue_score={score} num_turns={details.get('num_turns')}"]
    for turn in turns:
        metadata = _dict(turn.get("metadata"))
        explanations = _dict(metadata.get("score_explanation"))
        lines.append(
            f"-- turn {turn.get('turn_id')} score={turn.get('score')} "
            f"gt_steps={metadata.get('gt_steps')} pred_steps={metadata.get('pred_steps')} "
            f"extra_steps={metadata.get('extra_steps')} exactmatch={metadata.get('exactmatch_score')} "
            f"answer={metadata.get('answer_score')} groundedness={metadata.get('groundedness_score')}"
        )
        for check, _ in _CHECKS:
            if explanation := _text(explanations.get(check)):
                lines.append(f"  {check}: {explanation}")
    return "\n".join(lines)


def _score(value: Any) -> float | None:
    """A score that is a finite number; None for anything else.

    JSON allows NaN, and NaN >= 1 is False, so a NaN score would read as a failure; a
    bool or a string is not coerced either.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _text(value: Any) -> str:
    """Message text: a string, or the text parts of a list of content blocks; "" for anything else (never a repr)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = (part if isinstance(part, str) else part.get("text") if isinstance(part, dict) else None for part in value)
        return "\n".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    return ""


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _read_json(path: Path) -> Any:
    """The parsed file; a ValueError naming it if it cannot be read or is not JSON."""
    try:
        with path.open() as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc


def _plain(content: dict) -> dict:
    outcome = content["outcome"]
    return {**content, "outcome": None if outcome is None else outcome.model_dump(mode="json")}


def _number(key: str, value: str) -> float:
    try:
        return float(value.strip())
    except ValueError:
        raise ValueError(f"vakra option {key} must be a number, not {value!r}") from None


def _names(key: str, value: str) -> set[str]:
    """A comma-separated filter option. One that names nothing would mean "no filter", so it is an error."""
    names = {item.strip() for item in value.split(",") if item.strip()}
    if not names:
        raise ValueError(f"vakra option {key} names nothing: {value!r}")
    return names


def _boolean(key: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"vakra option {key} must be true or false, not {value!r}")
