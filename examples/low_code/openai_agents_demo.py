import os
import asyncio

# Ensure KAIZEN_AUTO_ENABLED is set
if os.environ.get("KAIZEN_AUTO_ENABLED", "").lower() != "true":
    print("WARNING: KAIZEN_AUTO_ENABLED is not true")

import kaizen.auto  # noqa: F401
from kaizen.config.llm import llm_settings

from agents import Agent, Runner, function_tool, ModelSettings
from agents.extensions.models.litellm_model import LitellmModel

# Import tool from our local MCP server definition
from local_mcp_server import add as mcp_add, multiply as mcp_multiply


@function_tool
async def add(a: int, b: int) -> int:
    """Add two numbers."""
    return mcp_add(a, b)  # type: ignore[no-any-return,operator]


@function_tool
async def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return mcp_multiply(a, b)  # type: ignore[no-any-return,operator]


async def main():
    # Create agent with local tools
    model_name = os.environ.get("KAIZEN_EXAMPLE_AGENT_MODEL") or llm_settings.tips_model
    custom_provider = llm_settings.custom_llm_provider

    # Use the Agent SDK's LitellmModel adapter
    # Explicitly pass Azure configuration and preserve model case (Azure/gpt-4.1)
    model = LitellmModel(
        model=model_name,
    )

    print(f"Running OpenAI Agent (Model: {model_name})...")

    # Pass custom_llm_provider via ModelSettings extra_args
    # This flows into litellm.acompletion(**extra_kwargs)
    settings = ModelSettings(extra_args={"custom_llm_provider": custom_provider})

    agent = Agent(
        name="MathAgent",
        instructions="You are a helpful assistant that does math.",
        model=model,
        model_settings=settings,
        tools=[add, multiply],
    )

    print("Running OpenAI Agent...")
    try:
        result = await Runner.run(agent, "What is (10 * 2) + 5?")
        print(f"Result: {result.final_output}")
    except Exception:  # noqa: BLE001
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
