from altk_forge.builtin import messages as messages_to_trajectory
from altk_forge.trajectory import validate
from altk_forge.trajectory.spans import walk


def test_messages_to_trajectory_is_valid():
    flat = {
        "trace_id": "flat-1",
        "source": {"kind": "mcp"},
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "weather", "arguments": '{"city": "Oslo"}'}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "sunny"},
            {"role": "assistant", "content": "It is sunny."},
        ],
    }
    doc = messages_to_trajectory.convert(flat, "helper")
    assert list(validate.validator_for(doc).iter_errors(doc)) == []
    assert validate.check(doc, {"flat-1": {s["span_id"] for s in walk(doc["tree"])}}) == []
    kids = doc["tree"]["children"][0]["children"]
    assert [(k["kind"], k.get("step")) for k in kids] == [("llm", "helper.tool_call"), ("tool", None), ("llm", "helper.answer")]
    assert kids[1]["triggered_by"] == "llm_1" and kids[1]["input"] == {"city": "Oslo"}
