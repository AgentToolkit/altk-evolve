from altk_forge.converters.checks import describe_error, fidelity_problems, first_differences, problems_of
from altk_forge.trajectory.spans import walk


def test_first_differences():
    diffs = first_differences({"a": 1, "b": [1, 2], "c": "x"}, {"a": 2, "b": [1], "d": 1})
    assert diffs == [".a: was 1, now 2", ".b: 2 items, now 1", ".c: removed", ".d: added 1"]


def test_describe_error_names_the_missing_field():
    from altk_forge.trajectory.validate import validator_for

    doc = {"schema_version": "2.0", "trace_id": "t", "source": {"kind": "file"}, "tree": {"kind": "turn"}}
    errors = [describe_error(e) for e in validator_for(doc).iter_errors(doc)]
    assert any("missing required property 'span_id'" in e for e in errors)


def test_problems_of_and_fidelity(trajectory, langfuse_trace):
    assert problems_of(trajectory) == []
    assert problems_of([]) == ["convert() returned list, not a trajectory object"]
    assert fidelity_problems(langfuse_trace, trajectory) == []
    # Drop the tool span: the trajectory is still valid, but not faithful to the trace.
    agent = trajectory["tree"]["children"][0]
    agent["children"] = [s for s in agent["children"] if s["kind"] != "tool"]
    assert any("TOOL run_sql (observation tool-1) has no span" in p for p in fidelity_problems(langfuse_trace, trajectory))


def test_fidelity_refuses_an_agent_id_taken_from_the_trace_id():
    doc = {"tree": {"span_id": "t", "kind": "turn", "children": [{"span_id": "a", "kind": "agent", "agent_id": "task_17"}]}}
    assert any("comes from this trace's id" in p for p in fidelity_problems({"id": "run-task_17"}, doc))


def with_middleware(trace):
    """The trace with the hooks a LangChain agent's middleware adds: LangGraph nodes named like a method, which ran
    before the agent and hold no LLM call of their own."""
    for i, name in enumerate(("PatchToolCallsMiddleware.before_agent", "SkillsMiddleware.before_agent")):
        trace["observations"].append(
            {
                "id": f"mw-{i}",
                "type": "CHAIN",
                "name": name,
                "parentObservationId": "graph",
                "startTime": "2026-01-01T00:00:00.500Z",
                "endTime": "2026-01-01T00:00:00.600Z",
                "metadata": {"langgraph_node": name},
            }
        )
    return trace


def test_middleware_nodes_are_not_required_as_agents(langfuse_trace):
    # Before, every LangGraph node had to be an agent span with the node's name as agent_id, which the schema
    # forbids for a name with a dot: no converter could pass both checks, and every LLM attempt was wasted.
    from altk_forge.builtin.langfuse import convert

    trace = with_middleware(langfuse_trace)
    doc = convert(trace)
    assert problems_of(doc) == []
    assert fidelity_problems(trace, doc) == []
    assert {s["agent_id"] for s in walk(doc["tree"]) if s["kind"] == "agent"} == {"sql_agent"}


def test_a_real_agent_node_is_still_required(langfuse_trace):
    from altk_forge.builtin.langfuse import convert

    doc = convert(langfuse_trace)
    doc["tree"]["children"][0]["agent_id"] = "other"  # the node sql_agent has no agent span any more
    assert any("LangGraph node sql_agent ran in the trace but is no agent span" in p for p in fidelity_problems(langfuse_trace, doc))
