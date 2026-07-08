"""
Sample preprocessing and response parsing utilities.

This module provides functions to:
- Parse agent responses in various formats (JSON, ReAct, code)
- Extract parsed responses from trajectory samples
- Handle field backfilling for missing values
- Validate sample data and agent configurations
"""

import logging

logger = logging.getLogger(__name__)
import json
import re

from altk_evolve.llm.guidelines.consistency_analyzer.utils import invert_list_of_dictionaries


def get_agent_config(agent: str, config: dict) -> dict:
    """
    Find metric configuration for a specific agent.

    Args:
        agent: Agent name
        config: Configuration dict with 'agents' list

    Returns:
        Agent configuration dict, or empty dict if not found
    """
    for c in config["agents"]:
        if agent == c["name"]:
            return c
    return {}


def parse_code_response(response: str) -> str:
    """
    Parse code response, stripping markdown fences and comments.

    Args:
        response: Raw response text

    Returns:
        Cleaned code string
    """
    # remove everything preceding the python code
    response = response[response.find("```") :]
    # remove everything following the python code
    response = response[: (response.rfind("```") + 3)]
    parsed_response = response.strip().removeprefix("```python").removesuffix("```").strip()

    # Remove Python-style comments
    # Remove multi-line comments (""" or ''')
    parsed_response = re.sub(r'"""[\s\S]*?"""', "", parsed_response)
    parsed_response = re.sub(r"'''[\s\S]*?'''", "", parsed_response)

    # Remove single-line comments (# ...)
    lines = parsed_response.split("\n")
    cleaned_lines = []
    for line in lines:
        # Find the position of # that's not inside a string
        in_string = False
        string_char = None
        comment_pos = -1

        for i, char in enumerate(line):
            if char in ['"', "'"] and (i == 0 or line[i - 1] != "\\"):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    in_string = False
                    string_char = None
            elif char == "#" and not in_string:
                comment_pos = i
                break

        if comment_pos >= 0:
            cleaned_lines.append(line[:comment_pos].rstrip())
        else:
            cleaned_lines.append(line)

    parsed_response = "\n".join(cleaned_lines)

    return parsed_response


def parse_tool_calls_response(response: list) -> dict:
    """
    Parse a tool_calls response into a dict of lists.

    Args:
        response: List of tool call dictionaries, e.g.
            [{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}, ...]

    Returns:
        Dictionary obtained by inverting the list of tool call dictionaries
        (see invert_list_of_dictionaries), or an empty dict if response is
        not a list of dictionaries
    """
    if not isinstance(response, list) or not all(isinstance(call, dict) for call in response):
        return {}
    return invert_list_of_dictionaries(response)


def parse_thought_code_response(response: str) -> dict:
    """
    Parse response to extract thought and code portions.

    This function extracts both the thought (non-code text preceding the Python code)
    and the code (Python code block) from a response string. The code extraction
    follows the same logic as parse_code_response.

    Args:
        response: Raw response text containing thought and code

    Returns:
        Dictionary with "thought" and "code" keys:
        - "thought": Text preceding the Python code block (empty string if none)
        - "code": Extracted and cleaned Python code (same as parse_code_response output)
    """
    result = {"thought": "", "code": ""}

    # Check if there's a code block
    if "```" not in response:
        # No code block found - treat entire response as thought
        result["thought"] = response.strip()
        return result

    # Find the start of the code block
    code_start = response.find("```")

    # Extract thought: everything before the code block
    result["thought"] = response[:code_start].strip()

    # Extract code using the same logic as parse_code_response
    code_portion = response[code_start:]

    # Find the end of the code block
    code_end = code_portion.rfind("```")
    if code_end == -1:
        # Malformed code block - no closing ```
        result["code"] = ""
        return result

    # Extract code between ``` markers
    code_portion = code_portion[: (code_end + 3)]
    parsed_code = code_portion.strip().removeprefix("```python").removesuffix("```").strip()

    # Remove Python-style comments (same as parse_code_response)
    # Remove multi-line comments (""" or ''')
    parsed_code = re.sub(r'"""[\s\S]*?"""', "", parsed_code)
    parsed_code = re.sub(r"'''[\s\S]*?'''", "", parsed_code)

    # Remove single-line comments (# ...)
    lines = parsed_code.split("\n")
    cleaned_lines = []
    for line in lines:
        # Find the position of # that's not inside a string
        in_string = False
        string_char = None
        comment_pos = -1

        for i, char in enumerate(line):
            if char in ['"', "'"] and (i == 0 or line[i - 1] != "\\"):
                if not in_string:
                    in_string = True
                    string_char = char
                elif char == string_char:
                    in_string = False
                    string_char = None
            elif char == "#" and not in_string:
                comment_pos = i
                break

        if comment_pos >= 0:
            cleaned_lines.append(line[:comment_pos].rstrip())
        else:
            cleaned_lines.append(line)

    parsed_code = "\n".join(cleaned_lines)
    result["code"] = parsed_code

    return result


