import json

from jinja2 import Template
from litellm import completion
from kaizen.config.llm import llm_settings
from kaizen.utils.utils import clean_llm_response
from pathlib import Path

def generate_tips(messages: list[dict]) -> list[str]:
    markdown_trajectory = messages_to_markdown(messages)
    prompt_file = Path(__file__).parent / "prompts/generate_tips.jinja2"
    prompt = Template(prompt_file.read_text()).render(markdown_trajectory=markdown_trajectory)
    response = completion(
        model=llm_settings.tips_model,
        messages=[{"role": "user", "content": prompt}],
        custom_llm_provider="openai"
    ).choices[0].message.content
    clean_response = clean_llm_response(response)
    return json.loads(clean_response)

def messages_to_markdown(messages: list[dict]) -> str:
    """
    Convert a list of OpenAI-format messages to a Markdown string.
    """
    md_lines = []

    for msg in messages:
        role = msg.get("role", "unknown").title()
        content = msg.get("content", "")

        md_lines.append(f"## {role}")

        if isinstance(content, str):
            md_lines.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        md_lines.append(block.get("text", ""))
                    elif block.get("type") == "function_call":
                        func = block.get("function", {})
                        md_lines.append(f"**Tool Call**: `{func.get('name')}`")
                        md_lines.append("```json")
                        md_lines.append(func.get("arguments", ""))
                        md_lines.append("```")
                    elif block.get("type") == "function_response":
                        md_lines.append(f"**Tool Result** ({block.get('id')}):")
                        md_lines.append("```")
                        md_lines.append(block.get("content", ""))
                        md_lines.append("```")

        md_lines.append("")  # Empty line between messages

    return "\n".join(md_lines)