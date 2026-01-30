import os
import sys

# Ensure KAIZEN_AUTO_ENABLED is set for the import to work
if os.environ.get("KAIZEN_AUTO_ENABLED", "").lower() != "true":
    print("WARNING: KAIZEN_AUTO_ENABLED is not true, tracing will not be enabled automatically.")

import kaizen.auto
from kaizen.config.llm import llm_settings

from openai import OpenAI

def main():
    client = OpenAI()
    model = os.environ.get("KAIZEN_EXAMPLE_AGENT_MODEL") or llm_settings.tips_model
    
    print(f"Sending request to OpenAI (Model: {model})...")
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "What is 2 + 2?"}],
            max_tokens=10
        )
        print(f"Response: {response.choices[0].message.content}")
        
        # Explicitly flush traces for short-lived scripts
        kaizen.auto.flush_traces()
    except Exception as e:
        print(f"Error calling OpenAI: {e}")

if __name__ == "__main__":
    main()