def parse_json_response(response: str) -> dict:
    """
    Parse JSON response.

    Args:
        response: Raw response text

    Returns:
        Parsed JSON dict, or empty dict if unparseable
    """
    if response == "":
        return {}
    try:
        parsed_response = json.loads(response)
    except Exception:
        # isolate the json output
        # remove everything preceding the json output
        response = str(response)
        response = response[response.find("```") :]
        # remove everything following the json output
        response = response[: (response.rfind("```") + 3)]
        response = (
            response.strip()
            .removeprefix("```json")
            .removeprefix("```Json")
            .removeprefix("```JSON")
            .removeprefix("```")
            .removesuffix("```")
            .strip()
        )
        try:
            parsed_response = json.loads(response)
        except (json.JSONDecodeError, ValueError):
            response = response.split("json")[-1].replace("```", "").strip()
            try:
                parsed_response = json.loads(response)
            except (json.JSONDecodeError, ValueError):
                parsed_response = {}

    return parsed_response


def find_reverse(str_a: str, ch: str) -> int:
    """
    Find last occurrence of character in string.

    Args:
        str_a: String to search
        ch: Character to find

    Returns:
        Index of last occurrence, or -1 if not found
    """
    for i in range(len(str_a) - 1, -1, -1):
        if str_a[i] == ch:
            return i
    return -1


def strip_end(a: str, b: str) -> str:
    """
    Remove all trailing occurrences of substring.

    Args:
        a: String to modify
        b: Substring to remove from end

    Returns:
        Modified string
    """
    while a.endswith(b):
        a = a[: len(a) - len(b)]
    return a


def fix_fractions(json_str):
    """
    Replace fractions with decimal values in JSON string.

    Args:
        json_str: JSON string potentially containing fractions

    Returns:
        Modified string with fractions converted to decimals
    """

    def replacer(match):
        numerator, denominator = match.group(0).split("/")
        try:
            value = int(numerator) / int(denominator)
        except ZeroDivisionError:
            value = float("nan")  # return NaN for division by zero
        return str(value)

    fixed = re.sub(r"\b\d+/\d+\b", replacer, json_str)
    return fixed


