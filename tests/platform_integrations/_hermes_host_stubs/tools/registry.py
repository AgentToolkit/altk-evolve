"""Stub of hermes-agent's tools/registry.py — matches the real return shape."""

import json


def tool_error(message, **extra):
    return json.dumps({"error": message, **extra}, ensure_ascii=False)
