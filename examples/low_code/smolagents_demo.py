import os
import sys

# Ensure KAIZEN_AUTO_ENABLED is set
if os.environ.get("KAIZEN_AUTO_ENABLED", "").lower() != "true":
    print("WARNING: KAIZEN_AUTO_ENABLED is not true")

import kaizen.auto
from kaizen.config.llm import llm_settings

from smolagents import CodeAgent, LiteLLMModel, tool

# Import tool from our local MCP server definition (Simulating local usage)
from local_mcp_server import add as mcp_add, multiply as mcp_multiply

# Wrap in smolagents @tool
@tool
def add(a: int, b: int) -> int:
    """
    Add two numbers.
    Args:
        a: First number.
        b: Second number.
    """
    return mcp_add(a, b)

@tool
def multiply(a: int, b: int) -> int:
    """
    Multiply two numbers.
    Args:
        a: First number.
        b: Second number.
    """
    return mcp_multiply(a, b)

def main():
    # Use LiteLLMModel to support generic providers
    # Exact match of Kaizen's internal usage pattern:
    model_id = os.environ.get("KAIZEN_EXAMPLE_AGENT_MODEL") or llm_settings.tips_model
    custom_provider = llm_settings.custom_llm_provider
    
    print(f"Running Smolagents CodeAgent (Model: {model_id}, Provider: {custom_provider})...")
    
    # Pass configuration exactly as Kaizen does
    model = LiteLLMModel(
        model_id=model_id, 
        custom_llm_provider=custom_provider
    )
    
    
    # Create agent with local tools
    agent = CodeAgent(tools=[add, multiply], model=model, add_base_tools=False)
    
    print("Running Smolagents CodeAgent...")
    try:
        # Ask it to do math using the tools
        result = agent.run("What is (5 * 5) + 10?")
        print(f"Result: {result}")
    except Exception as e:  # noqa: BLE001
        print(f"Error running agent: {e}")

if __name__ == "__main__":
    main()