def parse_react_aw_response(response: str) -> dict:
    """
    Parse ReAct AppWorld response.

    Args:
        response: Raw response text

    Returns:
        Parsed response dict with API_name and API_params
    """
    item = {"API_name": "", "API_params": ""}

    # Track character positions in original response
    original_response = response

    # Extract Python code block from response
    if "```python" not in response:
        logger.debug("+++ Warning: No Python code block found in response")
        return item

    # Find the python code block in the original response
    code_start = original_response.find("```python")
    code_end = original_response.find("```", code_start + 9)

    if code_end == -1:
        logger.debug("+++ Warning: Python code block not properly closed in response ")
        return item

    # Extract the code content (skip "```python" which is 9 chars)
    code_block = original_response[code_start + 9 : code_end].strip()

    # Parse the API call from the code
    # Look for patterns like: apis.api_docs.show_api_doc(...) or logger.debug(apis.api_docs.show_api_doc(...))
    # The code block may have multiple lines, so we need to find the line with the API call

    # Find where "apis." starts in the original response
    # We need to account for the code block start position and any whitespace
    apis_search_start = code_start + 9  # After "```python"
    apis_pos_in_response = original_response.find("apis.", apis_search_start)

    if apis_pos_in_response == -1:
        logger.debug("+++ Warning: No API call found in response (no 'apis.' prefix) ")
        return item

    # Find the line containing the API call in the code block
    # The API call might span multiple lines, so we need to find the complete call

    # Find where "apis." starts in the code block
    apis_in_block_pos = code_block.find("apis.")
    if apis_in_block_pos == -1:
        logger.debug("+++ Warning: No 'apis.' found in code block ")
        return item

    # Extract everything from "apis." onwards in the entire code block
    api_call_part = code_block[apis_in_block_pos:]

    # Find the opening parenthesis in the API call
    paren_start_in_call = api_call_part.find("(")
    if paren_start_in_call == -1:
        logger.debug("+++ Warning: No opening parenthesis found in API call")
        return item

    # Extract API name (everything before the opening parenthesis, removing 'apis.' prefix)
    api_full_name = api_call_part[:paren_start_in_call]
    api_name = api_full_name[5:]  # Remove 'apis.' prefix

    # Extract parameters (everything between parentheses)
    # Need to find the matching closing parenthesis, handling nested parens and strings
    paren_count = 0
    paren_end_in_call = -1
    in_string = False
    string_char = None

    for i in range(paren_start_in_call, len(api_call_part)):
        char = api_call_part[i]

        # Handle string delimiters
        if char in ['"', "'"] and (i == paren_start_in_call or api_call_part[i - 1] != "\\"):
            if not in_string:
                in_string = True
                string_char = char
            elif char == string_char:
                in_string = False
                string_char = None
        # Only count parentheses outside of strings
        elif not in_string:
            if char == "(":
                paren_count += 1
            elif char == ")":
                paren_count -= 1
                if paren_count == 0:
                    paren_end_in_call = i
                    break

    if paren_end_in_call == -1:
        logger.debug("+++ Warning: No matching closing parenthesis found in API call")
        return item

    params_str = api_call_part[paren_start_in_call + 1 : paren_end_in_call].strip()
    # normalize the parameter string by removing white space and \n characters
    params_str = params_str.replace(" ", "")
    params_str = params_str.replace("\n", "")

    item["API_name"] = api_name
    item["API_params"] = params_str
    return item


