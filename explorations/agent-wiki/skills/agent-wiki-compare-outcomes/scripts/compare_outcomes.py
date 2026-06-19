#!/usr/bin/env python3
# mypy: ignore-errors
# Exploration/reference code — not type-checked to the project standard.
"""Compare trajectory outcomes and draft evidence-backed contrastive rules."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "for",
    "from",
    "get",
    "have",
    "i",
    "in",
    "is",
    "it",
    "list",
    "me",
    "my",
    "of",
    "on",
    "or",
    "search",
    "show",
    "the",
    "them",
    "this",
    "to",
    "with",
}


@dataclass
class TraceSummary:
    path: str
    session_id: str
    task_id: str
    task: str
    success: bool | None
    success_source: str
    failures: list[dict[str, Any]]
    judgment: dict[str, Any] = field(default_factory=dict)
    top_tools: Counter[str] = field(default_factory=Counter)
    called_tools: set[str] = field(default_factory=set)
    tool_docs: dict[str, str] = field(default_factory=dict)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True, type=Path)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--out-guidelines-json", type=Path, default=None)
    parser.add_argument(
        "--judge-outcomes",
        choices=["never", "missing", "always"],
        default="never",
        help="Use an LLM to judge trajectory success/failure instead of or in addition to stored outcome.success.",
    )
    parser.add_argument(
        "--judge-include-failures",
        action="store_true",
        help="Include generic failure/evaluation snippets in the LLM judge prompt when present.",
    )
    parser.add_argument("--judge-model", default=os.environ.get("AGENT_WIKI_JUDGE_MODEL", "Azure/gpt-4.1"))
    parser.add_argument(
        "--judge-base-url",
        default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("CODEX_MODEL_PROVIDER_BASE_URL"),
    )
    parser.add_argument("--judgment-cache", type=Path, default=None)
    args = parser.parse_args()

    judgment_cache = load_judgment_cache(args.judgment_cache)
    traces = [summarize_trace(path, args, judgment_cache) for path in iter_input_paths(args.input)]
    save_judgment_cache(args.judgment_cache, judgment_cache)
    groups: dict[str, list[TraceSummary]] = defaultdict(list)
    for trace in traces:
        groups[group_key(trace)].append(trace)

    comparisons = [compare_group(key, rows) for key, rows in sorted(groups.items())]
    comparisons = [item for item in comparisons if item["candidates"] or item["status"] != "single_outcome"]

    output = {
        "schema_version": "1",
        "trace_count": len(traces),
        "group_count": len(groups),
        "comparisons": comparisons,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    args.out_md.write_text(render_markdown(output) + "\n", encoding="utf-8")
    if args.out_guidelines_json:
        args.out_guidelines_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_guidelines_json.write_text(
            json.dumps(render_guideline_payload(output, args.out_json), indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.out_guidelines_json}")
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    return 0


def iter_input_paths(inputs: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for input_path in inputs:
        if input_path.is_dir():
            paths.extend(sorted(input_path.rglob("*.json")))
        elif input_path.is_file():
            paths.append(input_path)
        else:
            raise SystemExit(f"Missing input: {input_path}")
    return paths


def summarize_trace(path: Path, args: argparse.Namespace, judgment_cache: dict[str, Any]) -> TraceSummary:
    data = json.loads(path.read_text(encoding="utf-8"))
    session_id = str(data.get("session_id") or data.get("metadata", {}).get("id") or path.stem)
    task_id = str(data.get("metadata", {}).get("task_id") or session_id)
    messages = data.get("openai_chat_completion", {}).get("messages") or data.get("messages") or []
    task = extract_task_text(messages)
    outcome = data.get("outcome") or {}
    stored_success = outcome.get("success")
    judgment: dict[str, Any] = {}
    success_source = "stored_outcome" if stored_success is not None else "unknown"
    success = stored_success
    if should_judge(args.judge_outcomes, stored_success):
        judgment = judge_outcome(
            path=path,
            session_id=session_id,
            task=task,
            messages=messages,
            top_tools=data.get("stats", {}).get("top_tools") or [],
            failures=outcome.get("failures") or [],
            args=args,
            cache=judgment_cache,
        )
        judged_success = judgment.get("success")
        if isinstance(judged_success, bool):
            success = judged_success
            success_source = "llm_judge"
        else:
            success = None
            success_source = "llm_judge_unusable"

    summary = TraceSummary(
        path=str(path),
        session_id=session_id,
        task_id=task_id,
        task=task,
        success=success,
        success_source=success_source,
        judgment=judgment,
        failures=outcome.get("failures") or [],
    )
    for item in data.get("stats", {}).get("top_tools") or []:
        tool = normalize_tool_name(str(item.get("tool") or ""))
        if tool:
            summary.top_tools[tool] += int(item.get("count") or 0)
            summary.called_tools.add(tool)
    for tool in extract_code_tool_calls(messages):
        summary.called_tools.add(tool)
        summary.top_tools[tool] += 1
    for tool, description in extract_tool_docs(messages).items():
        summary.tool_docs[tool] = description

    api_calls_path = data.get("source", {}).get("api_calls_path")
    if api_calls_path:
        for tool in extract_api_calls(Path(api_calls_path)):
            summary.called_tools.add(tool)
            summary.top_tools[tool] += 1
    return summary


def should_judge(mode: str, stored_success: Any) -> bool:
    if mode == "always":
        return True
    if mode == "missing":
        return stored_success is None
    return False


def load_judgment_cache(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_judgment_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if not path:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")


def judge_outcome(
    path: Path,
    session_id: str,
    task: str,
    messages: list[dict[str, Any]],
    top_tools: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    args: argparse.Namespace,
    cache: dict[str, Any],
) -> dict[str, Any]:
    prompt = build_judge_prompt(
        session_id=session_id,
        task=task,
        messages=messages,
        top_tools=top_tools,
        failures=failures if args.judge_include_failures else [],
    )
    cache_key = hashlib.sha256(
        json.dumps(
            {
                "path": str(path),
                "session_id": session_id,
                "model": args.judge_model,
                "include_failures": args.judge_include_failures,
                "prompt": prompt,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if cache_key in cache:
        return cache[cache_key]

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("LLM judging requires the openai Python package.") from exc

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ETE_LITELLM_API_KEY")
    if not api_key:
        raise SystemExit("LLM judging requires OPENAI_API_KEY or ETE_LITELLM_API_KEY.")
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if args.judge_base_url:
        client_kwargs["base_url"] = args.judge_base_url
    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=args.judge_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Judge whether an agent trajectory appears to have completed the user task. "
                    "Return strict JSON only. Do not assume benchmark-specific internals. "
                    "Use task text, transcript evidence, observed errors, and optional failure snippets."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    try:
        judgment = json.loads(content)
    except json.JSONDecodeError:
        judgment = {"success": None, "confidence": 0, "reasons": ["judge returned invalid JSON"], "raw": content}
    judgment["judge_model"] = args.judge_model
    judgment["judge_included_failure_snippets"] = bool(args.judge_include_failures)
    cache[cache_key] = judgment
    return judgment


def build_judge_prompt(
    session_id: str,
    task: str,
    messages: list[dict[str, Any]],
    top_tools: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> str:
    excerpts = compact_transcript(messages)
    failure_snips = []
    for failure in failures[:3]:
        requirement = str(failure.get("requirement") or "").strip()
        trace = str(failure.get("trace") or "").strip()
        if requirement or trace:
            failure_snips.append({"requirement": requirement, "trace_excerpt": trace[:1200]})
    payload = {
        "session_id": session_id,
        "task": task,
        "observed_top_tools": top_tools[:12],
        "transcript_excerpts": excerpts,
        "failure_snippets": failure_snips,
        "instructions": {
            "success": "true if the trajectory completed the requested task; false if it failed, stopped early, made a likely wrong irreversible action, or external failure snippets show mismatch; null if unclear.",
            "confidence": "0.0 to 1.0",
            "failure_modes": "short list such as wrong-data-source, step-limit, tool-error, missing-complete-task, apparent-success-but-unverifiable",
            "evidence": "quote or paraphrase only transcript-visible or failure-snippet evidence",
        },
        "required_json_shape": {
            "success": "boolean or null",
            "confidence": "number",
            "failure_modes": ["string"],
            "evidence": ["string"],
            "notes": "string",
        },
    }
    return json.dumps(payload, indent=2)


def compact_transcript(messages: list[dict[str, Any]], max_items: int = 14, max_chars: int = 1200) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    if messages:
        selected.extend(messages[:2])
        selected.extend(messages[-max_items:])
    seen: set[tuple[str, str]] = set()
    compact: list[dict[str, str]] = []
    for message in selected:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        key = (role, content[:100])
        if not content or key in seen:
            continue
        seen.add(key)
        compact.append({"role": role, "content": truncate_middle(content, max_chars)})
    return compact[-max_items:]


def truncate_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n[...truncated...]\n" + text[-half:]


def extract_task_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        marker = "Task:"
        if marker in content:
            return content.split(marker, 1)[1].strip().split("\n", 1)[0].strip()
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip().split("\n", 1)[0]
    return ""


def extract_code_tool_calls(messages: list[dict[str, Any]]) -> set[str]:
    tools: set[str] = set()
    pattern = re.compile(r"\bapis\.([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\s*\(")
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for app, api in pattern.findall(content):
            tools.add(f"{app}.{api}")
    return tools


def extract_tool_docs(messages: list[dict[str, Any]]) -> dict[str, str]:
    docs: dict[str, str] = {}
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for name, description in re.findall(
            r'"name"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]+)"',
            content,
        ):
            docs.setdefault(normalize_tool_name(name), description)
        app_name = first_json_string(content, "app_name")
        api_name = first_json_string(content, "api_name")
        api_path = first_json_string(content, "path")
        description = first_json_string(content, "description")
        if app_name and api_name and description:
            docs.setdefault(f"{app_name}.{api_name}", description)
            docs.setdefault(normalize_tool_name(api_name), description)
        if api_path and description:
            docs.setdefault(normalize_url_tool(api_path), description)
    return docs


def first_json_string(text: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', text)
    return match.group(1) if match else None


def extract_api_calls(path: Path) -> list[str]:
    if not path.exists():
        return []
    tools: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = str(item.get("url") or "")
            if is_documentation_lookup(url):
                continue
            data = item.get("data") or {}
            app = data.get("app_name")
            api = data.get("api_name")
            if app and api:
                tools.append(f"{app}.{api}")
                continue
            normalized = normalize_url_tool(url)
            if normalized:
                tools.append(normalized)
    return tools


def is_documentation_lookup(url: str) -> bool:
    parts = [part for part in url.strip("/").split("/") if part]
    return bool(parts and parts[0] in {"api_docs", "docs", "documentation"})


def normalize_url_tool(url: str) -> str:
    parts = [part for part in url.strip("/").split("/") if part]
    if not parts:
        return ""
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return parts[0]


def normalize_tool_name(value: str) -> str:
    value = value.strip()
    if value.startswith("/"):
        return normalize_url_tool(value)
    value = value.strip("/")
    value = value.replace("/", ".")
    value = re.sub(r"\.+", ".", value)
    return value


def group_key(trace: TraceSummary) -> str:
    if trace.task_id:
        return trace.task_id
    words = "-".join(tokenize(trace.task)[:12])
    return words or trace.session_id


def compare_group(key: str, traces: list[TraceSummary]) -> dict[str, Any]:
    successes = [trace for trace in traces if trace.success is True]
    failures = [trace for trace in traces if trace.success is False]
    status = "contrast" if successes and failures else "single_outcome"
    candidates: list[dict[str, Any]] = []
    if successes and failures:
        candidates = derive_candidates(successes, failures)
    return {
        "group": key,
        "task": most_common_task(traces),
        "status": status,
        "successes": [trace.session_id for trace in successes],
        "failures": [trace.session_id for trace in failures],
        "traces": [trace_to_json(trace) for trace in traces],
        "candidates": candidates,
    }


def derive_candidates(successes: list[TraceSummary], failures: list[TraceSummary]) -> list[dict[str, Any]]:
    success_tools = candidate_tools(successes)
    failure_tools = candidate_tools(failures)
    success_only = sorted(success_tools - failure_tools)
    failure_only = sorted(failure_tools - success_tools)
    candidates: list[dict[str, Any]] = []
    for success_tool in success_only:
        success_doc = first_doc(successes, success_tool)
        success_score, success_terms = semantic_alignment(successes[0].task, success_tool, success_doc)
        for failure_tool in failure_only:
            if coarse_family(success_tool) != coarse_family(failure_tool):
                continue
            failure_doc = first_doc(failures, failure_tool)
            failure_score, failure_terms = semantic_alignment(successes[0].task, failure_tool, failure_doc)
            if success_score <= failure_score:
                continue
            candidates.append(
                {
                    "confidence": confidence(success_score, failure_score, successes, failures),
                    "rule_type": "tool-selection",
                    "draft_rule": draft_rule(success_tool, failure_tool, success_terms),
                    "successful_pattern": {
                        "tool": success_tool,
                        "description": success_doc,
                        "task_overlap_terms": success_terms,
                        "sessions": [trace.session_id for trace in successes if success_tool in trace.called_tools],
                    },
                    "failed_pattern": {
                        "tool": failure_tool,
                        "description": failure_doc,
                        "task_overlap_terms": failure_terms,
                        "sessions": [trace.session_id for trace in failures if failure_tool in trace.called_tools],
                    },
                    "evaluator_failure_snippets": failure_snippets(failures),
                }
            )
    candidates.extend(derive_intensity_candidates(successes, failures))
    return dedupe_candidates(sorted(candidates, key=lambda item: item["confidence"], reverse=True))[:5]


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        success = candidate.get("successful_pattern", {})
        failure = candidate.get("failed_pattern", {})
        key = (
            str(candidate.get("rule_type") or ""),
            str(success.get("description") or success.get("tool") or ""),
            str(failure.get("description") or failure.get("tool") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def derive_intensity_candidates(
    successes: list[TraceSummary],
    failures: list[TraceSummary],
) -> list[dict[str, Any]]:
    task = successes[0].task
    tools = sorted(candidate_tools(successes) | candidate_tools(failures))
    success_heavy: list[tuple[str, float, float]] = []
    failure_heavy: list[tuple[str, float, float]] = []
    for tool in tools:
        success_avg = average_tool_count(successes, tool)
        failure_avg = average_tool_count(failures, tool)
        if success_avg >= max(2.0, failure_avg * 1.35):
            success_heavy.append((tool, success_avg, failure_avg))
        elif failure_avg >= max(2.0, success_avg * 1.35):
            failure_heavy.append((tool, success_avg, failure_avg))

    candidates: list[dict[str, Any]] = []
    for success_tool, success_avg, success_fail_avg in success_heavy:
        success_doc = first_doc(successes + failures, success_tool)
        success_score, success_terms = semantic_alignment(task, success_tool, success_doc)
        for failure_tool, failure_success_avg, failure_avg in failure_heavy:
            if coarse_family(success_tool) != coarse_family(failure_tool):
                continue
            failure_doc = first_doc(successes + failures, failure_tool)
            failure_score, failure_terms = semantic_alignment(task, failure_tool, failure_doc)
            failure_extra = distinctive_terms(failure_tool, failure_doc, task)
            success_extra = distinctive_terms(success_tool, success_doc, task)
            if success_score < failure_score and len(failure_extra) <= len(success_extra):
                continue
            candidates.append(
                {
                    "confidence": confidence_from_counts(
                        success_avg,
                        success_fail_avg,
                        failure_success_avg,
                        failure_avg,
                        successes,
                        failures,
                    ),
                    "rule_type": "tool-selection-intensity",
                    "draft_rule": draft_intensity_rule(
                        success_tool,
                        failure_tool,
                        success_terms,
                        failure_extra,
                    ),
                    "successful_pattern": {
                        "tool": success_tool,
                        "description": success_doc,
                        "task_overlap_terms": success_terms,
                        "average_calls_in_successes": round(success_avg, 2),
                        "average_calls_in_failures": round(success_fail_avg, 2),
                        "sessions": [trace.session_id for trace in successes if trace.top_tools.get(success_tool, 0) > 0],
                    },
                    "failed_pattern": {
                        "tool": failure_tool,
                        "description": failure_doc,
                        "task_overlap_terms": failure_terms,
                        "distinctive_non_task_terms": failure_extra[:6],
                        "average_calls_in_successes": round(failure_success_avg, 2),
                        "average_calls_in_failures": round(failure_avg, 2),
                        "sessions": [trace.session_id for trace in failures if trace.top_tools.get(failure_tool, 0) > 0],
                    },
                    "evaluator_failure_snippets": failure_snippets(failures),
                }
            )
    return candidates


def union_tools(traces: list[TraceSummary]) -> set[str]:
    return set().union(*(trace.called_tools for trace in traces))


def candidate_tools(traces: list[TraceSummary]) -> set[str]:
    return {tool for tool in union_tools(traces) if is_candidate_tool(tool)}


def is_candidate_tool(tool: str) -> bool:
    parts = [part for part in tool.split(".") if part]
    if not parts:
        return False
    if parts[0] in {"api_docs", "docs", "documentation"}:
        return False
    if any(part in {"auth", "token", "login", "logout"} for part in parts):
        return False
    if parts[-1] in {"complete_task", "finish", "finalize"}:
        return False
    return True


def average_tool_count(traces: list[TraceSummary], tool: str) -> float:
    if not traces:
        return 0.0
    return sum(trace.top_tools.get(tool, 0) for trace in traces) / len(traces)


def coarse_family(tool: str) -> str:
    parts = tool.split(".")
    return parts[0] if parts else tool


def first_doc(traces: list[TraceSummary], tool: str) -> str:
    short = tool.rsplit(".", 1)[-1]
    for trace in traces:
        if tool in trace.tool_docs:
            return trace.tool_docs[tool]
        if short in trace.tool_docs:
            return trace.tool_docs[short]
    return ""


def semantic_alignment(task: str, tool: str, description: str) -> tuple[int, list[str]]:
    task_terms = set(tokenize(task))
    doc_terms = set(tokenize(f"{tool} {description}"))
    overlap = sorted(task_terms & doc_terms)
    return len(overlap), overlap


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw_token in re.findall(r"[a-z][a-z_]+", text.lower().replace("-", "_")):
        token = {"my": "own", "mine": "own", "your": "own", "user": "own", "users": "own"}.get(raw_token, raw_token)
        if token not in STOPWORDS and len(token) > 2:
            tokens.append(token)
    return tokens


def distinctive_terms(tool: str, description: str, task: str) -> list[str]:
    task_terms = set(tokenize(task))
    return sorted(set(tokenize(f"{tool} {description}")) - task_terms)


def confidence(
    success_score: int,
    failure_score: int,
    successes: list[TraceSummary],
    failures: list[TraceSummary],
) -> float:
    score = 0.45
    score += min(0.25, 0.08 * (success_score - failure_score))
    score += 0.15 if len(successes) >= 1 and len(failures) >= 1 else 0.0
    score += 0.15 if any(trace.failures for trace in failures) else 0.0
    return round(min(score, 0.95), 2)


def draft_rule(success_tool: str, failure_tool: str, terms: list[str]) -> str:
    term_text = ", ".join(terms[:4]) if terms else "the task wording"
    return (
        f"When choosing a data source, prefer `{success_tool}` when its observed documentation "
        f"matches task terms ({term_text}). Use `{failure_tool}` only when its observed "
        "documentation matches the task wording more directly."
    )


def confidence_from_counts(
    success_avg: float,
    success_fail_avg: float,
    failure_success_avg: float,
    failure_avg: float,
    successes: list[TraceSummary],
    failures: list[TraceSummary],
) -> float:
    success_ratio = success_avg / max(success_fail_avg, 1.0)
    failure_ratio = failure_avg / max(failure_success_avg, 1.0)
    score = 0.45 + min(0.2, 0.04 * success_ratio) + min(0.2, 0.04 * failure_ratio)
    score += 0.1 if any(trace.failures for trace in failures) else 0.0
    return round(min(score, 0.92), 2)


def draft_intensity_rule(
    success_tool: str,
    failure_tool: str,
    success_terms: list[str],
    failure_extra: list[str],
) -> str:
    term_text = ", ".join(success_terms[:4]) if success_terms else "the task wording"
    extra_text = ", ".join(failure_extra[:4]) if failure_extra else "different concepts"
    return (
        f"For similar tasks, prefer `{success_tool}` when the task wording matches its observed "
        f"documentation or name ({term_text}). Treat `{failure_tool}` as narrower: its observed "
        f"documentation/name introduces terms not present in the task ({extra_text}), so use it "
        "only when those terms are explicit in the request."
    )


def failure_snippets(failures: list[TraceSummary]) -> list[str]:
    snippets: list[str] = []
    for trace in failures:
        for failure in trace.failures[:2]:
            requirement = str(failure.get("requirement") or "").strip()
            trace_text = str(failure.get("trace") or "").strip()
            snippet = requirement or trace_text
            if snippet:
                snippets.append(snippet[:600])
    return snippets[:4]


def most_common_task(traces: list[TraceSummary]) -> str:
    counter = Counter(trace.task for trace in traces if trace.task)
    return counter.most_common(1)[0][0] if counter else ""


def trace_to_json(trace: TraceSummary) -> dict[str, Any]:
    return {
        "session_id": trace.session_id,
        "path": trace.path,
        "success": trace.success,
        "success_source": trace.success_source,
        "judgment": trace.judgment,
        "called_tools": sorted(trace.called_tools),
        "top_tools": trace.top_tools.most_common(10),
    }


def render_markdown(output: dict[str, Any]) -> str:
    lines = [
        "# Outcome Comparison",
        "",
        f"Traces: {output['trace_count']}",
        f"Groups: {output['group_count']}",
        "",
    ]
    for group in output["comparisons"]:
        lines.extend(
            [
                f"## {group['group']}",
                "",
                f"Task: {group['task']}",
                "",
                f"Successes: {len(group['successes'])}  Failures: {len(group['failures'])}",
                "",
            ]
        )
        if not group["candidates"]:
            lines.extend(["No promotable contrastive candidates found.", ""])
            continue
        for index, candidate in enumerate(group["candidates"], start=1):
            lines.extend(
                [
                    f"### Candidate {index}: {candidate['rule_type']} ({candidate['confidence']})",
                    "",
                    candidate["draft_rule"],
                    "",
                    "Successful pattern:",
                    f"- Tool: `{candidate['successful_pattern']['tool']}`",
                    f"- Description: {candidate['successful_pattern']['description'] or 'n/a'}",
                    f"- Evidence sessions: {', '.join(candidate['successful_pattern']['sessions'])}",
                    "",
                    "Failed pattern:",
                    f"- Tool: `{candidate['failed_pattern']['tool']}`",
                    f"- Description: {candidate['failed_pattern']['description'] or 'n/a'}",
                    f"- Evidence sessions: {', '.join(candidate['failed_pattern']['sessions'])}",
                    "",
                ]
            )
            if candidate["evaluator_failure_snippets"]:
                lines.append("Evaluator failure snippets:")
                for snippet in candidate["evaluator_failure_snippets"]:
                    lines.append(f"- {snippet.replace(chr(10), ' ')[:300]}")
                lines.append("")
    return "\n".join(lines)


def render_guideline_payload(output: dict[str, Any], analysis_path: Path) -> dict[str, Any]:
    entities: list[dict[str, Any]] = []
    for group in output["comparisons"]:
        for candidate in group.get("candidates", []):
            success = candidate.get("successful_pattern", {})
            failure = candidate.get("failed_pattern", {})
            if not success.get("description") or not failure.get("description"):
                continue
            if float(candidate.get("confidence") or 0.0) < 0.8:
                continue
            title = "Choose data source from task wording"
            success_terms = stable_terms(success.get("task_overlap_terms") or [])
            failure_terms = stable_terms(
                failure.get("distinctive_non_task_terms")
                or distinctive_terms(str(failure.get("tool") or ""), str(failure.get("description") or ""), group.get("task") or "")
            )
            content = render_contrastive_rule_content(success, failure, success_terms, failure_terms)
            rationale = (
                "This rule is contrastive: the candidate has at least one successful and one failed "
                "trajectory for the same task group, a measurable tool-use difference, observed "
                "documentation for both tools, and evaluator failures on the failed side."
            )
            trigger = render_contrastive_trigger(success, failure, success_terms, failure_terms)
            entities.append(
                {
                    "type": "guideline",
                    "title": title,
                    "content": content,
                    "rationale": rationale,
                    "trigger": trigger,
                    "session_id": f"compare-outcomes__{group['group']}",
                    "agent": "agent-wiki-compare-outcomes",
                    "tags": ["contrastive", "tool-selection", "data-source-routing"],
                    "normalized_path": str(analysis_path),
                }
            )
    return {"entities": entities}


def stable_terms(terms: list[Any]) -> list[str]:
    output: list[str] = []
    for term in terms:
        value = str(term).strip().replace("_", " ")
        if not value or value in output:
            continue
        output.append(value)
    return output[:6]


def render_contrastive_rule_content(
    success: dict[str, Any],
    failure: dict[str, Any],
    success_terms: list[str],
    failure_terms: list[str],
) -> str:
    success_tool = success["tool"]
    failure_tool = failure["tool"]
    success_description = clean_sentence(success["description"])
    failure_description = clean_sentence(failure["description"])
    success_term_text = ", ".join(success_terms[:4]) if success_terms else "the successful-side terms"
    failure_term_text = ", ".join(failure_terms[:4]) if failure_terms else "the failed-side terms"
    return (
        f"Apply this rule only when the live choice is between `{success_tool}` and `{failure_tool}` "
        "or between APIs with the same documented meanings. Prefer "
        f"`{success_tool}` when the request matches its observed documentation "
        f"({success_description}) and uses successful-side terms such as {success_term_text}. "
        f"Do not apply this rule when the request explicitly uses failed-side terms such as "
        f"{failure_term_text}; in that case inspect `{failure_tool}` because its observed "
        f"documentation was {failure_description}. Do not generalize this rule to other record "
        "families or unrelated APIs unless a separate contrast includes those APIs."
    )


def render_contrastive_trigger(
    success: dict[str, Any],
    failure: dict[str, Any],
    success_terms: list[str],
    failure_terms: list[str],
) -> str:
    success_term_text = ", ".join(success_terms[:4]) if success_terms else "the successful-side documentation"
    failure_term_text = ", ".join(failure_terms[:4]) if failure_terms else "the failed-side documentation"
    return (
        f"Use only when choosing between `{success['tool']}` and `{failure['tool']}` "
        f"and the task wording aligns with {success_term_text}; skip when the task explicitly "
        f"mentions {failure_term_text} or asks about a different record family."
    )


def clean_sentence(text: Any) -> str:
    value = str(text or "").strip()
    return value.rstrip(".")


if __name__ == "__main__":
    raise SystemExit(main())
