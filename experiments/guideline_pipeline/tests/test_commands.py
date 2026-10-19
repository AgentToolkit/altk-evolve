"""consolidate, export and lineage end to end through the CLI, with the consolidation LLM call mocked."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import altk_evolve.llm.guidelines.clustering as clustering
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.guidelines import ConsolidatedGuideline

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS
from experiments.guideline_pipeline.tests.fakes import FakeAdapter, store_guidelines

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def wired(client: EvolveClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter())
    monkeypatch.setattr(cli, "make_client", lambda: client)


@pytest.fixture
def mined(dataset: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    args = ["mine", "--adapter", "fake", "--input", str(dataset), "--namespace", "memories", "--processing-profile", "guidelines"]
    assert cli.main(args) == 0
    capsys.readouterr()
    return dataset


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def mock_consolidation(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Cluster guidelines by evidence and merge each cluster into one, conserving support: no embeddings, no LLM.

    Returns the keyword options each clustering call received, so a test can check what reached the library.
    """
    cluster_options: list[dict] = []

    def cluster(entities: list[RecordedEntity], threshold: float = 0.8, embedding_model: str | None = None, **options):
        cluster_options.append(options)
        groups: dict[str, list[RecordedEntity]] = {}
        for entity in entities:
            groups.setdefault(str(entity.metadata.get("evidence")), []).append(entity)
        return [group for group in groups.values() if len(group) > 1]

    def combine(cluster: list[RecordedEntity], mode: str = "lossless", clustering_key: str = "task_description"):
        assert mode == "lossless"
        support = sum(int(e.metadata.get("support", 1)) for e in cluster)
        evidence = cluster[0].metadata.get("evidence")
        merged = ConsolidatedGuideline(
            content="Check the answer before finishing.", rationale="merged", category="strategy", trigger="always", support=support
        )
        return [(merged.model_copy(update={"evidence": evidence, "source_indices": list(range(len(cluster)))}), list(cluster))]

    def no_llm(*args, **kwargs):
        raise AssertionError("consolidation reached the real LLM: the merge mock no longer matches what it calls")

    monkeypatch.setattr(clustering, "cluster_entities", cluster)
    monkeypatch.setattr(clustering, "combine_cluster_with_members", combine)
    # If consolidation stops going through the mocked merge, refuse the LLM call so the test fails offline.
    monkeypatch.setattr(clustering, "completion", no_llm)
    return cluster_options