def parse_react_response(response: str) -> dict:
    """
    Parse ReAct response.

    Args:
        response: Raw response text

    Returns:
        Parsed response dict with thought/action/action_input fields
    """
    item = {}

    # Track character positions in original response
    original_response = response

    if "Final Answer:" in response:
        final_ans_pos = original_response.find("Final Answer:")

        temp = response.split("Final Answer:")
        response_part, final_ans = temp[0].strip(), temp[1].strip()
        item["thought"] = response_part
        item["final_ans"] = final_ans

        return item

    if "Action Input:" not in response:
        item["parse_error_msg"] = (
            "If you have already got enough information for the final answer,"
            ' say "Final Answer:" followed by your answer. Otherwise, please'
            ' specify your API call via "Action:" and API arguments via "Action'
            ' Input:" followed by a json string. If there are no arguments, use'
            ' "Action Input: {}". Do NOT start your response with'
            ' "Observation:"; there is no need to repeat it.'
        )
        # parse_error_msg is synthetic - no corresponding tokens
        return item

    if response.count("Action Input:") > 1:
        item["parse_error_msg"] = 'Please use only one "Action Input:" in your response.'
        return item

    # Track character positions before splitting
    action_input_pos = original_response.find("Action Input:")
    action_pos = original_response.find("Action:")

    action, action_input = response.split("Action Input:")
    action, action_input = (
        strip_end(action.strip(), "\\n").strip(),
        strip_end(action_input.strip(), "\\n").strip(),
    )

    # get action
    if "Action:" not in action:
        item["parse_error_msg"] = 'Please specify the API name you would like to call via "Action:" followed by the name. '
        return item

    if action.count("Action:") > 1:
        item["parse_error_msg"] = 'Please use only one "Action:" in your response.'
        return item

    thought, action = action.split("Action:")
    thought, action = (
        strip_end(thought.strip(), "\\n").strip(),
        strip_end(action.strip(), "\\n").strip(),
    )

    # Track thought position (may or may not have "Thought:" prefix)
    thought_pos = 0  # Default to beginning
    if "Thought:" in original_response:
        thought_pos = original_response.find("Thought:")

    if "Thought:" not in thought:
        # auto-correct: treat everything as the thought
        item["thought"] = thought
        thought_pos = 0  # Starts at beginning if no "Thought:" marker
    elif thought.count("Thought:") > 1:
        item["parse_error_msg"] = 'Please use only one "Thought:" in your response.'
        return item
    else:
        thought = thought.split("Thought:")[-1].strip()
        item["thought"] = thought

    # get action input
    left_bracket_pos = action_input.find("{")
    if left_bracket_pos == -1:
        item["parse_error_msg"] = 'the Action Input is in json string format, and should begin with "{"'
        return item
    right_bracket_pos = find_reverse(action_input, "}")
    if right_bracket_pos == -1:
        item["parse_error_msg"] = 'the Action Input is in json string format, and should end with "}". Do NOT say anything else after "}"'
        return item

    if left_bracket_pos >= right_bracket_pos:
        item["parse_error_msg"] = "Your action input cannot be parsed as a json string. Please try again."
        return item

    # keep only within {}
    action_input = action_input[left_bracket_pos : right_bracket_pos + 1]
    action_input = "{" + action_input.strip("{}") + "}"

    if action_input.startswith("{{"):
        item["parse_error_msg"] = 'the Action Input is in json string format, and should begin with only one "{", not two or more.'
        return item
    if action_input.endswith("}}"):
        item["parse_error_msg"] = (
            'the Action Input is in json string format, and should end with only one "}". Do NOT say anything else after "}"'
        )
        return item

    modified_action_input = action_input.replace("\\n", "")
    modified_action_input = modified_action_input.replace("\n", "")
    modified_action_input = modified_action_input.replace("\\", "")
    modified_action_input = fix_fractions(modified_action_input)

    try:
        _ = json.loads(modified_action_input)
    except (json.JSONDecodeError, ValueError):
        item["parse_error_msg"] = (
            f"the Action Input is in json string format, the generated value of {action_input} is not a valid json string."
        )
        return item

    modified_action_input = modified_action_input.strip()

    item["thought"] = thought
    item["action"] = action
    item["action_input"] = modified_action_input

    return item


def inner_field_backfill(response, config: dict):
    """
    Backfill missing fields in response according to config.

    Args:
        response: Response dict to backfill
        config: Configuration dict with 'fields' list

    Returns:
        Response with backfilled fields
    """
    if "fields" in config:
        # we are expecting a dictionary
        if not isinstance(response, dict):
            response = {}
        for field in config["fields"]:
            if "backfill" not in field:
                continue
            if isinstance(field["name"], str) and field["name"] not in response:
                response[field["name"]] = field["backfill"]
    return response


def field_backfill(response, config: dict):
    """
    Backfill missing field values according to config.

    Handles both regular configs and alternate configs.

    Args:
        response: Response dict to backfill
        config: Configuration dict

    Returns:
        Response with backfilled fields
    """
    if config["response_type"] in ["json", "react"]:
        if "alternates" in config:
            for alternate in config["alternates"]:
                response = inner_field_backfill(response, alternate)
        else:
            response = inner_field_backfill(response, config)
    return response


