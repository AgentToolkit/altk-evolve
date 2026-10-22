"""Helpers the tests import (conftest.py holds the fixtures)."""

# A converter as the LLM would write it, for traces like {"id", "question", "answer"} or a folder's envelope of one.
SIMPLE_CONVERTER = """
def convert(trace, context):
    body = trace.get("content") if isinstance(trace.get("content"), dict) else trace
    answer = str(body["answer"])
    return {
        "schema_version": "2.0",
        "trace_id": str(trace["id"]),
        "source": {"kind": "file"},
        "tree": {"span_id": "turn", "kind": "turn", "input": body["question"], "output": answer,
                 "children": [{"span_id": "agent", "kind": "agent", "agent_id": "qa",
                               "start_time": "2026-01-01T00:00:00Z", "input": body["question"], "output": answer}]}
    }
"""


def answer_with(code):
    """What a chat() stub returns: an answer holding `code`, and the usage."""
    return f"```python\n{code}```", {"model": "stub", "prompt_tokens": 1, "completion_tokens": 1}


def qa(trace_id, **extra):
    """A trace SIMPLE_CONVERTER converts: a question and its answer."""
    return {"id": trace_id, "question": "Q?", "answer": "A", **extra}
