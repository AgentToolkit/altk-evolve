import json
import re
from json import JSONDecodeError


def serialize_content(content: str | list | dict) -> str:
    """Serialize content to a string for storage."""
    if isinstance(content, str):
        return content
    return json.dumps(content)


def deserialize_content(content: str):
    """Deserialize content from storage."""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return content


def clean_llm_response(content: str) -> str:
    """
    Removes common junk from an LLM response so that it can be parsed using `json.loads()`

    Actions:
    - Returns the inner content of a Markdown code block.
    - If Markdown code blocks are not present, remove thought and reasoning blocks entirely.
    - Extracts an embedded JSON object/array when the provider adds text around it.
    """
    cleaned = re.sub(
        r"<(?:think(?:ing)?|reflection)>.*?</(?:think(?:ing)?|reflection)>",
        "",
        content.strip(),
        flags=re.DOTALL,
    ).strip()

    json_payload = _extract_json_payload(cleaned)
    return json_payload if json_payload is not None else cleaned


def _extract_json_payload(content: str) -> str | None:
    decoder = json.JSONDecoder()

    for match in re.finditer(
        r"```[a-zA-Z0-9_-]*\s*\n(.*?)```",
        content,
        flags=re.MULTILINE | re.DOTALL,
    ):
        payload = _decode_json_fragment(match.group(1).strip(), decoder)
        if payload is not None:
            return payload

    payload = _decode_json_fragment(content, decoder)
    if payload is not None:
        return payload

    for index, char in enumerate(content):
        if char not in "{[":
            continue
        payload = _decode_json_fragment(content[index:], decoder)
        if payload is not None:
            return payload

    return None


def _decode_json_fragment(fragment: str, decoder: json.JSONDecoder) -> str | None:
    fragment = fragment.strip()
    try:
        _, end = decoder.raw_decode(fragment)
    except JSONDecodeError:
        return None

    return fragment[:end].strip()
