import json
import logging
from json import JSONDecodeError
from pathlib import Path

import litellm
from jinja2 import Template
from litellm import completion, get_supported_openai_params, supports_response_schema
from pydantic import ValidationError

from kaizen.config.llm import llm_settings
from kaizen.schema.exceptions import KaizenException
from kaizen.schema.tips import DEFAULT_TASK_DESCRIPTION, TipGenerationResponse, TipGenerationResult
from kaizen.utils.utils import clean_llm_response

logger = logging.getLogger(__name__)


def parse_openai_agents_trajectory(messages: list[dict]) -> dict:
    """
    Parse OpenAI Agents SDK trajectory from streamer.to_input_list().

    Returns:
        dict with:
        - task_instruction: The task description
        - agent_steps: List of agent reasoning/actions
        - function_calls: List of tool/function calls made
        - num_steps: Total number of agent actions
    """
    agent_steps: list[dict[str, str | dict]] = []
    function_calls: list[dict[str, str | dict]] = []
    task_instruction: str | None = None

    for message in messages:
        # Extract task instruction from first user message
        if message.get("role") == "user" and task_instruction is None:
            if isinstance(message["content"], str):
                task_instruction = message["content"]
            else:
                raise KaizenException("First user message was not a task instruction.")

        # Extract assistant reasoning/messages
        if message.get("role") == "assistant":
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                agent_steps.append({"type": "reasoning", "content": content, "raw": message})

            # Extract function calls
            elif isinstance(content, list):
                for assistant_response in content:
                    if assistant_response["type"] == "function_call":
                        function_call = {
                            "type": "function_call",
                            "name": assistant_response["function"]["name"],
                            "arguments": assistant_response["function"]["arguments"],
                            "call_id": assistant_response["id"],
                            "raw": assistant_response,
                        }
                        function_calls.append(function_call)

                        # Add to agent steps as an action
                        args_str = assistant_response["function"]["arguments"]
                        try:
                            args: dict = json.loads(args_str)
                            args_display = ", ".join(f"{k}={json.dumps(v)}" for k, v in args.items())
                            function_description = f"{assistant_response['function']['name']}({args_display})"
                        except JSONDecodeError:
                            function_description = f"{assistant_response['function']['name']}({args_str})"

                        agent_steps.append(
                            {
                                "type": "action",
                                "content": function_description,
                                "raw": assistant_response,
                            }
                        )
                    else:
                        raise KaizenException(f"Unhandled assistant content type in list `{assistant_response['type']}`")
            else:
                # Skip empty assistant messages (common from tool-calling patterns)
                continue

    steps_text = []
    for i, step in enumerate(agent_steps[:50], 1):
        step_type = step["type"]
        content = step["content"]
        # Truncate long content
        if len(content) > 2000:
            content = content[:2000] + "..."

        if step_type == "reasoning":
            steps_text.append(f"**Step {i} - Reasoning:**\n{content}")
        elif step_type == "action":
            steps_text.append(f"**Step {i} - Action:**\n{content}")
        elif step_type == "observation":
            steps_text.append(f"**Step {i} - Observation:**\n{content}")

    return {
        "task_instruction": task_instruction or DEFAULT_TASK_DESCRIPTION,
        "trajectory_summary": "\n\n".join(steps_text),
        "function_calls": function_calls,
        "num_steps": len([s for s in agent_steps if s["type"] in ["action", "reasoning"]]),
    }


def generate_tips(messages: list[dict]) -> TipGenerationResult:
    prompt_file = Path(__file__).parent / "prompts/generate_tips.jinja2"
    supported_params = get_supported_openai_params(
        model=llm_settings.tips_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    supports_response_format = supported_params and "response_format" in supported_params
    response_schema_enabled = supports_response_schema(
        model=llm_settings.tips_model,
        custom_llm_provider=llm_settings.custom_llm_provider,
    )
    constrained_decoding_supported = supports_response_format and response_schema_enabled
    trajectory_data = parse_openai_agents_trajectory(messages)
    task_description = trajectory_data["task_instruction"]
    prompt = Template(prompt_file.read_text()).render(
        task_instruction=task_description,
        num_steps=trajectory_data["num_steps"],
        trajectory_summary=trajectory_data["trajectory_summary"],
        constrained_decoding_supported=constrained_decoding_supported,
    )

    if constrained_decoding_supported:
        litellm.enable_json_schema_validation = True
        clean_response = (
            completion(
                model=llm_settings.tips_model,
                messages=[{"role": "user", "content": prompt}],
                response_format=TipGenerationResponse,
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            .choices[0]
            .message.content
        )
    else:
        litellm.enable_json_schema_validation = False
        response = (
            completion(
                model=llm_settings.tips_model,
                messages=[{"role": "user", "content": prompt}],
                custom_llm_provider=llm_settings.custom_llm_provider,
            )
            .choices[0]
            .message.content
        )
        clean_response = clean_llm_response(response)
    if not clean_response:
        logger.warning(f"LLM returned empty response for tip generation. Model: {llm_settings.tips_model}")
        return TipGenerationResult(tips=[], task_description=task_description)
    try:
        tips = TipGenerationResponse.model_validate(json.loads(clean_response)).tips
        return TipGenerationResult(tips=tips, task_description=task_description)
    except JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM tip generation response: {e}. Response: {repr(clean_response[:500])}")
        return TipGenerationResult(tips=[], task_description=task_description)
    except ValidationError as e:
        logger.warning(f"Failed to validate LLM tip generation response: {e}. Response: {repr(clean_response[:500])}")
        return TipGenerationResult(tips=[], task_description=task_description)
