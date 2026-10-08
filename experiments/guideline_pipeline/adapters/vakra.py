"""VAKRA benchmark output: one record per dialogue, from per-domain JSON plus the run's results.json.

A run directory holds one ``<domain>.json`` per domain, a list of records
``{"uuid", "domain", "status", "model_input": {"messages", "tools"}, "trajectory":
[{"type": "HumanMessage" | "AIMessage" | "ToolMessage", ...}], "output": [{"turn_id",
"query", "answer", ...}]}``, an ignored ``<domain>_tools.json`` per domain, and
optionally the evaluator's ``results.json``, ``{"domains": {<domain>: {"dialogues":
[{"uuid", "score", "details"}]}}}``.

Each domain is split by position, never shuffled: the first records are train and
the rest are held out. Only one split is yielded (train by default), so held-out
queries never reach ``mine``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from altk_evolve.processing import TrajectoryBatch, TrajectoryOutcome

from experiments.guideline_pipeline.adapters.base import AdapterRecord
from experiments.guideline_pipeline.adapters.elision import elide_middle

SOURCE = "vakra"
STEP_CHARS = 2000  # the extractor truncates each step to 2000 characters, so longer text is chunked
DETAIL_CHARS = 2000
CHECK_CHARS = 500
DEFAULT_TRAIN_N = 7
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

    Options: train_n (the first N records of each domain are train; default 7) or
    dev_frac (the last round(n * dev_frac) records are held out), split (train or
    heldout), domains (comma-separated file stems), include_tools (boolean; default
    true), results (a results.json elsewhere) and heldout_out (write the held-out
    manifest to this file when reading starts).
    """

    name: str = "vakra"
    train_n: int | None = None
    dev_frac: float | None = None
    split: str = "train"
    domains: frozenset[str] = field(default_factory=frozenset)
    include_tools: bool = True
    results: Path | None = None
    heldout_out: Path | None = None

    def __post_init__(self) -> None:
        if self.train_n is not None and self.dev_frac is not None:
            raise ValueError("vakra options train_n and dev_frac are mutually exclusive")
        if self.train_n is not None and self.train_n < 0:
            raise ValueError(f"vakra option train_n must be >= 0, not {self.train_n}")
        if self.dev_frac is not None and not 0.0 <= self.dev_frac < 1.0:
            raise ValueError(f"vakra option dev_frac must be in [0.0, 1.0), not {self.dev_frac}")
        if self.split not in SPLITS:
            raise ValueError(f"vakra option split must be train or heldout, not {self.split!r}")

    def configure(self, options: Mapping[str, str]) -> VakraAdapter:
        changes: dict[str, Any] = {}
        for key, value in options.items():
            if key == "train_n":
                changes[key] = _number(key, value, int)
            elif key == "dev_frac":
                changes[key] = _number(key, value, float)
            elif key == "split":
                changes[key] = value.strip()
            elif key == "domains":
                changes[key] = frozenset(item.strip() for item in value.split(",") if item.strip())
            elif key == "include_tools":
                changes[key] = _boolean(key, value)
            elif key in ("results", "heldout_out"):
                changes[key] = Path(value) if value else None
            else:
                raise ValueError(
                    f"Unknown vakra option {key!r} (expected train_n, dev_frac, split, domains, include_tools, results, heldout_out)"
                )
        return replace(self, **changes)

    def split_records(self, records: list[dict]) -> tuple[list[dict], list[dict]]:
        """(train, heldout) for one domain's records, in file order.

        With dev_frac the last round(n * dev_frac) records are held out, so a
        single-record domain keeps its record in train; otherwise the first
        train_n records are train.
        """
        if self.dev_frac is not None:
            n_train = len(records) - round(len(records) * self.dev_frac)
        else:
            n_train = DEFAULT_TRAIN_N if self.train_n is None else self.train_n
        return records[:n_train], records[n_train:]

    def records(self, path: Path) -> Iterator[AdapterRecord]:
        files = self._files(path)
        results = self._results(path)
        if self.heldout_out is not None:
            self.heldout_out.write_text(json.dumps(self.heldout_manifest(path), indent=2) + "\n")
        for domain, train, heldout in _domains(files, self):
            for raw in train if self.split == "train" else heldout:
                yield self.record(raw, domain, self.split, results.get(str(raw.get("uuid") or "")))

    def heldout_manifest(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        """The held-out records of every selected domain, ``{domain: [{uuid, query, answer, score}]}``.

        Exactly the complement of the train split under the same options, in file order.
        """
        results = self._results(path)
        return {
            domain: [
                {
                    "uuid": raw.get("uuid", ""),
                    "query": _first_query(raw),
                    "answer": (raw.get("output") or [{}])[0].get("answer") if raw.get("output") else "",
                    "score": (results.get(str(raw.get("uuid") or "")) or {}).get("score"),
                }
                for raw in heldout
            ]
            for domain, _, heldout in _domains(self._files(path), self)
        }

    def record(self, raw: dict, domain: str, split: str, dialogue: dict | None) -> AdapterRecord:
        """Build one record from a domain file's record and its results.json dialogue (if any)."""
        uuid = raw.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            raise ValueError(f"A {domain} record has no uuid")
        score = dialogue.get("score") if dialogue else None
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
        trajectory = [message for message in raw.get("trajectory") or [] if isinstance(message, dict)]
        steps = _tool_space(raw, trajectory) if self.include_tools else []
        calls = 0
        first_human_seen = False
        for message in trajectory:
            kind = message.get("type")
            if kind == "HumanMessage":
                if not first_human_seen:
                    first_human_seen = True  # already the user message
                    continue
                steps += _chunk("USER MESSAGE", _text(message.get("content")))
            elif kind == "AIMessage":
                steps += _chunk("AGENT REASONING", _text(message.get("reasoning")))
                for call in message.get("tool_calls") or []:
                    steps.append(_action(calls, call.get("name") or "", call.get("args")))
                    calls += 1
                steps += _chunk("AGENT MESSAGE (answer)", _text(message.get("content")))
            elif kind == "ToolMessage":
                steps += _chunk("OBSERVATION", _text(message.get("result") or message.get("content")))
        return [{"role": "user", "content": _first_query(raw)}, *elide_middle(steps)]

    def _files(self, path: Path) -> list[Path]:
        files = _domain_files(path)
        if self.domains:
            if missing := sorted(self.domains - {file.stem for file in files}):
                raise ValueError(f"Domains not found in {path}: {missing}")
            files = [file for file in files if file.stem in self.domains]
        return files

    def _results(self, path: Path) -> dict[str, dict]:
        """uuid -> dialogue evaluation; {} when the run has no results.json and none was given."""
        results_path = self.results or path / "results.json"
        if self.results is None and not results_path.exists():
            return {}
        payload = _read_json(results_path)
        if not isinstance(payload, dict):
            raise ValueError(f"{results_path} is not a JSON object")
        index: dict[str, dict] = {}
        for domain in (payload.get("domains") or {}).values():
            for dialogue in (domain or {}).get("dialogues") or []:
                if isinstance(dialogue, dict) and dialogue.get("uuid"):
                    index[str(dialogue["uuid"])] = dialogue
        return index


def _domains(files: list[Path], adapter: VakraAdapter) -> Iterator[tuple[str, list[dict], list[dict]]]:
    """(domain, train, heldout) per domain file, reading one file at a time."""
    for file in files:
        records = _read_json(file)
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise ValueError(f"{file} is not a JSON list of records")
        domain = str((records[0].get("domain") if records else None) or file.stem)
        train, heldout = adapter.split_records(records)
        yield domain, train, heldout


def _domain_files(path: Path) -> list[Path]:
    """The ``<domain>.json`` record files: not the ``*_tools.json`` catalogs, not results.json."""
    files = (
        sorted(file for file in path.glob("*.json") if not file.stem.endswith("_tools") and file.stem != "results") if path.is_dir() else []
    )
    if not files:
        raise ValueError(f"No VAKRA domain files in {path}")
    return files


def _first_query(raw: dict) -> str:
    """The task: the first non-empty HumanMessage, else the first turn's query."""
    for message in raw.get("trajectory") or []:
        if isinstance(message, dict) and message.get("type") == "HumanMessage" and _text(message.get("content")):
            return _text(message.get("content"))
    output = raw.get("output") or []
    if output and isinstance(output[0], dict):
        return _text(output[0].get("query"))
    return ""


def _tool_space(raw: dict, trajectory: list[dict]) -> list[dict[str, Any]]:
    """The tools the agent chose and the names of all it was offered (names only: the catalog is large)."""
    tools = (raw.get("model_input") or {}).get("tools") or []
    used: list[str] = []
    for message in trajectory:
        if message.get("type") == "AIMessage":
            for call in message.get("tool_calls") or []:
                if call.get("name") and call["name"] not in used:
                    used.append(call["name"])
    names = [tool["name"] for tool in tools if isinstance(tool, dict) and tool.get("name")]
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
    """success is the dialogue score >= 1 (the record's status is execution status only); no score, no outcome."""
    score = (dialogue or {}).get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return None
    details = (dialogue or {}).get("details")
    details = details if isinstance(details, dict) else {}
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
    for turn in details.get("per_turn") or []:
        if not isinstance(turn, dict):
            continue
        metadata = turn.get("metadata") or {}
        explanations = metadata.get("score_explanation") or {}
        for check, key in _CHECKS:
            if _is_zero(metadata.get(key)):
                label = f"{check} (turn {turn.get('turn_id')})"
                explanation = " ".join(_text(explanations.get(check)).split())
                checks.append(f"{label}: {explanation[:CHECK_CHARS]}" if explanation else label)
    return tuple(dict.fromkeys(checks))


def _evaluation_report(score: float, details: dict) -> str | None:
    """The dialogue score and each turn's sub-scores and explanations; None without per-turn details."""
    turns = [turn for turn in details.get("per_turn") or [] if isinstance(turn, dict)]
    if not turns:
        return None
    lines = [f"dialogue_score={score} num_turns={details.get('num_turns')}"]
    for turn in turns:
        metadata = turn.get("metadata") or {}
        explanations = metadata.get("score_explanation") or {}
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


def _is_zero(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _read_json(path: Path) -> Any:
    with path.open() as handle:
        return json.load(handle)


def _plain(content: dict) -> dict:
    outcome = content["outcome"]
    return {**content, "outcome": None if outcome is None else outcome.model_dump(mode="json")}


def _number(key: str, value: str, kind: type[int] | type[float]) -> int | float:
    try:
        return kind(value.strip())
    except ValueError:
        raise ValueError(f"vakra option {key} must be a number, not {value!r}") from None


def _boolean(key: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"vakra option {key} must be true or false, not {value!r}")
