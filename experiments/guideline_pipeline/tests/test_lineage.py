"""Lineage from guidelines mined through the library's guideline processor, and without sources."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import Trajectory
from altk_evolve.schema.core import RecordedEntity

from experiments.guideline_pipeline.guidelines import fetch_guidelines, guideline_rows
from experiments.guideline_pipeline.stages.export import build_retrieval_index
from experiments.guideline_pipeline.stages.lineage import build_lineage, load_instructions, task_instruction
from experiments.guideline_pipeline.stages.mine import mine

from experiments.guideline_pipeline.tests.fakes import FakeAdapter, store_guidelines

pytestmark = pytest.mark.unit


def mined(client: EvolveClient, dataset: Path):
    report = mine(FakeAdapter().records(dataset), client, namespace_id="memories", processing_profile="guidelines")
    assert (report.processed, report.failures) == (3, [])
    return guideline_rows(fetch_guidelines(client, "memories"))[0]


def test_mined_guidelines_carry_their_source_task(client: EvolveClient, dataset: Path):
    document, report = build_lineage(mined(client, dataset), "memories")

    assert (document["count"], document["recorded"], document["missing"]) == (3, 3, 0)
    entry = next(e for e in document["guidelines"] if e["source_task_ids"] == ["task-2"])
    assert entry["guideline"] == "For tasks like 'do task-2', check the answer 'wrong'."
    assert (entry["support"], entry["evidence"], entry["sources_origin"], entry["provenance_incomplete"]) == (
        1,
        "failure",
        "recorded",
        False,
    )
    [source] = entry["sources"]
    assert {k: source[k] for k in ("conversation_id", "task_id", "status")} == {
        "conversation_id": None,
        "task_id": "task-2",
        "status": "supporting",
    }
    assert "instruction" not in source  # sources never store the task instruction
    assert entry["task_description"] == "do task-2"
    assert entry["processing"]["profile_id"] == "guidelines"
    assert entry["processing"]["source_batch"]["conversation_id"] == "task-2"
    assert report.summary() == (
        "lineage: 3 guidelines (3 with recorded sources, 0 derived from source_task_id, 0 without sources); 3 distinct source tasks"
    )


def test_instructions_come_from_the_dataset_when_given(client: EvolveClient, dataset: Path):
    rows = mined(client, dataset)
    instructions = load_instructions(FakeAdapter().records(dataset))
    assert instructions == {"task-1": "do task-1", "task-2": "do task-2", "task-3": "do task-3"}

    document, report = build_lineage(rows, "memories", instructions={"task-1": "do task-1"})
    by_task = {e["source_task_ids"][0]: e["sources"][0] for e in document["guidelines"]}
    assert by_task["task-1"]["instruction"] == "do task-1" and "instruction" not in by_task["task-2"]
    assert report.summary().endswith("3 distinct source tasks, 1 found in --input")

    index, _ = build_retrieval_index(rows, core_support=3, instructions=instructions)
    assert {(s["source_task"], s["source_instruction"]) for s in index["singletons"]} == set(instructions.items())


def test_guidelines_without_sources_are_reported(client: EvolveClient):
    # What consolidation writes on this version of Evolve: no source_task_id, no sources.
    store_guidelines(client, "memories", ("merged rule", {"support": 2, "evidence": "both", "task_description": "first task"}))
    legacy = RecordedEntity(
        id="legacy", type="guideline", content="legacy rule", metadata={"source_task_id": "task-9"}, created_at=datetime.now(UTC)
    )
    rows, _ = guideline_rows([*fetch_guidelines(client, "memories"), legacy])

    document, report = build_lineage(rows, "memories")

    merged, old = document["guidelines"]
    assert (merged["sources_origin"], merged["sources"], merged["source_task_ids"], merged["provenance_incomplete"]) == (
        "missing",
        [],
        [],
        True,
    )
    assert merged["processing"] is None
    assert (old["sources_origin"], old["source_task_ids"]) == ("derived", ["task-9"])
    assert (document["recorded"], document["derived"], document["missing"]) == (0, 1, 1)
    assert "1 derived from source_task_id, 1 without sources" in report.summary()


def test_task_instruction_is_the_first_user_message_context_first():
    def trajectory(messages, context=()):
        return Trajectory(messages=messages, context_messages=list(context), trace_id="t")

    user, other = {"role": "user", "content": " the task "}, {"role": "user", "content": "follow-up"}
    assert task_instruction(trajectory([{"role": "system", "content": "sys"}, user, other])) == "the task"
    assert task_instruction(trajectory([other], context=[user])) == "the task"
    assert task_instruction(trajectory([{"role": "assistant", "content": "hi"}])) is None
    assert task_instruction(trajectory([{"role": "user", "content": [{"type": "text"}]}, other])) is None

    first, second = trajectory([user]), trajectory([other])
    assert load_instructions([first, second]) == {"t": "the task"}
