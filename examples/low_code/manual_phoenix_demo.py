import os
from dotenv import load_dotenv
from phoenix.otel import register
from openinference.instrumentation.openai import OpenAIInstrumentor
from openai import OpenAI

# Load environment variables from .env if present
load_dotenv()
load_dotenv("../../../.env")  # Try loading from appworld root if running from repo

# EXPLICITLY NO KAIZEN IMPORT
# This demonstrates Brownfield support (Synced via kaizen sync phoenix)


# Define a tool manually to show tracing works with tools
def add(a: int, b: int) -> int:
    return a + b


def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("Skipping: OPENAI_API_KEY not found")
        return

    # 1. Manual Instrumentation
    project_name = os.environ.get("PHOENIX_PROJECT_NAME", "kaizen-manual-demo")
    # Standardize on PHOENIX_URL (matching kaizen config)
    endpoint = os.environ.get("PHOENIX_URL", "http://localhost:6006") + "/v1/traces"

    print(f"Registering Phoenix Tracer (Project: {project_name})...")
    tracer_provider = register(
        project_name=project_name,
        endpoint=endpoint,
    )
    OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)

    # 2. Run OpenAI with Tools
    client = OpenAI()

    # Manually check for Kaizen model config
    model = os.environ.get("KAIZEN_EXAMPLE_AGENT_MODEL") or os.environ.get("KAIZEN_TIPS_MODEL", "gpt-4o-mini")
    print(f"Running Manually Instrumented Agent (Model: {model})...")

    tools = [
        {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Add two numbers",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                },
            },
        }
    ]

    print("Sending request manually instrumented...")
    try:
        messages = [{"role": "user", "content": "What is 100 + 200?"}]
        response = client.chat.completions.create(model=model, messages=messages, tools=tools)

        # Simulate tool execution handling (naive loop for demo)
        msg = response.choices[0].message
        if msg.tool_calls:
            print("Tool call detected!")
            for tc in msg.tool_calls:
                if tc.function.name == "add":
                    import json

                    args = json.loads(tc.function.arguments)
                    result = add(args["a"], args["b"])
                    print(f"Executed add({args['a']}, {args['b']}) -> {result}")
        else:
            print(f"Response: {msg.content}")

    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
