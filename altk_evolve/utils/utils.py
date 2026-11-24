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


# One JSON string literal, escapes consumed so an escaped quote does not end it early.
_JSON_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)

# Escapes that turn a *single* backslash into a C0 control character. These are the ones a
# model may have meant as a literal backslash. \uXXXX is excluded on purpose: see below.
_AMBIGUOUS_CONTROL_ESCAPES = frozenset("bfnrt")


def literals_with_ambiguous_control_escapes(text: str) -> list[str]:
    """String literals in ``text`` carrying a ``\\b``, ``\\f``, ``\\n``, ``\\r`` or ``\\t``.

    Those are the escapes a model may have meant as a *literal backslash* — a Windows path
    or a regex — in a response it demonstrably failed to escape properly. Decoding one
    anyway yields a guideline that looks valid but whose text is silently wrong, which is
    then embedded and served on.

    Two things this deliberately does not flag:

    - ``\\uXXXX``, even where it encodes the same character. A model writing ``\\u0009``
      spelled a control character out on purpose; that is not what failing to escape a path
      looks like. Only the single-backslash forms are ambiguous.
    - A correctly escaped ``\\\\`` followed by ``t``, which decodes to a backslash and a
      ``t``, not a tab. The scan consumes ``\\\\`` as one escape, so it is not misread.

    Scanning *literals* is what makes both of those possible, and is why neither the decoded
    values nor the whole document is consulted. A decoded value cannot distinguish a tab that
    came from ``\\t`` from one that came from ``\\u0009``, nor from one the model typed as a
    path separator — the escape that corrupts consumes its own backslash, so nothing survives
    in the value to key on. And a pretty-printed document's newlines sit *between* tokens,
    never inside a literal, so a document-wide character scan would whitelist ``\\n`` for
    every value and let exactly this corruption through.

    What this cannot do is tell an intended ``\\t`` from a misread one: ``"name\\tvalue"``
    and ``"C:\\temp"`` are the same shape, one valid escape the repair never touches. Since
    the two are indistinguishable, the caller fails closed on the whole response — a
    discarded response is regenerable, whereas corrupted text served into the store is not.

    Splitting into literals is for the *log message* — so the warning can name the literal an
    operator has to look at — not for correctness. Valid JSON cannot carry a backslash outside
    a string, so scanning the whole document for the same escapes would reach the identical
    verdict; no test distinguishes the two, and none can.

    Returns the offending literals, for logging. Empty means nothing ambiguous was found.
    """
    offenders = []
    for literal in _JSON_STRING_LITERAL_RE.findall(text):
        body = literal[1:-1]
        i = 0
        while i < len(body):
            if body[i] != "\\" or i + 1 >= len(body):
                i += 1
                continue
            if body[i + 1] in _AMBIGUOUS_CONTROL_ESCAPES:
                offenders.append(literal)
                break
            # Consume the whole escape, so the second half of \\ is not read as a new one.
            i += 2
    return offenders
