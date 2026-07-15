"""
E2E tests for consistency-guideline generation via the auto-mcp creation mode.

These tests exercise the full path from a `save_trajectory` MCP call through
`generate_consistency_guidelines` to guideline storage in the Evolve backend.
They use an in-process MCP server (the `mcp` fixture from conftest.py) with a
real filesystem backend, so LLM credentials must be configured.

Requirements:
  - EVOLVE_MODEL_NAME or OPENAI_API_KEY in environment
  - EVOLVE_MODEL_NAME or OPENAI_API_KEY in environment
"""

import json
import os
import uuid

import pytest
from fastmcp.client import Client

from altk_evolve.config.evolve import evolve_config
from altk_evolve.frontend.client.evolve_client import EvolveClient

pytestmark = pytest.mark.e2e

# A short two-step trajectory for a simple math assistant.
# Two assistant turns → two steps to resample, keeping LLM cost manageable.
_MATH_AGENT_TRAJECTORY = json.dumps(
    [
        {
            "role": "user",
            "content": "What is the compound interest on $1000 at 5% annual rate for 3 years?",
        },
        {
            "role": "assistant",
            "content": (
                "Using the compound interest formula A = P(1 + r)^t:\n"
                "A = 1000 × (1.05)^3 = 1000 × 1.157625 = $1157.63\n"
                "The interest earned is $157.63."
            ),
        },
        {
            "role": "user",
            "content": "And at 7%?",
        },
        {
            "role": "assistant",
            "content": ("At 7%: A = 1000 × (1.07)^3 = 1000 × 1.225043 = $1225.04\nThe interest earned would be $225.04."),
        },
    ]
)


def _consistency_available() -> bool:
    try:
        import altk_evolve.llm.guidelines.consistency_analyzer.resampling  # noqa: F401

        return True
    except ImportError:
        return False


def _get_stored_guidelines(task_id: str) -> list:
    client = EvolveClient()
    return client.search_entities(
        namespace_id=evolve_config.namespace_id,
        filters={"type": "guideline", "metadata.source_task_id": task_id},
        limit=50,
    )


@pytest.mark.e2e
async def test_mcp_regular_mode_tags_generation_method(mcp):
    """EVOLVE_GUIDELINES_MODE=regular stores guidelines tagged generation_method='regular'."""
    os.environ["EVOLVE_GUIDELINES_MODE"] = "regular"
    try:
        async with Client(transport=mcp) as client:
            task_id = f"test-regular-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )
    finally:
        os.environ.pop("EVOLVE_GUIDELINES_MODE", None)

    guidelines = _get_stored_guidelines(task_id)
    assert len(guidelines) > 0, "Expected at least one regular guideline"
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata["generation_method"] == "regular"


@pytest.mark.e2e
async def test_mcp_consistency_mode_tags_generation_method(mcp):
    """EVOLVE_GUIDELINES_MODE=consistency stores guidelines tagged generation_method='consistency'."""
    if not _consistency_available():
        pytest.skip("consistency analyzer not available")

    os.environ["EVOLVE_GUIDELINES_MODE"] = "consistency"
    try:
        async with Client(transport=mcp) as client:
            task_id = f"test-consistency-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )
    finally:
        os.environ.pop("EVOLVE_GUIDELINES_MODE", None)

    guidelines = _get_stored_guidelines(task_id)
    # A consistent trajectory may legitimately produce 0 guidelines when
    # SKIP_ON_NO_UNCERTAINTY fires — the pipeline ran successfully either way.
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata["generation_method"] == "consistency"


@pytest.mark.e2e
async def test_mcp_both_mode_stores_guidelines_from_each_pipeline(mcp):
    """EVOLVE_GUIDELINES_MODE=both stores guidelines from both pipelines."""
    if not _consistency_available():
        pytest.skip("consistency analyzer not available")

    os.environ["EVOLVE_GUIDELINES_MODE"] = "both"
    try:
        async with Client(transport=mcp) as client:
            task_id = f"test-both-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )
    finally:
        os.environ.pop("EVOLVE_GUIDELINES_MODE", None)

    guidelines = _get_stored_guidelines(task_id)

    # Regular pipeline always produces guidelines (no SKIP_ON_NO_UNCERTAINTY gate).
    regular = [g for g in guidelines if g.metadata.get("generation_method") == "regular"]
    consistency = [g for g in guidelines if g.metadata.get("generation_method") == "consistency"]

    assert len(regular) > 0, "Expected at least one regular guideline in 'both' mode"
    # Consistency guidelines may be 0 if SKIP_ON_NO_UNCERTAINTY fired; that is valid.
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata.get("generation_method") in ("regular", "consistency"), (
            f"Unexpected generation_method: {g.metadata.get('generation_method')}"
        )
    assert len(regular) + len(consistency) == len(guidelines), "Every guideline must carry a generation_method tag"
