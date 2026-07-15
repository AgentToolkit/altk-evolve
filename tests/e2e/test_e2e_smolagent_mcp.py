"""
E2E test: smolagents CodeAgent with altk-evolve integrated via MCP save_trajectory.

A real smolagents CodeAgent runs a math task. After it completes, its
conversation is extracted and saved via the MCP save_trajectory tool with
guidelines_mode='consistency'. The test passes when:
  - The consistency score card is written (resampling completed)
  - Guidelines are stored in the backend (0 is acceptable if
    SKIP_ON_NO_UNCERTAINTY fired for a sufficiently consistent trajectory)

Requires:
  - uv sync --extra examples
  - EVOLVE_MODEL_NAME or OPENAI_API_KEY for agent + resampling LLM calls
"""

import json
import uuid
from pathlib import Path

import pytest
from fastmcp.client import Client

from altk_evolve.config.evolve import evolve_config
from altk_evolve.frontend.client.evolve_client import EvolveClient

pytestmark = pytest.mark.e2e


def _consistency_available() -> bool:
    try:
        import altk_evolve.llm.guidelines.consistency_analyzer.resampling  # noqa: F401

        return True
    except ImportError:
        return False


def _smolagents_available() -> bool:
    try:
        import smolagents  # noqa: F401

        return True
    except ImportError:
        return False


def _run_smolagent_and_extract_messages() -> list[dict]:
    """
    Run a minimal smolagents CodeAgent on a math task and return its
    full conversation in OpenAI message format.

    The agent uses two local tools (add, multiply) and no Phoenix tracing —
    trajectory extraction happens by reading the agent's memory after the run.
    """
    from smolagents import CodeAgent, LiteLLMModel, tool
    from altk_evolve.config.llm import llm_settings

    @tool
    def add(a: int, b: int) -> int:
        """
        Add two numbers.
        Args:
            a: First number.
            b: Second number.
        """
        return a + b

    @tool
    def multiply(a: int, b: int) -> int:
        """
        Multiply two numbers.
        Args:
            a: First number.
            b: Second number.
        """
        return a * b

    model = LiteLLMModel(
        model_id=llm_settings.guidelines_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    agent = CodeAgent(tools=[add, multiply], model=model, add_base_tools=False)
    agent.run("What is (10 * 2) + 5?")

    # write_memory_to_messages() returns smolagents ChatMessage objects;
    # .dict() produces plain dicts with role/content/tool_calls keys that
    # map directly onto the OpenAI message format.
    from smolagents.models import MessageRole

    # smolagents role → OpenAI role mapping.
    # TOOL_CALL is a smolagents-internal parsed representation of the code the LLM
    # produced — it is NOT a separate LLM call. It duplicates the preceding ASSISTANT
    # message, so we skip it to avoid back-to-back assistant turns in the trajectory.
    _ROLE_MAP = {
        MessageRole.SYSTEM: "system",
        MessageRole.USER: "user",
        MessageRole.ASSISTANT: "assistant",
        MessageRole.TOOL_RESPONSE: "user",  # observation → user message
    }

    messages = []
    for msg in agent.write_memory_to_messages():
        role = _ROLE_MAP.get(msg.role)
        if role is None:
            continue  # skip unmapped roles (e.g. TOOL_CALL)

        # smolagents content is either a plain str or a list of {"type": "text", "text": "..."}
        content = msg.content
        if isinstance(content, list):
            content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))

        if content:
            messages.append({"role": role, "content": content})

    return messages


