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
