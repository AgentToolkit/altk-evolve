import os
import sys

if os.environ.get("KAIZEN_AUTO_ENABLED", "").lower() != "true":
    print("WARNING: KAIZEN_AUTO_ENABLED is not true, tracing will not be enabled automatically.")

import kaizen.auto
from kaizen.config.llm import llm_settings

from litellm import completion

def main():
    print(f"Sending request via LiteLLM (Model: {llm_settings.tips_model})...")
    try:
        response = completion(
            model = os.environ.get("KAIZEN_EXAMPLE_AGENT_MODEL") or llm_settings.tips_model,
            custom_llm_provider=llm_settings.custom_llm_provider,
            messages=[{"role": "user", "content": "What is the capital of France?"}],
            max_tokens=10
        )
        print(f"Response: {response.choices[0].message.content}")
    except Exception as e:
        print(f"Error calling LiteLLM: {e}")

if __name__ == "__main__":
    main()