def parsed_response_backfill(config: dict):
    """
    Create backfilled response for unparseable responses.

    Args:
        config: Configuration dict with response_type and optional backfill

    Returns:
        Backfilled response (dict for JSON/ReAct, string for code)
    """
    if config["response_type"] in ["json", "react", "react_aw"]:
        response = {}
        if "backfill" in config:
            response = config["backfill"]
        else:
            response = field_backfill(response, config)
    elif config["response_type"] == "code":
        response = ""
        if "backfill" in config:
            response = config["backfill"]
    elif config["response_type"] == "tool_calls":
        response = []

    return response


def extract_parsed_responses_from_trajectory(trajectory: dict, config: dict) -> dict:
    """
    Extract and parse responses from all steps in a trajectory.

    Args:
        trajectory: Trajectory dict with steps
        config: Configuration dict with agent configs

    Returns:
        Trajectory with parsed_samples added to each step
    """
    for step in trajectory["steps"]:
        if "sampling" not in step:
            continue
        ms = config.get("max_samples", len(step["sampling"]["raw_samples"]))
        logger.debug(f"+++ Processing {ms} samples out of {len(step['sampling']['raw_samples'])} available samples")
        response_samples = step["sampling"]["raw_samples"][:ms]

        agent_config = get_agent_config(step["name"], config)
        if agent_config == {}:
            logger.debug(f"+++ No configuration found for agent {step['name']}")
            continue

        if agent_config["response_type"] in ["text"]:
            logger.debug(
                f"+++ Not parsing text responses for response type {agent_config['response_type']} in agent {agent_config['name']}"
            )
        elif agent_config["response_type"] in ["code", "json", "react", "react_aw", "thought_code", "tool_calls"]:
            parsed_response_list = []

            for response in response_samples:
                if agent_config["response_type"] == "code":
                    parsed_response = parse_code_response(response)
                elif agent_config["response_type"] == "json":
                    parsed_response = parse_json_response(response)
                elif agent_config["response_type"] == "react":
                    parsed_response = parse_react_response(response)
                elif agent_config["response_type"] == "react_aw":
                    parsed_response = parse_react_aw_response(response)
                elif agent_config["response_type"] == "tool_calls":
                    parsed_response = parse_tool_calls_response(response)
                elif agent_config["response_type"] == "thought_code":
                    parsed_response = parse_thought_code_response(response)

                # Handle backfill
                if parsed_response == {} or parsed_response == "":
                    parsed_response = parsed_response_backfill(agent_config)
                elif agent_config["response_type"] in ["json", "react"]:
                    parsed_response = field_backfill(parsed_response, agent_config)

                if parsed_response == {} or parsed_response == "":
                    logger.debug(
                        f"+++ Could not parse response ({response[:50]}) for {step['name']} with response type {agent_config['response_type']}"
                    )
                    continue

                parsed_response_list.append(parsed_response)

            step["sampling"]["parsed_samples"] = parsed_response_list
            if ms < step["sampling"]["num_samples"]:
                step["sampling"]["raw_samples"] = step["sampling"]["raw_samples"][:ms]

            if parsed_response_list == []:
                logger.debug(f"No parsed responses for agent {step['name']}")

            # also parse the actual response in the this
            actual_response = step.get("raw_response", "")
            if agent_config["response_type"] == "code":
                parsed_response = parse_code_response(actual_response)
            elif agent_config["response_type"] == "json":
                parsed_response = parse_json_response(actual_response)
            elif agent_config["response_type"] == "react_aw":
                parsed_response = parse_react_aw_response(actual_response)
            elif agent_config["response_type"] == "react":
                parsed_response = parse_react_response(actual_response)
            elif agent_config["response_type"] == "thought_code":
                parsed_response = parse_thought_code_response(actual_response)
            elif agent_config["response_type"] == "tool_calls":
                parsed_response = parse_tool_calls_response(actual_response)
            step["parsed_response"] = parsed_response
        else:
            logger.debug(f"+++ Cannot parse responses for response type {agent_config['response_type']}")

    return trajectory
