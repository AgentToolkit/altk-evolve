import json

from altk_forge.builtin import langfuse as L
from altk_forge.builtin.files import convert_one
from altk_forge.trajectory.spans import walk


def spans(doc):
    return {s["span_id"]: s for s in walk(doc["tree"])}


def test_converts_a_langgraph_trace_into_a_valid_trajectory(langfuse_trace):
    doc, problems = convert_one(langfuse_trace, "http://langfuse.test")
    assert problems == []
    assert doc["schema_version"] == "2.0"
    assert doc["trace_id"] == "trace-1"
    assert doc["agent_id"] == "sql_bot"
    assert doc["source"]["uri"] == "http://langfuse.test/project/proj/traces/trace-1"
    assert doc["tree"]["kind"] == "turn"
    assert doc["tree"]["input"] == "How many users?"
    assert doc["tree"]["output"] == "42 users"


def test_keeps_agents_llm_calls_and_tools(trajectory):
    s = spans(trajectory)
    assert s["agent-1"]["kind"] == "agent" and s["agent-1"]["agent_id"] == "sql_agent"
    assert s["llm-1"]["kind"] == "llm" and s["llm-2"]["kind"] == "llm"
    assert s["tool-1"]["kind"] == "tool" and s["tool-1"]["name"] == "run_sql"
    assert trajectory["agents"] == [{"agent_id": "sql_agent", "role": "specialist", "tools": ["run_sql"]}]


def test_names_steps_and_links_tool_to_its_caller(trajectory):
    s = spans(trajectory)
    assert s["llm-1"]["step"] == "sql_agent.tool_call"
    assert s["llm-2"]["step"] == "sql_agent.answer"
    assert s["tool-1"]["triggered_by"] == "llm-1"


def test_labels_where_each_input_message_came_from(trajectory):
    s = spans(trajectory)
    assert [m["source"] for m in s["llm-1"]["input"]] == ["system_prompt", "user"]
    sources = {m["source"]: m.get("from") for m in s["llm-2"]["input"]}
    assert sources["tool_result"] == ["tool-1"]
    assert sources["previous_step"] == ["llm-1"]


def test_tool_schemas_logged_as_messages_become_tools(trajectory):
    llm = spans(trajectory)["llm-1"]
    assert [t["function"]["name"] for t in llm["tools"]] == ["run_sql"]
    assert all(m["role"] != "tool" for m in llm["input"])
    assert llm["output"]["content"] is None
    assert json.loads(llm["output"]["tool_calls"][0]["function"]["arguments"]) == {"q": "select count(*) from users"}


def test_records_the_memory_contract_of_a_call(trajectory):
    assert spans(trajectory)["llm-1"]["memory"] == {
        "namespace": "bot__sql_agent__tool_call",
        "step": "sql_agent.tool_call",
        "query": "count users",
        "guideline_ids": ["7", "8"],
        "enabled": True,
    }
    assert "memory" not in spans(trajectory)["llm-2"]


def test_expected_and_scores(trajectory):
    assert trajectory["expected"]["final_answer"] == "42 users"
    assert trajectory["expected"]["tool_calls"]["calls"] == [{"name": "run_sql"}]
    assert trajectory["scores"][0]["metric"] == "correctness"
    assert trajectory["scores"][0]["range"] == [0, 100]


def test_an_empty_tool_result_is_an_outcome_not_an_error(langfuse_trace):
    langfuse_trace["observations"][3]["output"] = "No results found"
    tool = spans(L.convert(langfuse_trace))["tool-1"]
    assert tool["outcome"]["type"] == "empty_result"
    assert "error" not in tool


def test_a_failing_tool_is_an_error(langfuse_trace):
    langfuse_trace["observations"][3]["output"] = '{"status": "error", "error": "SQL0204N table not found"}'
    tool = spans(L.convert(langfuse_trace))["tool-1"]
    assert tool["error"]["type"] == "tool_error"
    assert tool["error"]["code"] == "SQL0204N"


def test_span_status():
    assert L.span_status({}, "Error: boom")[0]["detected_by"] == "converter"
    assert L.span_status({"level": "ERROR", "statusMessage": "x"}, "")[0]["detected_by"] == "source"
    assert L.span_status({}, '{"status": "no_results"}')[1]["type"] == "empty_result"
    assert L.span_status({}, "SQL0206N column not valid")[0]["type"] == "invalid_arguments"
    assert L.span_status({}, "42 rows") == (None, None)


def test_mark_retries_flags_the_discarded_output():
    first = {"span_id": "a", "step": "x.answer", "input": [{"role": "user", "content": "q"}], "output": {"content": "not json"}}
    retry = {
        "span_id": "b",
        "step": "x.answer",
        "input": [{"role": "user", "content": "q"}, {"role": "user", "content": "answer in JSON"}],
        "output": {"content": "{}"},
    }
    L.mark_retries([first, retry])
    assert first["outcome"]["type"] == "discarded"
    assert "not valid JSON" in first["outcome"]["message"]
    assert retry["triggered_by"] == "a"


def test_schema_violation_names_the_enum_field():
    fmt = {"name": "Route", "schema": {"type": "object", "properties": {"next": {"enum": ["a", "b"]}}}}
    assert "next" in L.schema_violation({"content": '{"next": "c"}'}, fmt)
    assert L.schema_violation({"content": '{"next": "a"}'}, fmt) is None


def test_prune_keeps_null_content_and_empty_output():
    assert L.prune({"a": None, "b": [], "content": None, "output": [], "c": {"d": {}}}) == {"content": None, "output": [], "c": {}}
