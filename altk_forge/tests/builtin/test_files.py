import json

from altk_forge.builtin.files import build


def test_build_writes_trajectories_and_an_index(tmp_path, langfuse_trace):
    traces, out = tmp_path / "traces", tmp_path / "out"
    traces.mkdir()
    (traces / "trace-1.langfuse.json").write_text(json.dumps(langfuse_trace))
    (traces / "broken.langfuse.json").write_text(json.dumps({"id": "broken", "observations": []}))
    built, skipped, failed = build(traces, out, log=lambda _: None)
    assert (built, skipped) == (1, 0)
    assert [tid for tid, _ in failed] == ["broken"]
    index = json.loads((out / "index.json").read_text())
    assert [row["trace_id"] for row in index] == ["trace-1"]
    # Unchanged traces are skipped the second time.
    built, skipped, _ = build(traces, out, log=lambda _: None)
    assert (built, skipped) == (0, 1)
