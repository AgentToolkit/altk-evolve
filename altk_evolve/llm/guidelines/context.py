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


_RULES_INSTRUCTION = (
    "The agent had to follow these rules, policies, or constraints. Every guideline must be consistent with them. "
    "Never produce a guideline that tells the agent to bypass, ignore, or work around a rule. "
    "Where a rule forbids something the agent did, the lesson is to follow the rule."
)


def render_rules(rules: str | None) -> str:
    """The Rules and Constraints prompt section, or "" without rules so the prompt is unchanged.

    Rules are rendered whole: a guideline cannot stay consistent with a rule the model never saw.
    """
    if not rules:
        return ""
    return f"# Rules and Constraints\n{_RULES_INSTRUCTION}\n{rules}"
