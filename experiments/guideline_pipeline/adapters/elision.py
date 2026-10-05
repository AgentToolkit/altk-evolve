"""Keep long trajectories within the extractor's step cap, shared by the adapters.

TEMPORARY: parse_openai_agents_trajectory keeps only the first 50 agent steps, so
a long run would lose its ending (where it succeeds or fails). Until those limits
are configurable in the library, adapters keep the head and the tail and replace
the middle with one marker step. Remove with that change.
"""

from __future__ import annotations

from typing import Any

PARSER_STEP_LIMIT = 50
ELISION_HEAD = 6


def elide_middle(steps: list[dict[str, Any]], *, limit: int = PARSER_STEP_LIMIT, head: int = ELISION_HEAD) -> list[dict[str, Any]]:
    """Keep the first head and the last steps, so at most limit steps reach the parser.

    The marker is a plain assistant reasoning message: one step, valid JSON, and
    never mistaken for a tool call.
    """
    if len(steps) <= limit:
        return steps
    tail = limit - head - 1
    elided = len(steps) - head - tail
    marker = {"role": "assistant", "content": f"[{elided} steps elided: the trajectory continues below]"}
    return [*steps[:head], marker, *steps[-tail:]]
