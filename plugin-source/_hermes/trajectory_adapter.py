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
    - Truncates oversized tool results so trajectories stay parseable.
    - Drops messages that end up empty after stripping.
    """
    out: List[Dict[str, str]] = []
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

        if role == "tool" and max_tool_result_chars and len(content) > max_tool_result_chars:
            content = content[:max_tool_result_chars] + "\n…[truncated]"

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
