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
from contextlib import contextmanager

import pytest
from fastmcp.client import Client

from altk_evolve.config.evolve import evolve_config
from altk_evolve.config.guidelines import guidelines_settings
from altk_evolve.frontend.client.evolve_client import EvolveClient

pytestmark = pytest.mark.e2e


@contextmanager
def _guidelines_env(**env_vars):
    """Set EVOLVE_GUIDELINES_MODE/EVOLVE_CONSISTENCY_METHOD and force a reload.

    guidelines_settings is a module-level singleton that reads os.environ only once,
    at construction time. Mutating os.environ alone (as this file used to do) has no
    effect on a settings object some earlier import already constructed — save_trajectory
    would keep dispatching on stale values. Reinitializing after the env mutation is
    required for the mode/method actually reaching save_trajectory's dispatch logic.
    """
    originals = {key: os.environ.get(key) for key in env_vars}
    os.environ.update(env_vars)
    guidelines_settings.__init__()
    try:
        yield
    finally:
        for key, original in originals.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        guidelines_settings.__init__()


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
async def test_mcp_standard_mode_tags_generation_method(mcp):
    """EVOLVE_GUIDELINES_MODE=standard stores guidelines tagged generation_method='standard'."""
    with _guidelines_env(EVOLVE_GUIDELINES_MODE="standard"):
        async with Client(transport=mcp) as client:
            task_id = f"test-standard-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )

    guidelines = _get_stored_guidelines(task_id)
    assert len(guidelines) > 0, "Expected at least one standard guideline"
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata["generation_method"] == "standard"


@pytest.mark.e2e
async def test_mcp_consistency_mode_tags_generation_method(mcp):
    """EVOLVE_GUIDELINES_MODE=consistency with the accurate method stores guidelines tagged
    generation_method='consistency' — pinned explicitly since fast is now the default method
    (see test_mcp_consistency_fast_method_tags_generation_method for that path)."""
    if not _consistency_available():
        pytest.skip("consistency analyzer not available")

    with _guidelines_env(EVOLVE_GUIDELINES_MODE="consistency", EVOLVE_CONSISTENCY_METHOD="accurate"):
        async with Client(transport=mcp) as client:
            task_id = f"test-consistency-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )

    guidelines = _get_stored_guidelines(task_id)
    # A consistent trajectory may legitimately produce 0 guidelines when
    # SKIP_ON_NO_UNCERTAINTY fires — the pipeline ran successfully either way.
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata["generation_method"] == "consistency"


@pytest.mark.e2e
async def test_mcp_consistency_fast_method_tags_generation_method(mcp):
    """EVOLVE_CONSISTENCY_METHOD=fast stores guidelines tagged generation_method='consistency-fast',
    via the same save_trajectory entry point used by the accurate/resampling pipeline above —
    no resampling involved, just a single self-judged LLM pass."""
    with _guidelines_env(EVOLVE_GUIDELINES_MODE="consistency", EVOLVE_CONSISTENCY_METHOD="fast"):
        async with Client(transport=mcp) as client:
            task_id = f"test-consistency-fast-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )

    guidelines = _get_stored_guidelines(task_id)
    # The fast pipeline may legitimately produce 0 guidelines if the LLM judges every
    # step confident — the pipeline ran successfully either way.
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata["generation_method"] == "consistency-fast"


@pytest.mark.e2e
async def test_mcp_all_mode_stores_guidelines_from_each_pipeline(mcp):
    """EVOLVE_GUIDELINES_MODE=all with the accurate method stores guidelines from both
    pipelines — pinned explicitly since fast is now the default method (see
    test_mcp_all_mode_with_fast_method_stores_guidelines_from_each_pipeline for that path)."""
    if not _consistency_available():
        pytest.skip("consistency analyzer not available")

    with _guidelines_env(EVOLVE_GUIDELINES_MODE="all", EVOLVE_CONSISTENCY_METHOD="accurate"):
        async with Client(transport=mcp) as client:
            task_id = f"test-all-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )

    guidelines = _get_stored_guidelines(task_id)

    # Standard pipeline always produces guidelines (no SKIP_ON_NO_UNCERTAINTY gate).
    standard = [g for g in guidelines if g.metadata.get("generation_method") == "standard"]
    consistency = [g for g in guidelines if g.metadata.get("generation_method") == "consistency"]

    assert len(standard) > 0, "Expected at least one standard guideline in 'all' mode"
    # Consistency guidelines may be 0 if SKIP_ON_NO_UNCERTAINTY fired; that is valid.
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata.get("generation_method") in ("standard", "consistency"), (
            f"Unexpected generation_method: {g.metadata.get('generation_method')}"
        )
    assert len(standard) + len(consistency) == len(guidelines), "Every guideline must carry a generation_method tag"


@pytest.mark.e2e
async def test_mcp_all_mode_with_fast_method_stores_guidelines_from_each_pipeline(mcp):
    """EVOLVE_GUIDELINES_MODE=all with EVOLVE_CONSISTENCY_METHOD=fast tags the consistency
    side 'consistency-fast' instead of 'consistency', while standard is unaffected."""
    with _guidelines_env(EVOLVE_GUIDELINES_MODE="all", EVOLVE_CONSISTENCY_METHOD="fast"):
        async with Client(transport=mcp) as client:
            task_id = f"test-all-fast-{uuid.uuid4().hex[:8]}"
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": _MATH_AGENT_TRAJECTORY,
                    "task_id": task_id,
                },
            )

    guidelines = _get_stored_guidelines(task_id)

    standard = [g for g in guidelines if g.metadata.get("generation_method") == "standard"]
    consistency_fast = [g for g in guidelines if g.metadata.get("generation_method") == "consistency-fast"]

    assert len(standard) > 0, "Expected at least one standard guideline in 'all' mode"
    for g in guidelines:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata.get("generation_method") in ("standard", "consistency-fast"), (
            f"Unexpected generation_method: {g.metadata.get('generation_method')}"
        )
    assert len(standard) + len(consistency_fast) == len(guidelines), "Every guideline must carry a generation_method tag"
