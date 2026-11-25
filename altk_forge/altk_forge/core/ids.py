"""Readable ids for the objects Forge stores (integrations, filters, pipelines)."""

import re


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48] or "item"


def unique_id(base, taken):
    base, n = slug(base), 2
    candidate = base
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate
