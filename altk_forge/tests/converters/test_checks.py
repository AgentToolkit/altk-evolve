from altk_forge.converters.checks import describe_error, fidelity_problems, first_differences, problems_of


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