def test_export_playbook_command(mined: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "out" / "playbook.json"

    code, stdout, _ = run(capsys, "export", "playbook", "--namespace", "memories", "--out", str(out))

    assert code == 0
    assert json.loads(out.read_text()) == {
        "entries": [
            {"r": "For tasks like 'do task-1', check the answer 'done'.", "n": 1, "e": "s"},
            {"r": "For tasks like 'do task-2', check the answer 'wrong'.", "n": 1, "e": "f"},
            {"r": "For tasks like 'do task-3', check the answer 'done'.", "n": 1, "e": "s"},
        ]
    }
    assert stdout == f"playbook: 3 entries (0 below min-support skipped); support sum=3 max=1 | n>=2: 0 | n>=3: 0; wrote {out}"
    assert run(capsys, "export", "playbook", "--namespace", "memories", "--out", str(out), "--min-support", "2")[0] == 0
    assert json.loads(out.read_text()) == {"entries": []}


def test_export_retrieval_index_command(mined: Path, tmp_path: Path, client: EvolveClient, capsys: pytest.CaptureFixture[str]):
    store_guidelines(client, "memories", ("Always-on rule.", {"support": 3}), ("Orphan rule.", {"support": 2}))
    out = tmp_path / "index.json"

    code, stdout, stderr = run(
        capsys, "export", "retrieval-index", "--namespace", "memories", "--out", str(out), "--adapter", "fake", "--input", str(mined)
    )

    assert code == 0
    index = json.loads(out.read_text())
    assert index["core"] == ["Always-on rule."]
    assert [(s["source_task"], s["source_instruction"]) for s in index["singletons"]] == [
        ("task-1", "do task-1"),
        ("task-2", "do task-2"),
        ("task-3", "do task-3"),
    ]
    assert stdout.startswith("retrieval index: 1 core, 3 singletons; 0 below min-support, 1 singletons skipped unresolved")
    assert stderr.startswith("skipped ") and stderr.endswith(": no source task")

    # Without the dataset, the guideline's own task_description stands in for single-source guidelines.
    assert run(capsys, "export", "retrieval-index", "--namespace", "memories", "--out", str(out), "--core-support", "2")[0] == 0
    index = json.loads(out.read_text())
    assert index["core"] == ["Always-on rule.", "Orphan rule."]
    assert [s["source_instruction"] for s in index["singletons"]] == ["do task-1", "do task-2", "do task-3"]


def test_lineage_command(mined: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "lineage.json"

    code, stdout, _ = run(capsys, "lineage", "--namespace", "memories", "--out", str(out), "--adapter", "fake", "--input", str(mined))

    assert code == 0
    document = json.loads(out.read_text())
    assert [e["source_task_ids"] for e in document["guidelines"]] == [["task-1"], ["task-2"], ["task-3"]]
    assert [e["sources"][0]["instruction"] for e in document["guidelines"]] == ["do task-1", "do task-2", "do task-3"]
    assert stdout.endswith(f"3 distinct source tasks, 3 found in --input; wrote {out}")


def test_consolidate_command_clustering_options(mined: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    cluster_options = mock_consolidation(monkeypatch)
    options = ("--cluster-key", "guideline_text", "--max-cluster-size", "2", "--max-rounds", "3")

    code, stdout, _ = run(capsys, "consolidate", "--namespace", "memories", "--threshold", "0.5", *options)

    assert code == 0
    # Round 2 finds no cluster left to merge, so the run stops before the limit.
    assert stdout == (
        "consolidate memories (lossless, by guideline_text, threshold 0.5, max cluster size 2): 1 clusters merged in 2 round(s), "
        "2 -> 1 guidelines, support 2 -> 2"
    )
    # The summary prints the stage's own options, so check what the library was actually given.
    assert cluster_options and all(o == {"key": "guideline_text", "max_cluster_size": 2} for o in cluster_options)
    for bad in (("--max-cluster-size", "1"), ("--max-rounds", "0"), ("--cluster-key", "content")):
        with pytest.raises(SystemExit):
            cli.main(["consolidate", "--namespace", "memories", *bad])


def test_consolidate_command_then_export(mined: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    cluster_options = mock_consolidation(monkeypatch)

    code, stdout, _ = run(capsys, "consolidate", "--namespace", "memories", "--threshold", "0.5")
    assert code == 0
    assert stdout == (
        "consolidate memories (lossless, by task_description, threshold 0.5): 1 clusters merged in 1 round(s), "
        "2 -> 1 guidelines, support 2 -> 2"
    )
    assert cluster_options == [{"key": "task_description", "max_cluster_size": None}]

    playbook, lineage = tmp_path / "playbook.json", tmp_path / "lineage.json"
    assert run(capsys, "export", "playbook", "--namespace", "memories", "--out", str(playbook))[0] == 0
    assert json.loads(playbook.read_text())["entries"] == [
        {"r": "Check the answer before finishing.", "n": 2, "e": "s"},
        {"r": "For tasks like 'do task-2', check the answer 'wrong'.", "n": 1, "e": "f"},
    ]
    code, stdout, _ = run(capsys, "lineage", "--namespace", "memories", "--out", str(lineage))
    merged = json.loads(lineage.read_text())["guidelines"][0]
    assert (merged["sources_origin"], merged["source_task_ids"]) == ("recorded", ["task-1", "task-3"])
    assert "2 with recorded sources, 0 derived from source_task_id, 0 without sources" in stdout

    assert run(capsys, "consolidate", "--namespace", "memories", "--mode", "none")[1] == "consolidate memories: mode none, nothing changed"


def test_usage_and_write_errors(mined: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    out = str(tmp_path / "x.json")
    code, _, stderr = run(capsys, "export", "playbook", "--namespace", "nope", "--out", out)
    assert code == 2 and stderr.startswith("error:")
    assert run(capsys, "consolidate", "--namespace", "nope")[0] == 2
    code, _, stderr = run(capsys, "lineage", "--namespace", "memories", "--out", out, "--adapter", "fake")
    assert (code, stderr) == (2, "error: --adapter and --input go together")
    code, _, stderr = run(
        capsys, "export", "retrieval-index", "--namespace", "memories", "--out", out, "--core-support", "1", "--min-support", "2"
    )
    assert code == 2 and "must be <= core-support" in stderr
    with pytest.raises(SystemExit):
        cli.main(["export", "playbook", "--namespace", "memories", "--out", out, "--min-support", "0"])
    with pytest.raises(SystemExit):
        cli.main(["export", "playbook", "--namespace", "memories"])  # --out is required: nothing is written by default
    capsys.readouterr()

    def fail(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "replace", fail)
    code, _, stderr = run(capsys, "export", "playbook", "--namespace", "memories", "--out", out)
    assert (code, stderr) == (1, "error: read-only file system")
    assert not Path(out).exists() and list(tmp_path.glob(".x.json*")) == []
