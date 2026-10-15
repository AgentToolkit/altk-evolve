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
# Under the default dev_frac=0.3, by the sha256 of each uuid.
TRAIN = ["auth-02", "auth-05", "auth-07", "auth-08", "auth-09", "hock-02"]
HELDOUT = ["auth-01", "auth-03", "auth-04", "auth-06", "hock-01", "mov-01"]


def by_id(adapter: VakraAdapter, path: Path = FIXTURES) -> dict[str, AdapterRecord]:
    return {record.trace_id: record for record in adapter.records(path)}


def manifest_ids(manifest: dict[str, list[dict]]) -> list[str]:
    return [item["uuid"] for items in manifest.values() for item in items]


def everything() -> VakraAdapter:
    """dev_frac=0: every record is train."""
    return VakraAdapter(dev_frac=0.0)


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
    assert list(by_id(everything())) == ALL
    with pytest.raises(ValueError, match="No VAKRA domain files"):
        next(VakraAdapter().records(tmp_path))


def test_messages_are_rebuilt_as_the_port_does():
    record = by_id(everything())["auth-01"]

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
    records = by_id(VakraAdapter(dev_frac=0.0, include_tools=False))
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
    records = by_id(everything())

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
    records = by_id(everything())
    record = records["auth-02"]

    assert record.trace_id == "auth-02"
    assert (record.batch.source, record.batch.conversation_id, record.batch.batch_id) == ("vakra", "auth-02", "auth-02")
    assert record.metadata == {"domain": "authors", "split": "train", "score": 0.0}
    assert records["mov-01"].metadata == {"domain": "movies", "split": "train"}
    assert by_id(VakraAdapter(split="heldout"))["auth-01"].metadata == {"domain": "authors", "split": "heldout", "score": 1.0}
    with pytest.raises(ValueError, match="has no uuid"):
        VakraAdapter().record({"trajectory": []}, "authors", "train", None)


def test_batch_is_stable_and_the_revision_follows_content(data_copy: Path):
    first = by_id(everything(), data_copy)
    assert by_id(everything(), data_copy) == first

    records = json.loads((data_copy / "authors.json").read_text())
    records[1]["trajectory"][-1]["content"] = "Grace wrote the most papers."
    (data_copy / "authors.json").write_text(json.dumps(records))
    edited = by_id(everything(), data_copy)
    assert edited["auth-01"].batch == first["auth-01"].batch
    assert edited["auth-02"].batch.batch_id == first["auth-02"].batch.batch_id
    assert edited["auth-02"].batch.revision != first["auth-02"].batch.revision

    results = json.loads((data_copy / "results.json").read_text())
    results["domains"]["authors"]["dialogues"][2]["score"] = 0.0
    (data_copy / "results.json").write_text(json.dumps(results))
    regraded = by_id(everything(), data_copy)
    assert regraded["auth-03"].batch.revision != first["auth-03"].batch.revision
    assert regraded["auth-04"].batch == first["auth-04"].batch

    # Dropping the tool summary changes what processors see, so it is a new revision too.
    assert by_id(VakraAdapter(dev_frac=0.0, include_tools=False), data_copy)["auth-01"].batch.revision != regraded["auth-01"].batch.revision


def test_split_is_by_uuid_hash():
    assert list(by_id(VakraAdapter())) == TRAIN
    assert list(by_id(VakraAdapter(split="heldout"))) == HELDOUT
    # Held out when the first 8 bytes of sha256(uuid) fall below dev_frac: auth-01's are about 0.108.
    assert VakraAdapter(dev_frac=0.11).is_heldout("auth-01") and not VakraAdapter(dev_frac=0.10).is_heldout("auth-01")
    assert list(by_id(everything())) == ALL
    assert list(by_id(VakraAdapter(dev_frac=0.0, split="heldout"))) == []


def test_split_survives_insertions_removals_and_reordering(data_copy: Path):
    """No dataset revision moves an already-mined record into held-out (or a held-out one into train)."""
    mined = set(by_id(VakraAdapter(), data_copy))
    heldout = set(by_id(VakraAdapter(split="heldout"), data_copy))

    records = json.loads((data_copy / "authors.json").read_text())
    added = [{**records[2], "uuid": f"auth-new-{i}"} for i in range(20)]
    (data_copy / "authors.json").write_text(json.dumps([*added, *reversed(records[1:])]))  # prepend, drop auth-01, reverse

    revised_train = set(by_id(VakraAdapter(), data_copy))
    revised_heldout = set(by_id(VakraAdapter(split="heldout"), data_copy))
    assert not revised_heldout & mined
    assert not revised_train & heldout
    assert revised_train - {f"auth-new-{i}" for i in range(20)} == mined


def test_dev_frac_is_the_held_out_fraction(tmp_path: Path):
    write_domain(tmp_path, "big", 2000)
    for dev_frac in (0.1, 0.3, 0.5):
        heldout = len(by_id(VakraAdapter(dev_frac=dev_frac, split="heldout"), tmp_path))
        assert abs(heldout / 2000 - dev_frac) < 0.04
    # A larger dev_frac only adds held-out records.
    assert set(by_id(VakraAdapter(dev_frac=0.1, split="heldout"), tmp_path)) < set(
        by_id(VakraAdapter(dev_frac=0.3, split="heldout"), tmp_path)
    )


def test_split_counts_are_reported_per_file(caplog: pytest.LogCaptureFixture):
    with caplog.at_level("WARNING", logger="experiments.guideline_pipeline.adapters.vakra"):
        list(VakraAdapter().records(FIXTURES))
    assert [r.getMessage() for r in caplog.records] == [
        "authors.json: 5 train, 4 held out (dev_frac=0.3)",
        "hockey.json: 1 train, 1 held out (dev_frac=0.3)",
        "movies.json: 0 train, 1 held out (dev_frac=0.3)",
    ]


