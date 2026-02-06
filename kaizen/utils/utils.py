import re


def clean_llm_response(content: str) -> str:
    """
    Removes common junk from an LLM response so that it can be parsed using `json.loads()`

    Actions:
    - Returns the inner content of a Markdown code block.
    - If Markdown code blocks are not present, remove thought and reasoning blocks entirely.
    """
    pattern = r"^```[a-zA-Z0-9]*\n(.*?)\n```$"
    match = re.match(pattern, content.strip(), flags=re.MULTILINE | re.DOTALL)
    match_res = match.group(1).strip() if match else content.strip()
    return re.sub(r"<(?:think(?:ing)?|reflection)>.*?</(?:think(?:ing)?|reflection)>", "", match_res, flags=re.DOTALL).strip()
