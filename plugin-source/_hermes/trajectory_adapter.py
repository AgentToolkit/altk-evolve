"""Convert hermes conversation messages into the OpenAI-format trajectory
that ALTK-Evolve's ``save_trajectory`` expects.

Evolve consumes a JSON array of ``{"role", "content"}`` objects (it does
``json.loads`` then iterates ``message["content"]``). This module flattens
hermes' richer message shape (assistant tool_calls, tool results, internal
fields) into that flat form, and — critically — strips any injected
``<memory-context>`` recall blocks so Evolve never re-learns guidelines from
its own prior injections.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

# Reuse hermes' canonical fence stripper so our notion of "memory-context"
# stays identical to what the runtime injects/scrubs.
try:  # pragma: no cover - exercised indirectly
    from agent.memory_manager import sanitize_context as _sanitize_context
except Exception:  # pragma: no cover - keep adapter importable in isolation

    def _sanitize_context(text: str) -> str:
        return text


_DEFAULT_MAX_TOOL_RESULT_CHARS = 2000
# Roles we forward to Evolve. System prompts are hermes-internal and would
# swamp guideline generation, so they are dropped.
_KEEP_ROLES = {"user", "assistant", "tool"}


def _stringify(content: Any) -> str:
    """Coerce a message content payload to a string.

    hermes content is usually a string, but tool results and some providers
    use a list of content parts or a dict. Keep it lossy-but-readable.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(part))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or content)
    return str(content)


def _truncate_tool_result(content: str, budget: int) -> str:
    """Shorten an oversized tool result, keeping both ends.

    Head-only truncation drops exactly the part that matters most: a failing
    command's error usually lands at the *end* of its output, and an
    error-and-recovery pair is the most valuable thing in a trajectory. So keep
    the head (what was run, the first rows of output) and the tail (how it
    ended), and say how much came out of the middle.
    """
    if budget <= 0 or len(content) <= budget:
        return content
    tail = max(1, budget // 3)
    head = budget - tail
    dropped = len(content) - head - tail
    return f"{content[:head]}\n…[{dropped} chars truncated]…\n{content[-tail:]}"


def _tool_call_names(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map ``tool_call_id`` -> tool name across every assistant message.

    A tool result on its own says nothing about which call produced it, which
    makes an error hard to attribute when the turn made several calls. hermes
    puts the name on the result message sometimes and only the id others, so
    build the lookup once from the assistant side.
    """
    names: Dict[str, str] = {}
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id") or call.get("tool_call_id")
            name = (call.get("function") or {}).get("name") or call.get("name")
            if call_id and name:
                names[str(call_id)] = str(name)
    return names


def _render_tool_calls(tool_calls: Any) -> str:
    """Render assistant tool_calls compactly as ``[tool_call] name(args)``."""
    rendered: List[str] = []
    for call in tool_calls or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or "tool"
        args = fn.get("arguments")
        if isinstance(args, (dict, list)):
            args = json.dumps(args, ensure_ascii=False)
        rendered.append(f"[tool_call] {name}({args if args is not None else ''})")
    return "\n".join(rendered)


def to_openai_trajectory(
    messages: List[Dict[str, Any]],
    *,
    max_tool_result_chars: int = _DEFAULT_MAX_TOOL_RESULT_CHARS,
) -> List[Dict[str, str]]:
    """Flatten hermes messages into Evolve's ``[{role, content}, ...]`` form.

    - Keeps user / assistant / tool roles; drops system + everything else.
    - Strips injected ``<memory-context>`` blocks from every content field.
    - Inlines assistant tool_calls as readable text.
    - Labels each tool result with the tool that produced it.
    - Truncates oversized tool results from the middle, keeping both ends.
    - Drops messages that end up empty after stripping.

    The emitted shape stays ``{"role", "content"}`` — the attribution goes into
    the text, because that is all Evolve reads.
    """
    out: List[Dict[str, str]] = []
    call_names = _tool_call_names(messages)
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in _KEEP_ROLES:
            continue

        content = _sanitize_context(_stringify(msg.get("content"))).strip()

        if role == "assistant" and msg.get("tool_calls"):
            calls = _render_tool_calls(msg.get("tool_calls"))
            content = f"{content}\n{calls}".strip() if content else calls

        if role == "tool":
            content = _truncate_tool_result(content, max_tool_result_chars)
            name = msg.get("name") or call_names.get(str(msg.get("tool_call_id") or ""))
            if content and name:
                content = f"[tool_result] {name}\n{content}"

        if not content:
            continue

        out.append({"role": role, "content": content})

    return out


def to_trajectory_json(
    messages: List[Dict[str, Any]],
    *,
    max_tool_result_chars: int = _DEFAULT_MAX_TOOL_RESULT_CHARS,
) -> str:
    """``to_openai_trajectory`` serialized to the JSON string Evolve ingests."""
    return json.dumps(
        to_openai_trajectory(messages, max_tool_result_chars=max_tool_result_chars),
        ensure_ascii=False,
    )