@pytest.mark.parametrize("options", [{}, {"dev_frac": "0"}, {"dev_frac": "0.3"}, {"dev_frac": "0.5"}])
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
    assert (adapter.dev_frac, adapter.split, adapter.include_tools) == (0.3, "heldout", False)
    assert list(by_id(adapter)) == ["hock-01", "mov-01"]

    for options, message in (
        ({"dev_frac": "1.0"}, r"dev_frac must be in \[0.0, 1.0\)"),
        ({"dev_frac": "-0.1"}, r"dev_frac must be in \[0.0, 1.0\)"),
        ({"dev_frac": "nan"}, r"dev_frac must be in \[0.0, 1.0\)"),
        ({"dev_frac": "seven"}, "dev_frac must be a number"),
        ({"train_n": "7"}, "Unknown vakra option 'train_n'"),
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

    records = by_id(VakraAdapter(dev_frac=0.0, results=elsewhere), data_copy)
    assert records["auth-01"].outcome is not None and records["auth-01"].outcome.success


def test_heldout_manifest_contents_and_determinism(tmp_path: Path):
    manifest = VakraAdapter().heldout_manifest(FIXTURES)
    assert manifest == {
        "authors": [
            {"uuid": "auth-01", "query": "Which conference had the most papers in 2012?", "answer": "ICML", "score": 1.0},
            {"uuid": "auth-03", "query": "Query 3", "answer": "Answer 3", "score": 1.0},
            {"uuid": "auth-04", "query": "Query 4", "answer": "Answer 4", "score": 1.0},
            {"uuid": "auth-06", "query": "Query 6", "answer": "", "score": 0.0},
        ],
        "hockey": [{"uuid": "hock-01", "query": "Which team won in 1999?", "answer": "Stars", "score": 1.0}],
        "movies": [{"uuid": "mov-01", "query": "Who directed the oldest film?", "answer": "A synthetic director.", "score": None}],
    }
    # A selected domain that holds nothing out still has its (empty) entry.
    assert VakraAdapter(dev_frac=0.0, domains=frozenset({"movies"})).heldout_manifest(FIXTURES) == {"movies": []}

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


def test_heldout_manifest_merges_files_that_share_a_domain(data_copy: Path):
    records = json.loads((data_copy / "authors.json").read_text())
    (data_copy / "authors_extra.json").write_text(json.dumps([{**record, "uuid": record["uuid"] + "-x"} for record in records]))

    heldout = list(by_id(VakraAdapter(split="heldout"), data_copy))
    manifest = VakraAdapter().heldout_manifest(data_copy)
    assert any(uuid.endswith("-x") for uuid in heldout) and any(uuid.startswith("auth") and not uuid.endswith("-x") for uuid in heldout)
    assert manifest_ids(manifest) == heldout
    assert list(manifest) == ["authors", "hockey", "movies"]


def test_heldout_manifest_reports_a_query_that_is_also_in_train(data_copy: Path, caplog: pytest.LogCaptureFixture):
    records = json.loads((data_copy / "authors.json").read_text())
    records[2]["trajectory"][0]["content"] = "  query   5 "  # auth-03 (held out) now asks auth-05's (train) question
    (data_copy / "authors.json").write_text(json.dumps(records))

    with caplog.at_level("WARNING", logger="experiments.guideline_pipeline.adapters.vakra"):
        VakraAdapter().heldout_manifest(data_copy)
    assert [r.getMessage() for r in caplog.records] == ["1 held-out record(s) repeat a train query: auth-03"]
    caplog.clear()
    with caplog.at_level("WARNING", logger="experiments.guideline_pipeline.adapters.vakra"):
        VakraAdapter().heldout_manifest(FIXTURES)
    assert caplog.records == []


def test_records_stream_one_domain_at_a_time(data_copy: Path):
    (data_copy / "movies.json").write_text("{ truncated")
    records = everything().records(data_copy)

    assert isinstance(records, Iterator)
    assert [next(records).trace_id for _ in range(11)][-1] == "hock-02"  # movies.json is not read yet
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

    # Only the train split by default: 6 of the 12 records.
    assert cli.main(argv) == 0
    assert capsys.readouterr().out.strip() == "dry run: 6 valid, 0 failed"
    out = tmp_path / "heldout.json"
    assert cli.main([*argv, "--adapter-option", "dev_frac=0.5", "--adapter-option", f"heldout_out={out}"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 5 valid, 0 failed"
    assert manifest_ids(json.loads(out.read_text())) == ["auth-01", "auth-03", "auth-04", "auth-06", "hock-01", "hock-02", "mov-01"]
    assert cli.main([*argv, "--adapter-option", "split=heldout"]) == 0
    assert capsys.readouterr().out.strip() == "dry run: 6 valid, 0 failed"
    assert cli.main([*argv, "--adapter-option", "dev_frac=1"]) == 2
    assert "dev_frac must be in" in capsys.readouterr().err


def test_mine_checkpoints_each_dialogue_and_stamps_evidence(client: EvolveClient, data_copy: Path):
    def run():
        return mine(
            VakraAdapter(dev_frac=0.0, domains=frozenset({"hockey", "movies"})).records(data_copy),
            client,
            namespace_id="m",
            processing_profile="echo",
        )

    first, again = run(), run()
    assert (first.processed, first.skipped, again.processed, again.skipped) == (3, 0, 0, 3)
    stored = {e.metadata["processing"]["source_batch"]["batch_id"]: e for e in client.get_all_entities("m")}
    assert {batch: e.metadata["success"] for batch, e in stored.items()} == {"hock-01": True, "hock-02": False, "mov-01": None}
