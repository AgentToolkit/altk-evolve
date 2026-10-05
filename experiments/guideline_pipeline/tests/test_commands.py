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


def mock_consolidation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cluster guidelines by evidence and merge each cluster into one, conserving support: no embeddings, no LLM."""

    def cluster(entities: list[RecordedEntity], threshold: float = 0.8, embedding_model: str | None = None):
        groups: dict[str, list[RecordedEntity]] = {}
        for entity in entities:
            groups.setdefault(str(entity.metadata.get("evidence")), []).append(entity)
        return [group for group in groups.values() if len(group) > 1]

    def combine(cluster: list[RecordedEntity], mode: str = "lossless"):
        assert mode == "lossless"
        support = sum(int(e.metadata.get("support", 1)) for e in cluster)
        evidence = cluster[0].metadata.get("evidence")
        merged = ConsolidatedGuideline(
            content="Check the answer before finishing.", rationale="merged", category="strategy", trigger="always", support=support
        )
        return [merged.model_copy(update={"evidence": evidence, "source_indices": list(range(len(cluster)))})]

    monkeypatch.setattr(clustering, "cluster_entities", cluster)
    monkeypatch.setattr(clustering, "combine_cluster", combine)


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


def test_consolidate_command_then_export(mined: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    mock_consolidation(monkeypatch)

    code, stdout, _ = run(capsys, "consolidate", "--namespace", "memories", "--threshold", "0.5")
    assert code == 0
    assert stdout == "consolidate memories (lossless, threshold 0.5): 1 clusters merged, 2 -> 1 guidelines, support 2 -> 2"

    playbook, lineage = tmp_path / "playbook.json", tmp_path / "lineage.json"
    assert run(capsys, "export", "playbook", "--namespace", "memories", "--out", str(playbook))[0] == 0
    assert json.loads(playbook.read_text())["entries"] == [
        {"r": "Check the answer before finishing.", "n": 2, "e": "s"},
        {"r": "For tasks like 'do task-2', check the answer 'wrong'.", "n": 1, "e": "f"},
    ]
    code, stdout, _ = run(capsys, "lineage", "--namespace", "memories", "--out", str(lineage))
    merged = json.loads(lineage.read_text())["guidelines"][0]
    assert (merged["sources_origin"], merged["source_task_ids"]) == ("missing", [])
    assert "1 with recorded sources, 0 derived from source_task_id, 1 without sources" in stdout

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
