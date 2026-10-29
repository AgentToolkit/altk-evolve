"""Guidelines from memory inside a prompt.

The agent appends the guidelines it got from memory to a system prompt under a `# Guidelines from memory` heading;
this finds that section and its items, so the viewer and the slices can show which call received which guidelines."""

import re

# Guidelines the agent got from memory: a section the agent appends to a system prompt.
GUIDELINE_HEAD = re.compile(r"^#{1,6}\s*Guidelines from memory\s*$", re.M | re.I)


def guidelines_in(text):
    """(start, end, intro, [items]) of the guidelines section in a prompt, or None."""
    if not isinstance(text, str):
        return None
    m = GUIDELINE_HEAD.search(text)
    if not m:
        return None
    level = len(m.group(0)) - len(m.group(0).lstrip("#"))
    nxt = re.compile(rf"^#{{1,{level}}}\s", re.M).search(text, m.end())
    end = nxt.start() if nxt else len(text)
    body = text[m.end() : end]
    items, intro = [], []
    for line in body.strip("\n").splitlines():
        if re.match(r"^\s*[-*•]\s+", line):
            items.append(re.sub(r"^\s*[-*•]\s+", "", line).strip())
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
        elif line.strip() and not items:
            intro.append(line.strip())
    return m.start(), end, " ".join(intro), items


def call_guidelines(llm):
    out = []
    for m in llm.get("input") or []:
        g = guidelines_in(m.get("content"))
        if g:
            out += g[3]
    return out


def guideline_count(llm):
    """How many guidelines from memory the prompt of an llm call held."""
    return len(call_guidelines(llm))
