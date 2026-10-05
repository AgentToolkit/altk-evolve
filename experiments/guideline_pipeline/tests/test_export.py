"""Playbook and retrieval-index export, and the atomic writer, against a filesystem backend."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import RecordedEntity
from altk_evolve.schema.guidelines import DEFAULT_TASK_DESCRIPTION

from experiments.guideline_pipeline.guidelines import fetch_guidelines, guideline_rows
from experiments.guideline_pipeline.output import write_json_atomic
from experiments.guideline_pipeline.stages.export import EVIDENCE_CODES, build_playbook, build_retrieval_index

from experiments.guideline_pipeline.tests.fakes import store_guidelines

pytestmark = pytest.mark.unit


def rows_of(client: EvolveClient, namespace_id: str = "memories"):
    return guideline_rows(fetch_guidelines(client, namespace_id))


def playbook(client: EvolveClient, **kwargs) -> list[dict]:
    rows, empty = rows_of(client)
    entries: list[dict] = build_playbook(rows, empty=empty, **kwargs)[0]["entries"]
    return entries


def task(task_id: str, **metadata) -> dict:
    """Metadata of a mined guideline from one source task."""
    return {"source_task_id": task_id, "task_description": f"instruction for {task_id}", **metadata}


def test_evidence_mapping_is_one_documented_constant():
    assert dict(EVIDENCE_CODES) == {"success": "s", "failure": "f", "both": "b", None: "b"}


def test_playbook_format_and_evidence_mapping(client: EvolveClient):
    store_guidelines(
        client,
        "memories",
        ("A success rule", {"evidence": "success"}),
        ("B failure rule", {"evidence": "failure"}),
        ("C both rule", {"evidence": "both"}),
        ("D unknown rule", {"evidence": None}),
        ("E absent rule", {}),
    )

    assert playbook(client) == [
        {"r": "A success rule", "n": 1, "e": "s"},
        {"r": "B failure rule", "n": 1, "e": "f"},
        {"r": "C both rule", "n": 1, "e": "b"},
        {"r": "D unknown rule", "n": 1, "e": "b"},
        {"r": "E absent rule", "n": 1, "e": "b"},
    ]


def test_unrecognized_evidence_is_an_error(client: EvolveClient):
    store_guidelines(client, "memories", ("rule", {"evidence": "mixed"}))
    with pytest.raises(ValueError, match="unrecognized evidence 'mixed'"):
        playbook(client)


def test_support_defaults_to_one(client: EvolveClient):
    store_guidelines(
        client, "memories", ("absent", {}), ("explicit", {"support": 4}), ("unusable", {"support": "many"}), ("zero", {"support": 0})
    )

    assert [(e["r"], e["n"]) for e in playbook(client)] == [("explicit", 4), ("absent", 1), ("unusable", 1), ("zero", 1)]


def test_min_support_keeps_guidelines_at_the_threshold(client: EvolveClient):
    store_guidelines(client, "memories", *((f"rule {n}", {"support": n}) for n in (1, 2, 3)))
    rows, _ = rows_of(client)

    for min_support, expected in ((1, [3, 2, 1]), (2, [3, 2]), (3, [3]), (4, [])):
        document, report = build_playbook(rows, min_support=min_support)
        assert [e["n"] for e in document["entries"]] == expected
        assert report.below_min_support == 3 - len(expected)
    assert build_playbook(rows, min_support=2)[1].summary() == (
        "playbook: 2 entries (1 below min-support skipped); support sum=5 max=3 | n>=2: 2 | n>=3: 1"
    )


def test_order_is_support_descending_then_text_regardless_of_storage_order(client: EvolveClient, tmp_path: Path):
    guidelines = [("beta", {"support": 2}), ("Alpha", {"support": 2}), ("gamma", {"support": 5}), ("alpha", {}), ("  delta  ", {})]
    store_guidelines(client, "memories", *guidelines)
    store_guidelines(client, "reversed", *reversed(guidelines))

    expected = ["gamma", "Alpha", "beta", "alpha", "delta"]
    assert [e["r"] for e in playbook(client)] == expected
    rows, _ = rows_of(client, "reversed")
    assert [e["r"] for e in build_playbook(rows)[0]["entries"]] == expected


def test_empty_guidelines_are_skipped_and_counted(client: EvolveClient):
    store_guidelines(client, "memories", ("   ", {}), ("kept", {}))
    rows, empty = rows_of(client)
    document, report = build_playbook(rows, empty=empty)
    assert [e["r"] for e in document["entries"]] == ["kept"]
    assert "1 empty" in report.summary()


def test_core_singleton_split_at_the_boundary(client: EvolveClient):
    store_guidelines(
        client,
        "memories",
        ("at core", {"support": 3}),
        ("above core", {"support": 4}),
        ("below core", task("task-2", support=2)),
        ("floor", task("task-1")),
    )
    rows, _ = rows_of(client)

    index, report = build_retrieval_index(rows, core_support=3)
    assert index == {
        "core": ["above core", "at core"],
        "singletons": [
            {"rule": "below core", "source_task": "task-2", "source_instruction": "instruction for task-2"},
            {"rule": "floor", "source_task": "task-1", "source_instruction": "instruction for task-1"},
        ],
    }
    assert (report.core, report.singletons, report.below_min_support) == (2, 2, 0)

    floored, report = build_retrieval_index(rows, core_support=3, min_support=2)
    assert [s["rule"] for s in floored["singletons"]] == ["below core"] and report.below_min_support == 1
    assert build_retrieval_index(rows, core_support=4)[0]["core"] == ["above core"]
    with pytest.raises(ValueError, match="min-support"):
        build_retrieval_index(rows, core_support=2, min_support=3)


def test_unresolvable_singletons_are_skipped_and_counted(client: EvolveClient):
    store_guidelines(
        client,
        "memories",
        ("consolidated, no sources", {"support": 2, "task_description": "merged tasks"}),
        ("placeholder description", task("task-1", task_description=DEFAULT_TASK_DESCRIPTION)),
        ("resolved", task("task-3")),
    )
    rows, _ = rows_of(client)

    index, report = build_retrieval_index(rows, core_support=3)

    assert [s["rule"] for s in index["singletons"]] == ["resolved"]
    assert dict(report.unresolved) == {"no source task": 1, "no stored instruction (pass --adapter and --input)": 1}
    assert {reason for _, reason in report.unresolved_ids} == set(report.unresolved)
    assert report.summary() == (
        "retrieval index: 0 core, 1 singletons; 0 below min-support, 2 singletons skipped unresolved "
        "(no source task: 1, no stored instruction (pass --adapter and --input): 1)"
    )


def entity(entity_id: str, content: str, metadata: dict) -> RecordedEntity:
    return RecordedEntity(id=entity_id, type="guideline", content=content, metadata=metadata, created_at=datetime.now(UTC))


def multi_source(*task_ids: str, superseded: tuple[str, ...] = ()) -> dict:
    sources = [{"task_id": t, "status": "superseded" if t in superseded else "supporting"} for t in task_ids]
    return {"support": 2, "task_description": "describes only one of them", "sources": sources}


def test_multi_source_singletons_need_the_dataset_and_list_every_resolved_task():
    rows, _ = guideline_rows([entity("1", "merged", multi_source("task-1", "task-2", "task-3", superseded=("task-3",)))])

    without, report = build_retrieval_index(rows, core_support=3)
    assert without["singletons"] == [] and sum(report.unresolved.values()) == 1

    instructions = {"task-1": "first", "task-2": "second", "task-3": "superseded source"}
    index, report = build_retrieval_index(rows, core_support=3, instructions=instructions)
    assert index["singletons"] == [
        {
            "rule": "merged",
            "source_task": "task-1",
            "source_instruction": "first",
            "source_tasks": [
                {"source_task": "task-1", "source_instruction": "first"},
                {"source_task": "task-2", "source_instruction": "second"},
            ],
        }
    ]
    assert report.partial == 0

    partial, report = build_retrieval_index(rows, core_support=3, instructions={"task-2": "second"})
    assert partial["singletons"] == [{"rule": "merged", "source_task": "task-2", "source_instruction": "second"}]
    assert report.partial == 1 and "1 singletons missing some source tasks" in report.summary()
    missing, report = build_retrieval_index(rows, core_support=3, instructions={})
    assert missing["singletons"] == [] and dict(report.unresolved) == {"source task not in --input": 1}


def test_atomic_write_replaces_or_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = tmp_path / "nested" / "playbook.json"
    write_json_atomic(out, {"entries": []})
    assert json.loads(out.read_text()) == {"entries": []}
    assert oct(out.stat().st_mode & 0o777) == "0o644"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        write_json_atomic(out, {"entries": [{"r": "new"}]})
    with pytest.raises(TypeError):
        write_json_atomic(tmp_path / "other.json", {"bad": object()})
    assert json.loads(out.read_text()) == {"entries": []}
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == ["playbook.json"]
