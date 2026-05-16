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

from altk_evolve.llm.outcome_extraction.tool_signals import (
    _classify_error,
    _is_tool_result_at,
)
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


def _last_assistant_index(messages: list[dict]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            return i
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

    # 2) Last-assistant analysis. The "decision to stop" is the final
    #    assistant message; whether the trajectory then ends with that
    #    message OR with a trailing tool result depends on dialect:
    #    - chat-style: assistant message is messages[-1].
    #    - tool-action style (AppWorld): assistant issues a final tool_call
    #      and the trailing message is the tool result (often as role=user).
    last_idx = _last_assistant_index(messages)
    if last_idx is None:
        return observations
    last = messages[last_idx]
    last_text = _content_str(last).strip()

    # Find the first tool result that follows the last assistant, if any.
    trailing_tool_result_idx: int | None = None
    for j in range(last_idx + 1, len(messages)):
        if _is_tool_result_at(messages, j):
            trailing_tool_result_idx = j
            break

    # Mid-task capture: assistant issued tool_calls but no tool result followed.
    # Shape-wise we can't tell success or failure.
    if _has_tool_calls(last) and trailing_tool_result_idx is None:
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

    # 4) Clean terminate, two flavors:
    #    A) chat-style: assistant's substantive reply IS the trailing message
    #       (no user follow-up, no tool_calls). If a real user message follows,
    #       the conversation continued and we should not infer terminate.
    #    B) tool-action style: assistant's final action was a tool_call AND the
    #       trailing tool result did NOT match any error pattern. Common in
    #       AppWorld where tasks complete with a tool call (e.g. send_money)
    #       whose response confirms success. Note: under the dual convention,
    #       a trailing role=user that's a tool result is fine here — that's
    #       exactly the AppWorld pattern.
    # A "real" user follow-up means the user kept talking after the assistant —
    # NOT a tool result (which is dialect-dependent; see _is_tool_result_at).
    has_real_user_followup = any(
        messages[j].get("role") == "user" and not _is_tool_result_at(messages, j) for j in range(last_idx + 1, len(messages))
    )
    flavor_a = not _has_tool_calls(last) and len(last_text) >= _SUBSTANTIVE_CONTENT_MIN_CHARS and not has_real_user_followup
    flavor_b = (
        _has_tool_calls(last)
        and trailing_tool_result_idx is not None
        and _classify_error(_content_str(messages[trailing_tool_result_idx])) is None
    )
    if flavor_a or flavor_b:
        detail = (
            "trajectory ended on substantive assistant message (clean terminate)"
            if flavor_a
            else "trajectory ended on successful tool action (clean terminate)"
        )
        observations.append(
            OutcomeObservation(
                trajectory_id=trajectory_id,
                signal_source=SignalSource.TRAJECTORY_SHAPE,
                observed_outcome=OutcomeKind.SUCCESS,
                confidence=_CONF_TERMINATE,
                observed_at=observed_at,
                detail=detail,
            )
        )

    return observations


# Convenience type alias mirroring tool_signals.
ExtractorInput = list[dict[str, Any]]
