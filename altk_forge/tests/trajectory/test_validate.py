import pytest

from altk_forge.trajectory import validate
from altk_forge.trajectory.spans import walk


def test_valid_trajectory_has_no_problems(trajectory):
    assert list(validate.validator_for(trajectory).iter_errors(trajectory)) == []
    assert validate.check(trajectory, {"trace-1": {s["span_id"] for s in walk(trajectory["tree"])}}) == []


def test_check_reports_dangling_references(trajectory):
    trajectory["tree"]["children"][0]["children"][1]["triggered_by"] = "nowhere"
    problems = validate.check(trajectory, {"trace-1": {s["span_id"] for s in walk(trajectory["tree"])}})
    assert any("span nowhere not found" in p for p in problems)


def test_structure_problems():
    assert validate.structure_problems({"kind": "turn", "children": [{"kind": "agent", "span_id": "a"}]}) == []
    problems = validate.structure_problems({"kind": "agent", "children": [{"kind": "llm", "span_id": "x"}]})
    assert len(problems) == 2
    nested = {"kind": "turn", "children": [{"kind": "agent", "span_id": "a", "children": [{"kind": "turn", "span_id": "t"}]}]}
    assert "turn span(s) inside the turn" in validate.structure_problems(nested)[0]


def test_check_reports_a_step_of_another_agent(trajectory):
    trajectory["tree"]["children"][0]["children"][0]["step"] = "other_agent.answer"
    problems = validate.check(trajectory, {})
    assert any("is not under agent sql_agent" in p for p in problems)


def node(name, kind="CHAIN", graph_node=None):
    return {"type": kind, "name": name, "metadata": {"langgraph_node": name if graph_node is None else graph_node}}


@pytest.mark.parametrize(
    "observation, agent",
    [
        (node("sql_agent"), "sql_agent"),
        (node("text-to-sql"), "text-to-sql"),
        (node("supervisor", kind="AGENT"), "supervisor"),
        (node("__start__"), None),  # the graph's own entry and exit
        (node("__end__"), None),
        (node("PatchToolCallsMiddleware.before_agent"), None),  # a hook named like a method: no agent_id
        (node("has space"), None),
        (node("RunnableSequence", graph_node="sql_agent"), None),  # plumbing inside a node
        (node("run_sql", kind="TOOL"), None),
    ],
)
def test_graph_agent_node(observation, agent):
    from altk_forge.trajectory.validate import graph_agent_node

    assert graph_agent_node(observation) == agent


def test_every_graph_agent_name_is_a_valid_agent_id():
    # The rule a converter is held to and the schema it is validated against must never disagree: whatever
    # graph_agent_node calls an agent passes the schema's agent_id pattern.
    from altk_forge.trajectory.validate import AGENT_ID, TRAJECTORY

    assert AGENT_ID.pattern == TRAJECTORY["$defs"]["Span"]["properties"]["agent_id"]["pattern"]


def llm(span_id, step):
    return {"span_id": span_id, "kind": "llm", "step": step, "input": [], "output": None}


def nested(step):
    """A call in an agent nested in another agent (a LangChain agent inside a LangGraph node)."""
    inner = {"span_id": "inner", "kind": "agent", "agent_id": "inner", "children": [llm("c", step)]}
    outer = {"span_id": "outer", "kind": "agent", "agent_id": "outer", "children": [llm("o", "outer.route"), inner]}
    return {"span_id": "turn", "kind": "turn", "children": [outer]}


def test_a_call_belongs_to_its_nearest_agent_only():
    # Before, a call in a nested agent had to start with every agent above it at once: no step could pass.
    assert validate.steps_off_their_agent(nested("inner.answer")) == []


def test_a_step_of_another_agent_names_the_fix():
    problems = validate.steps_off_their_agent(nested("RunnableParallel<raw>.tool_call"))
    assert problems == [
        "c: step RunnableParallel<raw>.tool_call is not under agent inner: an llm span's step starts with the "
        "agent_id of the agent span it is in (inner.tool_call)"
    ]