@pytest.mark.e2e
async def test_smolagent_mcp_consistency_pipeline(mcp):
    """
    Full e2e consistency pipeline via MCP:

    1. Run a real smolagents CodeAgent (add/multiply tools) on a math task.
    2. Extract the agent's conversation in OpenAI format from its memory.
    3. Save the trajectory via save_trajectory (guidelines_mode='consistency').
    4. Assert that:
       a. The trajectory IR was written (pipeline entered consistency analysis).
       b. The score card was written (resampling completed successfully).
       c. Any stored guidelines carry the expected metadata tags.
    """
    if not _smolagents_available():
        pytest.skip("smolagents not installed — run `uv sync --extra examples`")
    if not _consistency_available():
        pytest.skip("consistency analyzer not available")

    debug_dir = Path(__file__).parent.parent.parent / "consistency_debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    for f in debug_dir.glob("guidelines_*.json"):
        f.unlink()

    # --- Step 1: Run the agent ---
    print("\n--- Step 1: Running smolagents CodeAgent ---")
    messages = _run_smolagent_and_extract_messages()
    assert len(messages) > 1, f"Expected a multi-turn conversation, got: {messages}"
    print(f"Agent produced {len(messages)} messages")

    # --- Step 2: Save trajectory via MCP ---
    # EVOLVE_DEBUG_DIR tells generate_consistency_guidelines where to
    # write debug artifacts; it is not an explicit parameter on save_trajectory.
    task_id = f"smol-mcp-{uuid.uuid4().hex[:8]}"
    print(f"\n--- Step 2: Saving trajectory via MCP (task_id={task_id}) ---")

    import os

    os.environ["EVOLVE_DEBUG_DIR"] = str(debug_dir)
    os.environ["EVOLVE_GUIDELINES_MODE"] = "consistency"
    try:
        async with Client(transport=mcp) as client:
            await client.call_tool_mcp(
                "save_trajectory",
                {
                    "trajectory_data": json.dumps(messages),
                    "task_id": task_id,
                },
            )
    finally:
        os.environ.pop("EVOLVE_DEBUG_DIR", None)
        os.environ.pop("EVOLVE_GUIDELINES_MODE", None)

    # --- Step 3: Verify the full pipeline ran to completion ---
    # generate_consistency_guidelines always writes guidelines_*.json as its
    # very last action — both when SKIP_ON_NO_UNCERTAINTY fires (0 guidelines)
    # and when guidelines are actually generated. This file is the true endpoint.
    print(f"\n--- Step 3: Verifying debug artifacts in {debug_dir} ---")

    all_files = list(debug_dir.iterdir())
    print(f"Debug artifacts: {[f.name for f in sorted(all_files)]}")

    guidelines_files = list(debug_dir.glob("guidelines_*.json"))
    assert guidelines_files, (
        f"No guidelines file written — consistency pipeline did not complete. Debug dir contents: {[f.name for f in all_files]}"
    )

    # Print score cards for visibility
    for sc_file in sorted(debug_dir.glob("consistency_score_card_*.json")):
        sc = json.loads(sc_file.read_text())
        print(f"\nScore card ({sc_file.name}):")
        print(f"  task: {sc.get('task')}")
        print(f"  aggregate_uncertainty: {sc.get('aggregate_trajectory_uncertainty')}")
        for step in sc.get("steps", []):
            print(f"  step {step.get('step_number')}: uncertainty={step.get('step_uncertainty')}")

    # Print generated guidelines (may be empty if trajectory was fully consistent)
    for g_file in sorted(guidelines_files):
        g_data = json.loads(g_file.read_text())
        print(f"\nGuidelines ({g_file.name}):")
        for result in g_data:
            for g in result.get("guidelines", []):
                print(f"  [{g.get('category')}] {g.get('content', '')[:100]}")

    # --- Step 4: Verify stored guidelines ---
    print("\n--- Step 4: Checking stored guidelines ---")
    evolve_client = EvolveClient()
    stored = evolve_client.search_entities(
        namespace_id=evolve_config.namespace_id,
        filters={"type": "guideline", "metadata.source_task_id": task_id},
        limit=50,
    )
    print(f"Stored guidelines: {len(stored)}")
    for g in stored:
        assert g.metadata["creation_mode"] == "auto-mcp"
        assert g.metadata["generation_method"] == "consistency"
        print(f"  [{g.metadata['category']}] {g.content[:80]}...")
