"""Whether a trajectory a converter produced is acceptable: valid against the schema, and faithful to the trace.

A trajectory can be valid and still flatten the graph away. `fidelity_problems` checks that every LangGraph node is
an agent span, every generation an llm span, every tool / retriever a span of that name, and every span has its
start time. The messages are written for the model that wrote the code, so it can fix it."""

import json
import re

from ..trajectory.spans import walk
from ..trajectory.validate import check, span_ids, validator_for
from .schema import MAX_DEPTH

GRAPH_INTERNAL_NODES = {"__start__", "__end__"}


CHAT_ROLES = {"system", "user", "human", "assistant", "ai", "tool"}


def problems_of(doc):
    """Schema and reference problems of a trajectory; empty when it is valid."""

    if not isinstance(doc, dict):
        return [f"convert() returned {type(doc).__name__}, not a trajectory object"]
    out = [describe_error(e) for e in validator_for(doc).iter_errors(doc)]
    if not out:
        out += check(doc, span_ids(doc))
    return out


def _id_tokens(value):
    return [t for t in re.split(r"[^a-z0-9]+", str(value or "").lower()) if t]


def _contains(tokens, part):
    """Whether `part` (tokens) is a run of consecutive tokens of `tokens`."""
    n = len(part)
    return bool(n) and any(tokens[i : i + n] == part for i in range(len(tokens) - n + 1))


def llm_messages_in(value, depth=0):
    """How many lists of chat messages (dicts with a role of a chat and some content) the trace holds: the inputs of
    LLM calls, whatever the format around them."""
    if depth > MAX_DEPTH:
        return 0
    if isinstance(value, dict):
        return sum(llm_messages_in(v, depth + 1) for v in value.values())
    if not isinstance(value, list):
        return 0
    msgs = [
        m
        for m in value
        if isinstance(m, dict)
        and str(m.get("role") or m.get("type") or "").lower() in CHAT_ROLES
        and any(k in m for k in ("content", "value", "text"))
    ]
    if len(msgs) >= 2 and len(msgs) == len(value) and {str(m.get("role") or m.get("type")).lower() for m in msgs} - {"tool"}:
        return 1
    return sum(llm_messages_in(v, depth + 1) for v in value)


def fidelity_problems(trace, doc):
    """Where a valid trajectory misrepresents the trace it came from (for traces with Langfuse-style observations):
    a LangGraph node that is not an agent span, a generation without its llm span, a tool or retriever without its
    span (span ids are observation ids), a span without its start time. Empty when nothing is missing."""
    if not isinstance(doc, dict) or not isinstance(doc.get("tree"), dict):
        return []

    spans = list(walk(doc["tree"]))
    out = []
    calls = llm_messages_in(trace)
    if calls and not any(s.get("kind") == "llm" for s in spans):
        out.append(
            f"the trace holds the messages of {calls} LLM call(s) (lists of role/content messages: system, "
            "user/human, assistant/ai), but the trajectory has no llm span: every LLM call is an llm span with "
            "those messages as its input and the model's answer as its output"
        )
    tokens = _id_tokens(trace.get("id"))
    for a in sorted({s.get("agent_id") for s in spans if s.get("kind") == "agent" and s.get("agent_id")}):
        if _contains(tokens, _id_tokens(a)):
            out.append(
                f"agent_id {a!r} comes from this trace's id {str(trace.get('id'))!r} (a file name, task or run id): "
                "an agent_id names the agent, the same in every trace it ran in, so its memories are shared; take "
                "it from what the trace says about the agent (its name or role in the system prompt, the "
                "framework's agent name), never from the file, task or run"
            )
    obs = [o for o in trace.get("observations") or [] if isinstance(o, dict) and o.get("id")]
    if not obs:
        return out
    by_id = {s.get("span_id"): s for s in spans}
    agents = {s.get("agent_id") for s in spans if s.get("kind") == "agent"}
    nodes = sorted(
        {
            o["name"]
            for o in obs
            if o.get("type") in ("CHAIN", "AGENT")
            and o.get("name") == (o.get("metadata") or {}).get("langgraph_node")
            and o["name"] not in GRAPH_INTERNAL_NODES
        }
    )
    out += [f"LangGraph node {n} ran in the trace but is no agent span (agent_id {n!r})" for n in nodes if n not in agents]
    for o in sorted(obs, key=lambda o: o.get("startTime") or ""):
        kinds = {"GENERATION": {"llm"}, "TOOL": {"tool", "retriever"}, "RETRIEVER": {"retriever"}}.get(o.get("type"))
        if not kinds:
            continue
        s = by_id.get(o["id"])
        if s is None:
            out.append(f"{o['type']} {o.get('name')} (observation {o['id']}) has no span with span_id {o['id']!r}")
        elif s.get("kind") not in kinds:
            out.append(
                f"observation {o['id']} ({o['type']} {o.get('name')}) is a {s.get('kind')} span, expected " + " or ".join(sorted(kinds))
            )
        elif o.get("type") != "GENERATION" and s.get("name") != o.get("name"):
            out.append(f"span {o['id']} is named {s.get('name')!r}, the trace calls it {o.get('name')!r}")
    missing = [s.get("span_id") for s in spans if s.get("kind") != "turn" and not s.get("start_time")]
    if missing:
        out.append(
            f"{len(missing)} span(s) without start_time (take it from the observation's startTime): " + ", ".join(map(str, missing[:5]))
        )
    return out


def _short(value, n=80):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[:n] + "…"


def describe_error(e):
    """One schema error as `path: what is wrong`. For oneOf/anyOf jsonschema's message is the whole failing value
    ("{...} is not valid under any of the given schemas"), which says nothing; the cause is in e.context: the error of
    the variant that got furthest. Messages that quote a value quote it short."""

    def path(err):
        return "/".join(map(str, err.absolute_path)) or "(root)"

    while e.context:
        # The variant that matched deepest is the one that was meant; its first error is the cause.
        e = max(e.context, key=lambda c: (len(c.absolute_path), -len(c.message)))
    if e.validator == "required":
        what = e.message.split(" is a required")[0]
        return f"{path(e)}: missing required property {what}"
    if e.validator == "additionalProperties":
        return f"{path(e)}: {e.message.split(' were unexpected')[0].split('(')[-1].rstrip(')')} not allowed here"
    if e.validator in ("type", "enum", "const", "format", "pattern", "minLength", "minimum", "maximum", "minItems"):
        return f"{path(e)}: {_short(e.instance)} — expected {e.validator} {_short(e.validator_value, 120)}"
    return f"{path(e)}: {e.message if len(e.message) <= 300 else e.message[:150] + ' … ' + e.message[-120:]}"


def first_differences(a, b, path="", out=None, limit=8):
    """Where two JSON values differ, as short lines (for telling the model what its change broke)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append(f"{path or '(root)'}: was {json.dumps(a)[:120]}, now {json.dumps(b)[:120]}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in b:
                out.append(f"{path}.{k}: removed")
            elif k not in a:
                out.append(f"{path}.{k}: added {json.dumps(b[k])[:120]}")
            else:
                first_differences(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items, now {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            first_differences(x, y, f"{path}[{i}]", out, limit)
    elif a != b:
        out.append(f"{path}: was {json.dumps(a)[:120]}, now {json.dumps(b)[:120]}")
    return out[:limit]
