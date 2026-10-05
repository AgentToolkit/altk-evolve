"""The VAKRA adapter, on hand-written synthetic domain files under fixtures/vakra."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory
from altk_evolve.processing import TrajectoryOutcome

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS, AdapterRecord, get_adapter
from experiments.guideline_pipeline.adapters.elision import PARSER_STEP_LIMIT
from experiments.guideline_pipeline.adapters.vakra import VakraAdapter
from experiments.guideline_pipeline.stages.mine import mine

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "vakra"
ALL = ["auth-01", "auth-02", "auth-03", "auth-04", "auth-05", "auth-06", "auth-07", "auth-08", "auth-09", "hock-01", "hock-02", "mov-01"]


def by_id(adapter: VakraAdapter, path: Path = FIXTURES) -> dict[str, AdapterRecord]:
    return {record.trace_id: record for record in adapter.records(path)}


def manifest_ids(manifest: dict[str, list[dict]]) -> list[str]:
    return [item["uuid"] for items in manifest.values() for item in items]


def write_domain(directory: Path, domain: str, count: int) -> None:
    records = [
        {"uuid": f"{domain}-{i}", "domain": domain, "trajectory": [{"type": "HumanMessage", "content": f"q{i}"}]} for i in range(count)
    ]
    (directory / f"{domain}.json").write_text(json.dumps(records))


@pytest.fixture
def data_copy(tmp_path: Path) -> Path:
    return Path(shutil.copytree(FIXTURES, tmp_path / "vakra"))


def test_registered_and_reads_domain_files_only(tmp_path: Path):
    assert isinstance(ADAPTERS["vakra"], VakraAdapter)
    # authors_tools.json and results.json are not domain files; every domain is read, in name order.
    assert list(by_id(VakraAdapter(train_n=100))) == ALL
    with pytest.raises(ValueError, match="No VAKRA domain files"):
        next(VakraAdapter().records(tmp_path))


def test_messages_are_rebuilt_as_the_port_does():
    record = by_id(VakraAdapter())["auth-01"]

    assert record.messages == [
        {"role": "user", "content": "Which conference had the most papers in 2012?"},
        {
            "role": "assistant",
            "content": "TOOL SPACE:\ntools chosen in this trajectory: get_papers\nall available tools: get_papers, get_conferences",
        },
        {"role": "assistant", "content": "AGENT REASONING:\nI should count papers per conference for 2012."},
        {
            "role": "assistant",
            "content": [{"type": "function_call", "id": "call_0", "function": {"name": "get_papers", "arguments": '{"year": 2012}'}}],
        },
        {"role": "assistant", "content": 'OBSERVATION:\n[{"conference": "ICML", "count": 12}]'},
        {"role": "assistant", "content": "AGENT MESSAGE (answer):\nICML had the most papers in 2012."},
    ]
    # The outcome is a field, not an injected message.
    assert "dialogue_score" not in json.dumps(record.messages)
    parsed = parse_openai_agents_trajectory(record.messages)
    assert parsed["task_instruction"] == "Which conference had the most papers in 2012?"
    assert parsed["num_steps"] == 5
    assert parsed["steps_list"][2] == "**Step 3 - Action:**\nget_papers(year=2012)"


def test_follow_ups_calls_and_the_task_fallback():
    records = by_id(VakraAdapter(train_n=100, include_tools=False))
    messages = records["auth-02"].messages

    # Calls are numbered across the trajectory, an empty observation is dropped,
    # and a follow-up user turn is an assistant step (the parser takes the first user message as the task).
    assert [m["content"][0]["id"] for m in messages if isinstance(m["content"], list)] == ["call_0", "call_1"]
    assert messages[3:] == [
        {"role": "assistant", "content": 'OBSERVATION:\n["Ada", "Grace"]'},
        {"role": "assistant", "content": "USER MESSAGE:\nOnly count journal papers."},
        {"role": "assistant", "content": "AGENT MESSAGE (answer):\nAda wrote the most papers."},
    ]
    assert records["auth-06"].messages == [{"role": "user", "content": "Query 6"}]
    # No HumanMessage in the trajectory: the task is the first turn's query.
    assert records["auth-09"].messages == [{"role": "user", "content": "Query 9"}]


def test_long_text_is_chunked_and_long_trajectories_are_elided():
    observation = "x" * 4500
    calls = [{"type": "AIMessage", "tool_calls": [{"name": "lookup", "args": {"i": i}}]} for i in range(60)]
    raw = {
        "uuid": "long-1",
        "trajectory": [{"type": "HumanMessage", "content": "long task"}, {"type": "ToolMessage", "result": observation}, *calls],
    }
    record = VakraAdapter(include_tools=False).record(raw, "authors", "train", None)

    chunks = [m["content"] for m in record.messages if isinstance(m["content"], str) and m["content"].startswith("OBSERVATION")]
    assert [chunk.split(":\n")[0] for chunk in chunks] == ["OBSERVATION (part 1/3)", "OBSERVATION (part 2/3)", "OBSERVATION (part 3/3)"]
    assert "".join(chunk.split(":\n", 1)[1] for chunk in chunks) == observation
    assert all(len(chunk) <= 2000 for chunk in chunks)

    # 3 chunks + 60 calls = 63 steps: the shared helper keeps 6 + marker + 43, so the last call survives.
    assert len(record.messages) == 1 + PARSER_STEP_LIMIT
    assert record.messages[7] == {"role": "assistant", "content": "[14 steps elided: the trajectory continues below]"}
    parsed = parse_openai_agents_trajectory(record.messages)
    assert parsed["num_steps"] == PARSER_STEP_LIMIT
    assert parsed["steps_list"][-1] == "**Step 50 - Action:**\nlookup(i=59)"


def test_outcome_mapping():
    records = by_id(VakraAdapter(train_n=100))

    passed = records["auth-01"].outcome
    assert passed is not None and passed.success and passed.failed_checks == ()
    assert passed.detail == (
        "dialogue_score=1.0 num_turns=1\n"
        "-- turn 1 score=1.0 gt_steps=1 pred_steps=1 extra_steps=0 exactmatch=1.0 answer=None groundedness=1.0\n"
        "  exactmatch: The tool calls match.\n"
        "  groundedness: The answer is grounded in the tool output."
    )

    failed = records["auth-02"].outcome
    assert failed is not None and not failed.success
    assert failed.failed_checks == (
        "exactmatch (turn 1): get_papers was called without the journal filter.",
        "answer (turn 1): The expected answer is Grace, not Ada.",
    )
    assert failed.detail is not None and "  answer: The expected answer is Grace,\nnot Ada." in failed.detail
    hockey = records["hock-02"].outcome
    assert hockey is not None and hockey.failed_checks == (
        "exactmatch (turn 1)",
        "groundedness (turn 1): No tool output supports the number.",
    )

    # A score without details is just the verdict; status "error" does not decide it.
    assert records["auth-03"].outcome == TrajectoryOutcome(success=True)
    assert records["auth-06"].outcome == TrajectoryOutcome(success=False)
    # No results entry: no outcome, not a guess from status.
    assert records["mov-01"].outcome is None
    assert VakraAdapter().record({"uuid": "u"}, "d", "train", {"uuid": "u", "score": None}).outcome is None


def test_metadata_and_batch_identity():
    records = by_id(VakraAdapter())
    record = records["auth-02"]

    assert record.trace_id == "auth-02"
    assert (record.batch.source, record.batch.conversation_id, record.batch.batch_id) == ("vakra", "auth-02", "auth-02")
    assert record.metadata == {"domain": "authors", "split": "train", "score": 0.0}
    assert records["mov-01"].metadata == {"domain": "movies", "split": "train"}
    assert by_id(VakraAdapter(split="heldout"))["auth-09"].metadata == {"domain": "authors", "split": "heldout", "score": 0.0}
    with pytest.raises(ValueError, match="has no uuid"):
        VakraAdapter().record({"trajectory": []}, "authors", "train", None)


def test_batch_is_stable_and_the_revision_follows_content(data_copy: Path):
    first = by_id(VakraAdapter(), data_copy)
    assert by_id(VakraAdapter(), data_copy) == first

    records = json.loads((data_copy / "authors.json").read_text())
    records[1]["trajectory"][-1]["content"] = "Grace wrote the most papers."
    (data_copy / "authors.json").write_text(json.dumps(records))
    edited = by_id(VakraAdapter(), data_copy)
    assert edited["auth-01"].batch == first["auth-01"].batch
    assert edited["auth-02"].batch.batch_id == first["auth-02"].batch.batch_id
    assert edited["auth-02"].batch.revision != first["auth-02"].batch.revision

    results = json.loads((data_copy / "results.json").read_text())
    results["domains"]["authors"]["dialogues"][2]["score"] = 0.0
    (data_copy / "results.json").write_text(json.dumps(results))
    regraded = by_id(VakraAdapter(), data_copy)
    assert regraded["auth-03"].batch.revision != first["auth-03"].batch.revision
    assert regraded["auth-04"].batch == first["auth-04"].batch

    # Dropping the tool summary changes what processors see, so it is a new revision too.
    assert by_id(VakraAdapter(include_tools=False), data_copy)["auth-01"].batch.revision != regraded["auth-01"].batch.revision


def test_train_n_split():
    # Default train_n=7: authors keeps 7 of 9; the 2-record and 1-record domains are all train.
    assert list(by_id(VakraAdapter())) == [*ALL[:7], "hock-01", "hock-02", "mov-01"]
    assert list(by_id(VakraAdapter(split="heldout"))) == ["auth-08", "auth-09"]
    assert list(by_id(VakraAdapter(train_n=1))) == ["auth-01", "hock-01", "mov-01"]
    assert list(by_id(VakraAdapter(train_n=0))) == []
    assert len(by_id(VakraAdapter(train_n=0, split="heldout"))) == len(ALL)


def test_dev_frac_split_rounds_the_held_out_count(tmp_path: Path):
    # The last round(n * 0.3) records are held out: 9 -> 3, 2 -> 1, and a lone record stays in train.
    assert list(by_id(VakraAdapter(dev_frac=0.3), FIXTURES)) == [
        "auth-01",
        "auth-02",
        "auth-03",
        "auth-04",
        "auth-05",
        "auth-06",
        "hock-01",
        "mov-01",
    ]
    assert list(by_id(VakraAdapter(dev_frac=0.3, split="heldout"))) == ["auth-07", "auth-08", "auth-09", "hock-02"]

    for count in (1, 2, 4, 10):
        write_domain(tmp_path, f"d{count}", count)
    heldout = by_id(VakraAdapter(dev_frac=0.3, split="heldout"), tmp_path)
    # 1 * 0.3 -> 0, 2 * 0.3 -> 1, 4 * 0.3 = 1.2 -> 1, 10 * 0.3 -> 3; always the last records.
    assert list(heldout) == ["d10-7", "d10-8", "d10-9", "d2-1", "d4-3"]
    assert len(by_id(VakraAdapter(dev_frac=0.0, split="heldout"), tmp_path)) == 0


@pytest.mark.parametrize("options", [{}, {"train_n": "3"}, {"dev_frac": "0.3"}, {"dev_frac": "0.5"}])
def test_train_and_heldout_are_disjoint_and_cover_everything(options: dict[str, str]):
    train = list(by_id(VakraAdapter().configure(options)))
    heldout = list(by_id(VakraAdapter().configure({**options, "split": "heldout"})))

    assert not set(train) & set(heldout)
    assert sorted(train + heldout) == sorted(ALL)
    # The manifest is exactly the held-out split under the same options.
    assert manifest_ids(VakraAdapter().configure(options).heldout_manifest(FIXTURES)) == heldout


def test_options_and_usage_errors(tmp_path: Path):
    adapter = get_adapter("vakra", {"dev_frac": "0.3", "split": "heldout", "domains": "hockey, movies", "include_tools": "no"})
    assert isinstance(adapter, VakraAdapter) and adapter is not ADAPTERS["vakra"]
    assert (adapter.dev_frac, adapter.train_n, adapter.split, adapter.include_tools) == (0.3, None, "heldout", False)
    assert list(by_id(adapter)) == ["hock-02"]

    with pytest.raises(ValueError, match="train_n and dev_frac are mutually exclusive"):
        VakraAdapter().configure({"train_n": "7", "dev_frac": "0.3"})
    with pytest.raises(ValueError, match="mutually exclusive"):
        VakraAdapter(train_n=7).configure({"dev_frac": "0.3"})
    for options, message in (
        ({"dev_frac": "1.0"}, r"dev_frac must be in \[0.0, 1.0\)"),
        ({"train_n": "-1"}, "train_n must be >= 0"),
        ({"train_n": "seven"}, "train_n must be a number"),
        ({"split": "dev"}, "split must be train or heldout"),
        ({"include_tools": "maybe"}, "must be true or false"),
        ({"shuffle": "true"}, "Unknown vakra option 'shuffle'"),
    ):
        with pytest.raises(ValueError, match=message):
            VakraAdapter().configure(options)
    with pytest.raises(ValueError, match=r"Domains not found .*\['nope'\]"):
        next(VakraAdapter(domains=frozenset({"nope"})).records(FIXTURES))
    with pytest.raises(FileNotFoundError):
        next(VakraAdapter(results=tmp_path / "missing.json").records(FIXTURES))


def test_results_option_reads_an_evaluation_elsewhere(data_copy: Path):
    elsewhere = data_copy.parent / "eval.json"
    (data_copy / "results.json").rename(elsewhere)
    assert all(record.outcome is None for record in VakraAdapter().records(data_copy))

    records = by_id(VakraAdapter(results=elsewhere), data_copy)
    assert records["auth-01"].outcome is not None and records["auth-01"].outcome.success


def test_heldout_manifest_contents_and_determinism(tmp_path: Path):
    manifest = VakraAdapter().heldout_manifest(FIXTURES)
    assert manifest == {
        "authors": [
            {"uuid": "auth-08", "query": "Query 8", "answer": "Answer 8", "score": 1.0},
            {"uuid": "auth-09", "query": "Query 9", "answer": "Answer 9", "score": 0.0},
        ],
        "hockey": [],
        "movies": [],
    }
    assert VakraAdapter(dev_frac=0.3).heldout_manifest(FIXTURES)["hockey"] == [
        {"uuid": "hock-02", "query": "How many goals in 2001?", "answer": "312", "score": 0.0}
    ]
    assert VakraAdapter(domains=frozenset({"movies"})).heldout_manifest(FIXTURES) == {"movies": []}

    # heldout_out writes it when reading starts, byte-for-byte the same on every run.
    out = tmp_path / "vakra_heldout.json"
    adapter = VakraAdapter().configure({"heldout_out": str(out)})
    records = adapter.records(FIXTURES)
    assert not out.exists()  # nothing happens until the first record is read
    next(records)
    first = out.read_bytes()
    assert json.loads(first) == manifest
    list(adapter.records(FIXTURES))
    assert out.read_bytes() == first


def test_records_stream_one_domain_at_a_time(data_copy: Path):
    (data_copy / "movies.json").write_text("{ truncated")
    records = VakraAdapter().records(data_copy)

    assert isinstance(records, Iterator)
    assert [next(records).trace_id for _ in range(9)][-1] == "hock-02"  # movies.json is not read yet
    with pytest.raises(json.JSONDecodeError):
        next(records)
    (data_copy / "movies.json").write_text(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError, match="not a JSON list of records"):
        list(VakraAdapter().records(data_copy))


def test_mine_dry_run_from_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def no_client() -> EvolveClient:
        raise AssertionError("dry run must not touch storage")

    monkeypatch.setattr(cli, "make_client", no_client)
    argv = ["mine", "--adapter", "vakra", "--input", str(FIXTURES), "--namespace", "x", "--processing-profile", "x", "--dry-run"]

    # Only the train split by default: 7 + 2 + 1 of the 12 records.
    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip() == "dry run: 10 valid, 0 failed"
    out = tmp_path / "heldout.json"
    assert cli.main([*argv, "--adapter-option", "dev_frac=0.3", "--adapter-option", f"heldout_out={out}"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 8 valid, 0 failed"
    assert manifest_ids(json.loads(out.read_text())) == ["auth-07", "auth-08", "auth-09", "hock-02"]
    assert cli.main([*argv, "--adapter-option", "split=heldout"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 2 valid, 0 failed"
    assert cli.main([*argv, "--adapter-option", "train_n=7", "--adapter-option", "dev_frac=0.3"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_mine_checkpoints_each_dialogue_and_stamps_evidence(client: EvolveClient, data_copy: Path):
    def run():
        return mine(
            VakraAdapter(domains=frozenset({"hockey", "movies"})).records(data_copy), client, namespace_id="m", processing_profile="echo"
        )

    first, again = run(), run()
    assert (first.processed, first.skipped, again.processed, again.skipped) == (3, 0, 0, 3)
    stored = {e.metadata["processing"]["source_batch"]["batch_id"]: e for e in client.get_all_entities("m")}
    assert {batch: e.metadata["success"] for batch, e in stored.items()} == {"hock-01": True, "hock-02": False, "mov-01": None}
