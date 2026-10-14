"""The adapter contract and the mine stage, against a filesystem backend with an echo processor."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import ProfileNotFound, Trajectory, TrajectoryBatch

from experiments.guideline_pipeline.adapters import Adapter, AdapterRecord, get_adapter
from experiments.guideline_pipeline.stages.mine import mine

from experiments.guideline_pipeline.tests.fakes import FakeAdapter, profile

pytestmark = pytest.mark.unit


def run(client: EvolveClient | None, records, **kwargs):
    return mine(records, client, namespace_id="memories", processing_profile="echo", **kwargs)


def notes(client: EvolveClient) -> list:
    return sorted(client.get_all_entities("memories"), key=lambda e: str(e.content))


def test_record_requires_identity_and_converts_to_a_plain_trajectory():
    batch = TrajectoryBatch(source="fake", conversation_id="task-1", batch_id="attempt-1")
    with pytest.raises(ValidationError):
        AdapterRecord(messages=[], trace_id="task-1")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AdapterRecord(messages=[], batch=batch, trace_id="")
    record = AdapterRecord(messages=[{"role": "user", "content": "hi"}], batch=batch, trace_id="task-1", metadata={"split": "dev"})
    trajectory = record.to_trajectory()
    assert type(trajectory) is Trajectory
    assert trajectory.model_dump() == record.model_dump()


def test_fake_adapter_satisfies_the_protocol_and_streams(dataset: Path):
    adapter: Adapter = FakeAdapter()
    records = adapter.records(dataset)
    assert isinstance(records, Iterator)
    assert next(records).trace_id == "task-1"
    with pytest.raises(ValueError, match="Unknown adapter 'nope'"):
        get_adapter("nope")


def test_mine_processes_records_through_the_profile(client: EvolveClient, dataset: Path):
    report = run(client, FakeAdapter().records(dataset))

    assert (report.processed, report.skipped, report.failures) == (3, 0, [])
    assert (report.entities, report.updates, report.events) == (3, 3, {"ADD": 3})
    assert (report.profile_id, report.profile_revision) == ("echo", 1)
    stored = notes(client)
    assert [e.content for e in stored] == ["task-1: done", "task-2: wrong", "task-3: done"]
    assert [e.metadata["success"] for e in stored] == [True, False, True]
    assert {e.metadata["model"] for e in stored} == {"fake-model"}
    # Generated through the profile, so provenance and the source batch are stamped.
    processing = stored[1].metadata["processing"]
    assert (processing["profile_id"], processing["revision"]) == ("echo", 1)
    assert processing["source_batch"]["conversation_id"] == "task-2"


def test_rerun_skips_checkpointed_records_and_reprocesses_new_revisions(client: EvolveClient, dataset: Path):
    run(client, FakeAdapter().records(dataset))

    again = run(client, FakeAdapter().records(dataset))
    assert (again.processed, again.skipped, again.entities, again.updates) == (0, 3, 0, 0)
    assert again.summary().endswith("0 entities proposed, 0 updates")

    items = json.loads(dataset.read_text())
    items[1].update(answer="fixed", success=True, revision="2")
    dataset.write_text(json.dumps(items))
    corrected = run(client, FakeAdapter().records(dataset))
    assert (corrected.processed, corrected.skipped) == (1, 2)
    assert len(notes(client)) == 4


def test_failed_record_is_reported_and_the_run_continues(client: EvolveClient, dataset: Path):
    client.processing.put("echo", profile("v2", fail_on="task-2"), expected_revision=1)

    report = run(client, FakeAdapter().records(dataset))

    assert (report.processed, report.profile_revision) == (2, 2)
    assert report.failures == [("task-2", "RuntimeError: processor failed")]
    # The failed record left no checkpoint, so the next run retries only it.
    retry = run(client, FakeAdapter().records(dataset), revision=1)
    assert (retry.processed, retry.skipped, retry.failures) == (1, 2, [])


def test_pinned_revision_and_unknown_profile(client: EvolveClient, dataset: Path):
    client.processing.put("echo", profile("v2"), expected_revision=1)

    report = run(client, FakeAdapter().records(dataset), revision=1)

    assert report.profile_revision == 1
    assert {e.metadata["label"] for e in notes(client)} == {"v1"}
    with pytest.raises(ProfileNotFound):
        mine([], client, namespace_id="memories", processing_profile="missing")


def test_limit_and_adapter_failure(client: EvolveClient, dataset: Path):
    assert run(client, FakeAdapter().records(dataset), limit=2).processed == 2

    def broken() -> Iterator[AdapterRecord]:
        yield from FakeAdapter().records(dataset)
        raise OSError("truncated file")

    report = run(client, broken())
    assert (report.processed, report.skipped) == (1, 2)
    assert report.failures == [("<adapter>", "OSError: truncated file")]


def test_dry_run_validates_without_storage(dataset: Path):
    good, bad = list(FakeAdapter().records(dataset))[:2]
    bad.messages.append({"role": "tool", "content": object()})

    report = run(None, [good, bad])

    assert report.dry_run and report.processed == 1
    assert [trace_id for trace_id, _ in report.failures] == ["task-2"]
    assert report.summary() == "dry run: 1 valid, 1 failed"


def test_backend_without_atomic_writes_is_rejected_up_front(client: EvolveClient, dataset: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(type(client.backend), "supports_atomic_writes", False)
    with pytest.raises(ValueError, match="atomic writes"):
        run(client, FakeAdapter().records(dataset))
    assert "memories" not in {namespace.id for namespace in client.all_namespaces()}
