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
        payload, _ = _decode_json_prefix(match.group(1).strip(), decoder)
        if payload is not None:
            return payload

    payload, _ = _decode_json_prefix(content, decoder)
    if payload is not None:
        return payload

    for index, char in enumerate(content):
        if char not in "{[":
            continue
        payload, incomplete = _decode_json_prefix(content[index:], decoder)
        if payload is not None:
            return payload
        if incomplete:
            # This container never closed, so it runs to the end of the input and
            # every later `{`/`[` is nested inside it. Decoding one of those would
            # return a FRAGMENT of the reply while looking like a success: a
            # truncated {"entities": [...]} yields a lone entity object, and a
            # truncated [{"entities": [...]}, ...] yields a complete-looking
            # response holding only the events that arrived. Both hide the
            # truncation from the caller, and the second would be applied.
            # Give up instead, so the caller sees a parse failure and can report
            # the reply as cut short.
            return None

    return None


def _decode_json_prefix(fragment: str, decoder: json.JSONDecoder) -> tuple[str | None, bool]:
    """Decode the JSON value starting at the beginning of *fragment*.

    Returns (payload, incomplete). `incomplete` distinguishes "ran out of input"
    from "not JSON": the decoder either reports a position at the end of the
    input, or names an unterminated construct. Only the former means a later
    fragment would be nested inside something unfinished.
    """
    fragment = fragment.strip()
    if not fragment:
        return None, False

    try:
        _, end = decoder.raw_decode(fragment)
    except JSONDecodeError as error:
        incomplete = error.pos >= len(fragment) or error.msg.startswith("Unterminated")
        return None, incomplete

    return fragment[:end].strip(), False
