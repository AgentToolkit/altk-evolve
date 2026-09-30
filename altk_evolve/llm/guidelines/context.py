"""Bound history for extraction prompts without altering inference replay inputs."""

import json


def render_supporting_context(messages: list[dict] | None, *, max_chars: int = 20000) -> str:
    """Render recent history within a character budget; replay uses original messages."""
    if not messages:
        return ""
    parts = []
    remaining = max_chars - len("[earlier history omitted]\n")
    for message in reversed(messages[-50:]):
        rendered = json.dumps(message, ensure_ascii=False)
        if len(rendered) > 2000:
            rendered = rendered[:2000] + " [truncated]"
        if len(rendered) + 1 > remaining:
            break
        parts.append(rendered)
        remaining -= len(rendered) + 1
    prefix = "[earlier history omitted]\n" if len(parts) < len(messages) else ""
    return prefix + "\n".join(reversed(parts))
