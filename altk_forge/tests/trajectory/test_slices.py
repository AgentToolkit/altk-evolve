from altk_forge.trajectory import slices


def by_span(out):
    return {x["span_id"]: x for x in out}


def test_one_slice_per_llm_call(trajectory):
    out = slices.slices(trajectory, "p1")
    assert [x["span_id"] for x in out] == ["llm-1", "llm-2"]
    assert [x["num"] for x in out] == [1, 3]


def test_namespace_comes_from_the_agent_then_from_forge(trajectory):
    out = by_span(slices.slices(trajectory, "p1"))
    assert out["llm-1"]["namespace_id"] == "bot__sql_agent__tool_call"
    assert out["llm-1"]["extra"]["namespace_source"] == "agent"
    assert out["llm-2"]["namespace_id"] == "p1__sql_agent__answer"
    assert out["llm-2"]["extra"]["namespace_source"] == "forge"


def test_a_single_namespace_overrides_everything(trajectory):
    out = slices.slices(trajectory, "p1", namespace="everything")
    assert {x["namespace_id"] for x in out} == {"everything"}
    assert {x["extra"]["namespace_source"] for x in out} == {"forge"}


def test_labels_and_checks(trajectory):
    out = by_span(slices.slices(trajectory, "p1"))
    assert out["llm-1"]["checks"] == [{"ok": True, "text": "called run_sql, as expected"}]
    assert out["llm-1"]["label"] == "unknown"  # no turn score judges tool calls here
    assert out["llm-2"]["label"] == "success"
    assert out["llm-2"]["trajectory"]["outcome"]["success"] is True


def test_a_failed_call_is_a_failure_whatever_the_score(trajectory):
    llm = trajectory["tree"]["children"][0]["children"][2]
    llm["error"] = {"type": "llm_error", "message": "boom", "code": "HTTP 500"}
    out = by_span(slices.slices(trajectory, "p1"))["llm-2"]
    assert out["label"] == "failure"
    assert out["problem"].startswith("The call failed (llm call failed, HTTP 500)")


def test_the_slice_carries_review_guidelines_and_metadata(trajectory):
    out = by_span(slices.slices(trajectory, "p1"))["llm-2"]
    review = out["messages"][-1]
    assert review["review"] is True
    assert "Use COUNT(*) to count rows" in review["content"]
    assert "Expected answer: 42 users" in review["content"]
    t = out["trajectory"]
    assert all("review" not in m for m in t["messages"])
    assert t["trace_id"] == "trace-1:llm-2"
    assert t["metadata"]["pipeline"] == "p1" and t["metadata"]["source"] == "forge"
    assert t["batch"]["conversation_id"] == "trace-1" and len(t["batch"]["revision"]) == 16
    assert out["extra"]["guidelines_given"] == ["Use COUNT(*) to count rows", "Quote identifiers"]


def test_the_revision_follows_the_content(trajectory):
    first = slices.slices(trajectory, "p1")[1]["trajectory"]["batch"]["revision"]
    assert slices.slices(trajectory, "p1")[1]["trajectory"]["batch"]["revision"] == first
    trajectory["tree"]["output"] = "41 users"
    assert slices.slices(trajectory, "p1")[1]["trajectory"]["batch"]["revision"] != first


def test_tool_results_follow_the_tool_call(trajectory):
    msgs = by_span(slices.slices(trajectory, "p1"))["llm-1"]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[2] == {"role": "tool", "tool_call_id": "call1", "content": '[{"n": 42}]'}


def test_namespace_of():
    assert slices.namespace_of("My Pipe", "SQL-Agent", "answer") == "my_pipe__sql_agent__answer"
    assert slices.namespace_of(None, None, "x") == "pipeline__unknown__x"
    assert slices.namespace_of("1st", "a", "b") == "n_1st__a__b"


def test_norm():
    assert slices.norm({"value": 50, "range": [0, 100]}) == 0.5
    assert slices.norm({"value": 0.2, "higher_is_better": False}) == 0.8
    assert slices.norm({"value": None, "label": "ok"}) is None
    assert slices.norm({"value": 150, "range": [0, 100]}) == 1.0
