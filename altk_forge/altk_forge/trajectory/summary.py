"""One-line summary of a trajectory for a pipeline's trajectories/index.json (the list the viewer shows)."""

from datetime import datetime

from .guidelines import guideline_count
from .spans import walk


def summary(doc, file_name):
    spans = list(walk(doc["tree"]))
    llms = [x for x in spans if x["kind"] == "llm"]
    usage = [x["usage"] for x in llms if x.get("usage")]
    # Turn-level scores only; range and direction let the list compare metrics with different scales.
    scores = {
        s["metric"]: {
            "value": s.get("value"),
            "label": s.get("label"),
            "range": s.get("range"),
            "higher_is_better": s.get("higher_is_better", True),
        }
        for s in doc.get("scores") or []
        if not s.get("target")
    }
    return {
        "trace_id": doc["trace_id"],
        "file": file_name,
        "timestamp": doc.get("timestamp"),
        "question": doc["tree"].get("input"),
        "agent_id": doc.get("agent_id"),
        "session_id": (doc.get("session") or {}).get("id"),
        "turn_index": (doc.get("session") or {}).get("turn_index"),
        "task_type": (doc.get("task") or {}).get("type"),
        "task_name": (doc.get("task") or {}).get("name"),
        "tags": (doc.get("source") or {}).get("tags") or [],
        "agents": [x["agent_id"] for x in spans if x["kind"] == "agent"],
        "llm_calls": len(llms),
        "tool_calls": len([x for x in spans if x["kind"] in ("tool", "retriever")]),
        "errors": len([x for x in spans if x.get("error")]),
        "warnings": len([x for x in spans if x.get("outcome")]),
        "retries": len([x for x in llms if (x.get("outcome") or {}).get("type") == "discarded"]),
        "findings": len(doc.get("findings") or []),
        "guidelines": sum(guideline_count(x) for x in llms),
        "guided_calls": sum(1 for x in llms if guideline_count(x)),
        "total_tokens": sum(u.get("total_tokens") or 0 for u in usage),
        "duration_ms": duration_ms(doc["tree"]),
        "scores": scores,
    }


def duration_ms(span):
    if not span.get("start_time") or not span.get("end_time"):
        return None

    def parse(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    return round((parse(span["end_time"]) - parse(span["start_time"])).total_seconds() * 1000)
