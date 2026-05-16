"""Tool-level outcome signal extractor (Phase 2).

Inspects an OpenAI-format trajectory and yields `OutcomeObservation`s
based on tool errors, exceptions, and retry patterns. Highest-coverage
free signal source — no LLM calls, runs in milliseconds per trajectory.

What this extractor catches:
- Tool result messages containing exception markers (Traceback, raise X,
  unhandled exception).
- Tool result messages containing HTTP-style failure status codes (4xx/5xx)
  or explicit `"error"` / `"failed"` / `"denied"` substrings.
- Same-tool retries (call A fails → call A again with similar args) — the
  first call is observed as FAILURE.

What this extractor does NOT catch:
- Trajectory-shape signals (max-iter exhaustion, clean terminate). See
  `trajectory_shape.py`.
- Reply-pattern signals (user said "no, do X instead"). See
  `reply_pattern.py` (Phase 2.5).
- LLM-judged outcomes. See `llm_judge.py` (Phase 2.5).

Confidence policy (mirrors §7.1.1):
- Exception traceback or status-code 5xx: 0.95 (confirmed).
- "error" / "failed" / "denied" substring or 4xx: 0.85 (confirmed).
- Same-tool retry pattern (the failed first call): 0.80 (confirmed,
  threshold edge — we treat it as confirmed).

Each observation reports its `detail` so downstream review can audit.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

from altk_evolve.schema.outcome_evidence import (
    OutcomeKind,
    OutcomeObservation,
    SignalSource,
)


# Regex patterns. Compiled once at module load.
_TRACEBACK_RE = re.compile(
    r"(?:traceback \(most recent call last\)|\bTraceback:|raise [A-Z][A-Za-z]+(?:Error|Exception)|unhandled exception)",
    re.IGNORECASE | re.MULTILINE,
)
# HTTP status patterns require the status name as well — a bare `\b4\d{2}\b`
# false-matches Python traceback line numbers, transaction IDs, etc.
_HTTP_5XX_RE = re.compile(
    r"\b5\d{2}\s+(?:internal[\s_-]*server[\s_-]*error|server[\s_-]*error|service[\s_-]*unavailable|gateway[\s_-]*timeout|bad[\s_-]*gateway)",
    re.IGNORECASE,
)
_HTTP_4XX_RE = re.compile(
    r"\b4\d{2}\s+(?:bad[\s_-]*request|unauthorized|forbidden|not[\s_-]*found|conflict|timeout|too[\s_-]*many[\s_-]*requests|payment[\s_-]*required)",
    re.IGNORECASE,
)
_GENERIC_ERROR_RE = re.compile(r"\b(?:error|failed|failure|denied|exception|timeout|refused)\b", re.IGNORECASE)

# Spec / documentation lookups: the response describes an API rather than the
# result of CALLING it. These outputs contain words like "failure" / "error"
# in describing error responses but are not themselves failures. Heuristic:
# JSON with api_name + (path | method) keys → suppress generic-error matches.
_SPEC_LOOKUP_RE = re.compile(r'"api_name"\s*:\s*"', re.IGNORECASE)
_SPEC_HAS_PATH_OR_METHOD_RE = re.compile(r'"(?:path|method)"\s*:\s*"', re.IGNORECASE)


# Confidence levels per detection class.
_CONF_TRACEBACK = 0.95
_CONF_HTTP_5XX = 0.95
_CONF_HTTP_4XX = 0.85
_CONF_GENERIC = 0.85
_CONF_RETRY = 0.80


def _now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _content_str(message: dict) -> str:
    """Extract a string view of a message's content for pattern matching.

    Messages can have content as str, list[dict] (OpenAI multipart),
    or dict. We coerce everything to a single string so patterns can scan.
    """
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # OpenAI multimodal/multipart content; concatenate `text` fields.
        parts = []
        for item in content:
            if isinstance(item, dict):
                txt = item.get("text") or item.get("content")
                if isinstance(txt, str):
                    parts.append(txt)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, dict):
        # Best-effort flatten.
        return "\n".join(f"{k}: {v}" for k, v in content.items())
    return str(content)


_TOOL_OUTPUT_PREFIX_RE = re.compile(r"^\s*(?:Output|Result|Response|Tool[\s_-]*output)\s*:", re.IGNORECASE)


def _is_tool_result_at(messages: list[dict], idx: int) -> bool:
    """Is messages[idx] a tool-result message?

    Recognizes the three conventions seen in real trajectories:

    - **OpenAI strict:** `role == "tool"` or message carries `tool_call_id`.
    - **Anthropic / OpenAI tool_calls:** `role == "user"` immediately following
      an assistant message with `tool_calls` set. The tool output is stuffed
      into a user message; the prior assistant's `tool_calls` field marks it.
    - **Narrated AppWorld:** `role == "user"` whose content starts with a
      literal "Output:" / "Result:" / "Response:" prefix. AppWorld trajectories
      use this format with the tool-action implicit in the assistant's prose
      rather than a `tool_calls` field.

    The narrated form is heuristic but the prefix is distinctive enough that
    real user prose rarely starts with "Output:" or "Result:".
    """
    if idx < 0 or idx >= len(messages):
        return False
    msg = messages[idx]
    if msg.get("role") == "tool":
        return True
    if msg.get("tool_call_id"):
        return True
    if msg.get("role") == "user":
        if idx > 0:
            prev = messages[idx - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                return True
        content = _content_str(msg)
        if content and _TOOL_OUTPUT_PREFIX_RE.match(content):
            return True
    return False


def _looks_like_spec_lookup(content: str) -> bool:
    """Heuristic: is this the response from an API-spec / documentation lookup
    rather than a real call result? Such responses contain words like 'failure'
    in describing error contracts but are not themselves failures."""
    return bool(_SPEC_LOOKUP_RE.search(content) and _SPEC_HAS_PATH_OR_METHOD_RE.search(content))


def _classify_error(content: str) -> tuple[float, str] | None:
    """Return (confidence, detail_label) for the strongest match, or None.

    Strong signals (Traceback, HTTP status with name) fire regardless of context.
    Generic word-level signals are suppressed when the content looks like an
    API spec / documentation lookup — there `failure` / `error` describe the
    contract, not an actual call outcome.
    """
    if _TRACEBACK_RE.search(content):
        return (_CONF_TRACEBACK, "exception traceback in tool result")
    if _HTTP_5XX_RE.search(content):
        return (_CONF_HTTP_5XX, "HTTP 5xx status in tool result")
    if _HTTP_4XX_RE.search(content):
        return (_CONF_HTTP_4XX, "HTTP 4xx status in tool result")
    if _GENERIC_ERROR_RE.search(content) and not _looks_like_spec_lookup(content):
        return (_CONF_GENERIC, "error/failed/denied substring in tool result")
    return None


def _tool_call_signature(call: dict) -> tuple[str, str]:
    """Return (tool_name, normalized_args) for retry-detection grouping."""
    fn = call.get("function") or {}
    name = fn.get("name") or call.get("name") or ""
    raw_args = fn.get("arguments") or call.get("arguments") or ""
    # First try JSON canonicalization (handles whitespace + key-order differences).
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
            normalized = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, ValueError):
            # Fallback: collapse whitespace runs.
            normalized = "".join(raw_args.split())
    elif isinstance(raw_args, (dict, list)):
        try:
            normalized = json.dumps(raw_args, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            normalized = str(raw_args)
    else:
        normalized = str(raw_args)
    return name, normalized


def _iter_tool_calls(message: dict) -> list[dict]:
    """Return the assistant tool_calls list from a message, or []."""
    calls = message.get("tool_calls") or []
    return [c for c in calls if isinstance(c, dict)]


def extract_tool_signals(
    messages: list[dict],
    *,
    trajectory_id: str,
    observed_at: _dt.datetime | None = None,
) -> list[OutcomeObservation]:
    """Mine tool errors / retries from a trajectory.

    Args:
        messages: OpenAI-format messages from the trajectory.
        trajectory_id: ID of the source trajectory (set on every observation).
        observed_at: timestamp to stamp on each observation. Defaults to now-UTC.

    Returns:
        A list of OutcomeObservation. Empty if the trajectory shows no
        tool errors or retries (caller may then fall through to other
        extractors or emit an UNKNOWN observation).
    """
    if observed_at is None:
        observed_at = _now()

    observations: list[OutcomeObservation] = []

    # Pass 1: error indicators in tool result messages.
    for idx, msg in enumerate(messages):
        if not _is_tool_result_at(messages, idx):
            continue
        text = _content_str(msg)
        if not text:
            continue
        match = _classify_error(text)
        if match is None:
            continue
        confidence, label = match
        observations.append(
            OutcomeObservation(
                trajectory_id=trajectory_id,
                signal_source=SignalSource.TOOL_ERROR,
                observed_outcome=OutcomeKind.FAILURE,
                confidence=confidence,
                observed_at=observed_at,
                detail=label,
            )
        )

    # Pass 2: same-tool retry detection. A retry is one assistant tool_call
    # whose (name, args) signature matches a prior assistant tool_call AND
    # the prior call's adjacent tool_result was a failure (within window).
    seen_signatures: dict[tuple[str, str], int] = {}  # signature → message index of first call
    last_failure_msg_idx: dict[tuple[str, str], int] = {}

    for idx, msg in enumerate(messages):
        if msg.get("role") != "assistant":
            continue
        for call in _iter_tool_calls(msg):
            sig = _tool_call_signature(call)
            if sig in seen_signatures:
                # This is a retry. Confirm the prior result was a failure
                # by scanning forward from the original call to the next
                # tool message.
                prior_idx = seen_signatures[sig]
                prior_failed = _prior_call_failed(messages, prior_idx)
                if prior_failed:
                    observations.append(
                        OutcomeObservation(
                            trajectory_id=trajectory_id,
                            signal_source=SignalSource.TOOL_ERROR,
                            observed_outcome=OutcomeKind.FAILURE,
                            confidence=_CONF_RETRY,
                            observed_at=observed_at,
                            detail=f"same-tool retry pattern: {sig[0]}",
                        )
                    )
                    last_failure_msg_idx[sig] = idx
            else:
                seen_signatures[sig] = idx

    return observations


def _prior_call_failed(messages: list[dict], assistant_idx: int) -> bool:
    """Did the tool result following messages[assistant_idx] match an error pattern?"""
    for j in range(assistant_idx + 1, min(assistant_idx + 4, len(messages))):
        if not _is_tool_result_at(messages, j):
            continue
        text = _content_str(messages[j])
        if text and _classify_error(text) is not None:
            return True
        # Found a tool result that wasn't an error — prior call succeeded.
        return False
    return False


# ── module export shim ─────────────────────────────────────────────────────
# Convenience for callers who want to type the input as Any to avoid noise:
ExtractorInput = list[dict[str, Any]]
