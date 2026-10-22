from altk_forge.trajectory.summary import summary


def test_summary(trajectory):
    row = summary(trajectory, "trace-1.trajectory.json")
    assert row["trace_id"] == "trace-1"
    assert row["question"] == "How many users?"
    assert row["agents"] == ["sql_agent"]
    assert (row["llm_calls"], row["tool_calls"], row["errors"]) == (2, 1, 0)
    assert row["total_tokens"] == 48
    assert row["duration_ms"] == 10_000
    assert row["guidelines"] == 4 and row["guided_calls"] == 2
    assert row["scores"]["correctness"]["value"] == 100
