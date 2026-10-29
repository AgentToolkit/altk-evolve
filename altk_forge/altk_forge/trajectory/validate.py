"""Validate trajectory and session documents against the schemas and check that references resolve.

A file with `turns` is validated as a session; its turns are looked up next to it (*.trajectory.json).
The command line is `python -m altk_forge.cli.validate`."""

import json
import re
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

from .spans import walk

SCHEMAS = Path(__file__).resolve().parent / "schema"
TRAJECTORY = json.loads((SCHEMAS / "trajectory.schema.json").read_text())
SESSION = json.loads((SCHEMAS / "session.schema.json").read_text())
REGISTRY = Registry().with_resources(
    [
        (TRAJECTORY["$id"], Resource.from_contents(TRAJECTORY)),
        ("trajectory.schema.json", Resource.from_contents(TRAJECTORY)),
    ]
)


# What an agent_id may be: the schema's own pattern, so the rules below never disagree with the schema.
AGENT_ID = re.compile(TRAJECTORY["$defs"]["Span"]["properties"]["agent_id"]["pattern"])
# LangGraph's entry and exit nodes run in every graph; they are the graph's, not an agent.
GRAPH_INTERNAL_NODES = {"__start__", "__end__"}


def is_agent_name(name):
    """Whether `name` can be an agent_id."""
    return isinstance(name, str) and bool(AGENT_ID.match(name))


def graph_agent_node(observation):
    """The agent a LangGraph node of a trace is, or None when the observation is no such node. A node is an agent
    when its name can be an agent_id: the graph's own __start__ / __end__ are not, and neither are framework hooks
    named like a method (PatchToolCallsMiddleware.before_agent, the middleware of a LangChain agent), which run
    around an agent rather than being one."""
    name = observation.get("name")
    if observation.get("type") not in ("CHAIN", "AGENT") or name != (observation.get("metadata") or {}).get("langgraph_node"):
        return None
    return name if name not in GRAPH_INTERNAL_NODES and is_agent_name(name) else None


def refs(doc):
    """(where, trace_id or None, span_id) for every span reference in the document."""
    for x in walk(doc["tree"]) if doc.get("tree") else []:
        if x.get("triggered_by"):
            yield f"{x['span_id']}.triggered_by", None, x["triggered_by"]
        for m in x["input"] if x["kind"] == "llm" else []:
            for sid in m.get("from", []):
                yield f"{x['span_id']}.input.from", None, sid
    for f in doc.get("findings") or []:
        for r in f["symptom"] + f.get("cause", []):
            yield f"findings.{f['id']}", r.get("trace_id"), r["span_id"]
    for g in (doc.get("expected") or {}).get("guidelines", []):
        for r in g.get("evidence", []):
            yield "expected.guidelines", r.get("trace_id"), r["span_id"]
    for s in doc.get("scores") or []:
        if (s.get("target") or {}).get("span_id"):
            yield f"scores.{s['metric']}", None, s["target"]["span_id"]
        for r in s.get("evidence", []):
            yield f"scores.{s['metric']}.evidence", r.get("trace_id"), r["span_id"]


def structure_problems(tree):
    """The shape every trajectory has, whatever its source: one turn at the root and nowhere else, agents directly
    under it, every other span inside an agent."""
    out = []
    if tree.get("kind") != "turn":
        out.append(f"tree is a {tree.get('kind')} span; the root is the one turn span")
    for child in tree.get("children", []):
        if child.get("kind") != "agent":
            out.append(
                f"{child.get('span_id')}: a {child.get('kind')} span directly under the turn; the turn's "
                "children are agent spans, and llm, tool, retriever and memory spans go inside an agent"
            )
    nested = [x.get("span_id") for c in tree.get("children", []) for x in walk(c) if x.get("kind") == "turn"]
    if nested:
        out.append(
            f"{len(nested)} turn span(s) inside the turn ({', '.join(map(str, nested[:3]))}"
            f"{', …' if len(nested) > 3 else ''}): a trajectory has exactly one turn, its root; a conversation "
            "of several exchanges is still one turn"
        )
    return out


def check(doc, traces):
    problems = []
    for where, trace_id, span_id in refs(doc):
        trace_id = trace_id or doc.get("trace_id")
        if trace_id in traces and span_id not in traces[trace_id]:
            problems.append(f"{where}: span {span_id} not found in trace {trace_id}")
    if doc.get("tree"):
        problems += structure_problems(doc["tree"])
        agent_ids = {a["agent_id"] for a in doc.get("agents") or []}
        spans = list(walk(doc["tree"]))
        for x in spans:
            if x["kind"] == "agent" and agent_ids and x["agent_id"] not in agent_ids:
                problems.append(f"{x['span_id']}: agent {x['agent_id']} not in agents")
        problems += steps_off_their_agent(doc["tree"])
    return problems


def steps_off_their_agent(span, agent=None):
    """An llm span's step is `<agent_id>.<purpose>` of the agent it runs in: the nearest agent span above it. A call
    in an agent nested in another belongs to the inner one only."""
    out = []
    if span.get("kind") == "agent":
        agent = span.get("agent_id")
    elif span.get("kind") == "llm" and agent is not None and str(span.get("step")).split(".")[0] != agent:
        step = str(span.get("step"))
        purpose = step.split(".")[1] if "." in step else "<purpose>"
        out.append(
            f"{span.get('span_id')}: step {step} is not under agent {agent}: an llm span's step starts with the "
            f"agent_id of the agent span it is in ({agent}.{purpose})"
        )
    for child in span.get("children") or []:
        out += steps_off_their_agent(child, agent)
    return out


def validator_for(doc):
    return jsonschema.Draft202012Validator(SESSION if "turns" in doc else TRAJECTORY, registry=REGISTRY)


def span_ids(doc):
    """{trace_id: the span ids of the trajectory}, what check() resolves references against."""
    return {doc["trace_id"]: {x["span_id"] for x in walk(doc["tree"])}} if doc.get("tree") else {}
