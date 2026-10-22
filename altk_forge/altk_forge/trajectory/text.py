"""Text helpers for showing trajectory content: JSON in strings, clipping, the readable text of an answer."""

import json
import re
from html.parser import HTMLParser

CLIP = 2000  # altk-evolve truncates each step to 2000 characters as well


def try_json(s):
    if not isinstance(s, str):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def clip(text, n=CLIP):
    text = "" if text is None else str(text)
    return f"{text[:n]}… [{len(text) - n:,} more characters]" if len(text) > n else text


def msg_text(m):
    c = m.get("content")
    return c if isinstance(c, str) else "" if c is None else json.dumps(c, ensure_ascii=False)


class _Text(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "br", "tr"}

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("• ")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def readable(s):
    """Text a reader sees, from an HTML, Markdown or JSON answer."""
    if not s:
        return ""
    s = str(s)
    j = try_json(s)
    if isinstance(j, dict):
        a = j.get("answer")
        if isinstance(a, str):
            return a
        if isinstance(a, list):
            return "\n".join(x if isinstance(x, str) else str(x.get("data") or x.get("text") or "") for x in a)
        if isinstance(j.get("error"), str):
            return f"Error: {j['error']}"
        if isinstance(j.get("introduction"), str):
            return "\n\n".join(x for x in (j.get("introduction"), j.get("body"), j.get("conclusion")) if x)
        return json.dumps(j, indent=2, ensure_ascii=False)
    if not re.search(r"<[a-z][^>]*>", s, re.I):
        return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"^#{1,6}\s+", "", s, flags=re.M)).strip()
    p = _Text()
    p.feed(s)
    text = re.sub(r"[ \t]+\n", "\n", "".join(p.parts))
    return re.sub(r"\n{3,}", "\n\n", text).strip()
