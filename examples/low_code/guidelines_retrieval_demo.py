import argparse
import os

from altk_evolve.config.llm import llm_settings
from altk_evolve.frontend.client.evolve_client import EvolveClient

from smolagents import CodeAgent, LiteLLMModel, tool

# Reuse the same tools as smolagents_demo.py so this is a direct "part two" of that demo.
from local_mcp_server import add as mcp_add, multiply as mcp_multiply


@tool
def add(a: int, b: int) -> int:
    """
    Add two numbers.
    Args:
        a: First number.
        b: Second number.
    """
    return mcp_add(a, b)  # type: ignore[no-any-return,operator]


@tool
def multiply(a: int, b: int) -> int:
    """
    Multiply two numbers.
    Args:
        a: First number.
        b: Second number.
    """
    return mcp_multiply(a, b)  # type: ignore[no-any-return,operator]


def format_guidelines(selection) -> str:
    """Turn retrieved guideline entities into a plain-text instructions block."""
    lines = [f"- {g.content}" for g in selection.all]
    if not lines:
        return ""
    return "Apply these lessons learned from previous runs:\n" + "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Re-run the smolagents demo with retrieved guidelines injected.")
    parser.add_argument(
        "--namespace",
        default=os.environ.get("EVOLVE_NAMESPACE_ID", "evolve"),
        help="Namespace guidelines were synced/saved into",
    )
    parser.add_argument("--task", default="What is (5 * 5) + 10?", help="Task to run the agent on")
    args = parser.parse_args()

    client = EvolveClient()
    selection = client.select_guidelines(namespace_id=args.namespace, task_query=args.task)

    print(f"Retrieved {len(selection.core)} core + {len(selection.retrieved)} task-specific guideline(s) from '{args.namespace}':")
    for guideline in selection.all:
        print(f"  - {guideline.content}")
    if not selection.all:
        print("  (none found - run the guideline-generation steps first)")

    instructions = format_guidelines(selection)

    # Same model configuration pattern as smolagents_demo.py
    model_id = os.environ.get("EVOLVE_EXAMPLE_AGENT_MODEL") or llm_settings.guidelines_model
    custom_provider = llm_settings.custom_llm_provider
    model = LiteLLMModel(model_id=model_id, custom_llm_provider=custom_provider)

    agent = CodeAgent(
        tools=[add, multiply],
        model=model,
        add_base_tools=False,
        instructions=instructions or None,
    )

    print("\nRunning Smolagents CodeAgent with guidelines injected as instructions...")
    try:
        result = agent.run(args.task)
        print(f"Result: {result}")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"Error running agent: {e}") from e


if __name__ == "__main__":
    main()
