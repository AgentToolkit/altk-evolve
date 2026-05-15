"""Trajectory-shape outcome signal extractor (Phase 2).

Detects coarse-grained success/failure signals from the SHAPE of a
trajectory rather than the contents of individual tool results. Pure
pattern matching — no LLM calls, runs in microseconds per trajectory.

Complements `tool_signals.py`:
- tool_signals: per-tool-call errors, exceptions, retries.
- trajectory_shape: end-of-conversation patterns (clean terminate,
  max-iter exhaustion, early abort).

What this extractor catches:
- **Clean terminate**: last assistant message has substantive content,
  no tool_calls in flight, no error indicators in the trailing message.
  → SUCCESS @ 0.65 (medium confidence — we can't be sure the user got
  what they wanted, only that the agent reached a natural stopping point).
- **Max-iter exhaustion**: any message contains a max-iteration / context-
  limit / "took too long" indicator. → FAILURE @ 0.7.
- **Early abort**: assistant explicitly says it cannot complete the task
  (e.g. "I cannot help with this", "I don't have access to...").
  → FAILURE @ 0.65.

What this extractor does NOT catch:
- Tool-level errors (see `tool_signals.py`).
- User-reply patterns ("no, do X instead") — see `reply_pattern.py` (Phase 2.5).
- LLM-judge outcomes — see `llm_judge.py` (Phase 2.5).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from altk_evolve.schema.outcome_evidence import (
    OutcomeKind,
    OutcomeObservation,
    SignalSource,
)


# Confidence levels.
_CONF_TERMINATE = 0.65
_CONF_MAX_ITER = 0.70
_CONF_EARLY_ABORT = 0.65

# Pattern: anywhere in the trajectory, the agent or system says we hit a hard limit.
_MAX_ITER_RE = re.compile(
    r"\b(?:max(?:imum)?[\s_-]*iter(?:ation)?s?|context[\s_-]*(?:length|limit|window)\s*(?:exceeded|reached)|"
    r"too[\s_-]*many[\s_-]*(?:steps|iterations|turns)|took[\s_-]*too[\s_-]*long|"
    r"step[\s_-]*limit\s*(?:exceeded|reached)|recursion[\s_-]*limit)\b",
    re.IGNORECASE,
)

# Pattern: agent declines to help. Conservative — only fires on explicit refusal.
# Two structures:
#   1. "I (cannot|can't|am unable to) (help|assist|complete|do|access|provide|fulfill|answer|...)"
#   2. "I (don't|do not) have (access|the ability|the capability)" — implicit refusal, no verb-after needed.
_EARLY_ABORT_RE = re.compile(
    r"\bi\s+(?:"
    r"(?:cannot|can[''']t|am\s+unable\s+to)\s+(?:help|assist|complete|do|access|provide|fulfill|answer|perform|give)"
    r"|"
    r"(?:don[''']?t|do\s+not)\s+have\s+(?:access|the\s+ability|the\s+capability)"
    r")\b",
    re.IGNORECASE,
)

# Substantive content threshold — single-token "ok"/"sure" doesn't count as a real terminate.
_SUBSTANTIVE_CONTENT_MIN_CHARS = 30


def _now() -> _dt.datetime:
    return _dt.datetime.now(tz=_dt.timezone.utc)


def _content_str(message: dict) -> str:
    content = message.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
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
        return "\n".join(f"{k}: {v}" for k, v in content.items())
    return str(content)


def _last_assistant_message(messages: list[dict]) -> dict | None:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg
    return None


def _has_tool_calls(message: dict) -> bool:
    calls = message.get("tool_calls")
    return bool(calls)


def extract_trajectory_shape_signals(
    messages: list[dict],
    *,
    trajectory_id: str,
    observed_at: _dt.datetime | None = None,
) -> list[OutcomeObservation]:
    """Mine end-of-conversation patterns from a trajectory.

    Returns:
        Zero or more OutcomeObservation. A trajectory typically yields
        one observation (the dominant terminal pattern); some yield zero
        (when no shape signal is detectable — e.g. mid-task captures).
    """
    if observed_at is None:
        observed_at = _now()

    if not messages:
        return []

    observations: list[OutcomeObservation] = []

    # 1) Max-iter / context-limit signal — scan ALL messages.
    for msg in messages:
        text = _content_str(msg)
        if text and _MAX_ITER_RE.search(text):
            observations.append(
                OutcomeObservation(
                    trajectory_id=trajectory_id,
                    signal_source=SignalSource.TRAJECTORY_SHAPE,
                    observed_outcome=OutcomeKind.FAILURE,
                    confidence=_CONF_MAX_ITER,
                    observed_at=observed_at,
                    detail="max-iteration / context-limit marker in trajectory",
                )
            )
            break  # one max-iter observation is enough

    # 2) Last-assistant analysis — only meaningful if the conversation actually
    #    terminated on an assistant turn (not still mid-tool-call, not the user
    #    speaking last).
    last = _last_assistant_message(messages)
    if last is None:
        return observations
    last_text = _content_str(last).strip()

    # Mid-task capture: the trajectory ends on an assistant tool_call without
    # a subsequent tool result. We can't tell success/failure shape-wise.
    if _has_tool_calls(last) and messages[-1] is last:
        return observations

    # 3) Early abort — assistant says they can't help.
    if _EARLY_ABORT_RE.search(last_text):
        observations.append(
            OutcomeObservation(
                trajectory_id=trajectory_id,
                signal_source=SignalSource.TRAJECTORY_SHAPE,
                observed_outcome=OutcomeKind.FAILURE,
                confidence=_CONF_EARLY_ABORT,
                observed_at=observed_at,
                detail="agent declined / unable to complete task",
            )
        )
        return observations  # don't also emit clean-terminate for the same trajectory

    # 4) Clean terminate — substantive final assistant content, no errors,
    #    no in-flight tool calls. The trajectory ends with the assistant
    #    delivering an answer.
    if messages[-1] is last and not _has_tool_calls(last) and len(last_text) >= _SUBSTANTIVE_CONTENT_MIN_CHARS:
        observations.append(
            OutcomeObservation(
                trajectory_id=trajectory_id,
                signal_source=SignalSource.TRAJECTORY_SHAPE,
                observed_outcome=OutcomeKind.SUCCESS,
                confidence=_CONF_TERMINATE,
                observed_at=observed_at,
                detail="trajectory ended on substantive assistant message (clean terminate)",
            )
        )

    return observations


# Convenience type alias mirroring tool_signals.
ExtractorInput = list[dict[str, Any]]
