"""Guideline generation for the Evolve lite backend.

Ports the capture criteria and exclusion lists from altk-evolve's
evolve-lite ``learn`` skill
(``plugin-source/skills/evolve-lite/learn/SKILL.md.j2``) into a single
structured LLM call via ``agent.plugin_llm.PluginLlm``. This is Phase 0's
answer to "who generates guidelines in lite mode": the provider itself,
at capture time, using the user's active model + auth — no second API
key, no core edits (see README.md, "How it works").

Never raises. Capture must not break a session, so every failure mode
(missing plugin_llm, LLM error, malformed JSON, empty trajectory) falls
back to an empty guideline list instead of propagating.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_GUIDELINES = 5
_CATEGORIES = ("strategy", "recovery", "optimization")

_INSTRUCTIONS = """You analyze an agent's conversation trajectory (tool calls, errors, \
recoveries, workarounds) and extract reusable guidelines for future sessions on similar \
tasks.

Capture:
- concrete solutions to concrete problems (not abstract advice)
- errors encountered during the conversation and how they were recovered from
- non-trivial workarounds, ad hoc scripts, parsers, or command sequences that solved a \
real problem and would save time if reused

Do NOT capture:
- secrets, tokens, API keys, credentials, or any credential-shaped content
- trivial one-liners a future agent would not benefit from reusing
- guidelines that instruct invoking a specific skill, tool, or command by name (e.g. \
"run tool X", "call save_trajectory") -- these trigger prompt-injection detection when \
recalled in a future session
- one-off, user-specific data (literal names, ids, paths) that does not generalize

Each guideline needs:
- content: a proactive statement of what TO DO (not a narration of what happened)
- trigger: the broad situational context when this applies (not the narrow original \
request)
- rationale: one sentence on why this approach works
- category: one of "strategy", "recovery", "optimization"

Return at most 5 guidelines, the most valuable first. If nothing in the trajectory is \
worth capturing, return an empty list.
"""

_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "guidelines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "trigger": {"type": "string"},
                    "rationale": {"type": "string"},
                    "category": {"type": "string", "enum": list(_CATEGORIES)},
                },
                "required": ["content"],
            },
        },
    },
    "required": ["guidelines"],
}


def _default_llm_call(trajectory_json: str) -> Optional[str]:
    """Call ``PluginLlm.complete_structured``; return raw text or None on failure."""
    try:
        from agent.plugin_llm import PluginLlm, PluginLlmTextInput
    except Exception:
        logger.debug("evolve: plugin_llm unavailable, skipping guideline generation", exc_info=True)
        return None

    try:
        llm = PluginLlm(plugin_id="evolve")
        result = llm.complete_structured(
            instructions=_INSTRUCTIONS,
            input=[PluginLlmTextInput(text=trajectory_json)],
            json_schema=_JSON_SCHEMA,
            purpose="evolve-guideline-generation",
        )
        return result.text
    except Exception:
        logger.warning("evolve: guideline generation LLM call failed", exc_info=True)
        return None


def generate_guidelines(
    trajectory_messages: List[Dict[str, Any]],
    *,
    llm_call: Optional[Callable[[str], Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Generate up to 5 guidelines ``{content, trigger, rationale, category}`` from a trajectory.

    ``trajectory_messages`` is the flattened ``[{role, content}, ...]`` list produced
    by ``trajectory_adapter.to_openai_trajectory``. ``llm_call`` is injectable for
    tests -- it takes the trajectory as a JSON string and returns the raw structured
    completion text (or ``None`` on failure); it defaults to a ``PluginLlm``-backed
    call. This function never raises.
    """
    if not trajectory_messages:
        return []

    caller = llm_call or _default_llm_call
    try:
        trajectory_json = json.dumps(trajectory_messages, ensure_ascii=False)
    except Exception:
        logger.debug("evolve: trajectory not JSON-serializable", exc_info=True)
        return []

    try:
        raw = caller(trajectory_json)
    except Exception:
        logger.warning("evolve: guideline generator callable raised", exc_info=True)
        return []

    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except Exception:
        logger.debug("evolve: guideline generation returned non-JSON output", exc_info=True)
        return []

    guidelines = parsed.get("guidelines") if isinstance(parsed, dict) else None
    if not isinstance(guidelines, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in guidelines:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        category = str(item.get("category") or "").strip().lower()
        if category not in _CATEGORIES:
            category = ""
        out.append(
            {
                "content": content,
                "trigger": str(item.get("trigger") or "").strip(),
                "rationale": str(item.get("rationale") or "").strip(),
                "category": category,
            }
        )
        if len(out) >= _MAX_GUIDELINES:
            break
    return out
