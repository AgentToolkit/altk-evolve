"""Processing contracts tested without model calls or external databases."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from pydantic import BaseModel, ConfigDict, Field

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import (
    InMemoryProfileRepository,
    ProcessingError,
    ProcessingManager,
    ProcessorRegistry,
    ProcessorResult,
    ProfileConflict,
    ProfileNotFound,
    ProfileReference,
    SQLiteProfileRepository,
)
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit


class EchoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = "default"
    values: list[int] = Field(default_factory=lambda: [1])


class EchoProcessor:
    id = "tests.echo"
    api_version = 1
    version = "1"
    config_model = EchoConfig

    def __init__(self, config):
        self.config = config

    @classmethod
    def from_config(cls, config):
        return cls(config)

    def process(self, trajectory, *, context):
        config = self.config
        config.values.append(2)
        return ProcessorResult(
            entities=[Entity(type="note", content=config.label, metadata={"values": config.values, "processing": "forged"})]
        )


def definition(label="old", plugin="tests.echo"):
    return {"processors": [{"id": "first", "plugin": plugin, "config": {"label": label}}]}


def make_manager(repository=None, processor_type=EchoProcessor):
    registry = ProcessorRegistry()
    registry.register(processor_type)
    return ProcessingManager(registry=registry, repository=repository)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    manager = make_manager(SQLiteProfileRepository(tmp_path / "profiles.db"))
    client = EvolveClient(EvolveConfig(settings=FilesystemSettings(data_dir=str(tmp_path / "entities"))), processing=manager)
    client.create_namespace("memories")
    return client


@pytest.mark.parametrize("persistent", [False, True])
def test_revision_conflicts_and_old_revisions(tmp_path, persistent):
    repository = SQLiteProfileRepository(tmp_path / "profiles.db") if persistent else InMemoryProfileRepository()
    manager = make_manager(repository)
    manager.put("review", definition(), expected_revision=0)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(manager.put, "review", definition(label), expected_revision=1) for label in ("a", "b")]
    assert sum(isinstance(f.exception(), ProfileConflict) for f in futures) == 1
    assert manager.resolve("review").revision == 2
    assert manager.resolve("review", revision=1).manifest()["processors"][0]["config"]["label"] == "old"
    with pytest.raises(ProfileNotFound):
        manager.resolve("review", revision=99)
    with pytest.raises(ProcessingError):
        manager.put("review", {"processors": [{"id": "bad", "plugin": "tests.echo", "config": {"unknown": 1}}]}, expected_revision=2)
    assert manager.get("review")["revision"] == 2
    if persistent:
        restarted = make_manager(SQLiteProfileRepository(tmp_path / "profiles.db"))
        assert restarted.get("review") == manager.get("review")


def test_inflight_plan_isolation_and_latest_next(client):
    started, release = Event(), Event()

    class Blocking(EchoProcessor):
        def process(self, trajectory, *, context):
            if self.config.label == "old":
                started.set()
                assert release.wait(10)
            return super().process(trajectory, context=context)

    client._processing = make_manager(processor_type=Blocking)
    manager = client.processing
    manager.put("review", definition(), expected_revision=0)
    original = manager.resolve("review")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(client.process_trajectory, {"messages": []}, namespace_id="memories", plan=original)
        try:
            assert started.wait(10)
            manager.put("review", definition("new"), expected_revision=1)
        finally:
            release.set()
        first = future.result()
    second = client.process_trajectory({"messages": []}, namespace_id="memories", processing_profile="review")
    assert first.entities[0].content == "old"
    assert second.entities[0].content == "new"
    stored = client.get_all_entities("memories")
    assert {e.metadata["processing"]["revision"] for e in stored} == {1, 2}
    assert original.manifest()["processors"][0]["config"]["values"] == [1]
    assert second.entities[0].metadata["values"] == [1, 2]
    assert all(isinstance(e.metadata["processing"], dict) for e in stored)


def test_discovery_is_not_activation_and_failures_are_explicit(monkeypatch):
    entry = Mock(name="entry")
    entry.name = EchoProcessor.id
    entry.load.return_value = EchoProcessor
    monkeypatch.setattr("altk_evolve.processing.registry.entry_points", lambda **_: [entry])
    registry = ProcessorRegistry.discover(include_builtins=False)
    entry.load.assert_not_called()
    manager = ProcessingManager(registry=registry)
    plan = manager.validate(definition())
    entry.load.assert_called_once()
    assert plan.processor_types[0].id == EchoProcessor.id
    with pytest.raises(ProcessingError, match="Duplicate"):
        registry.register(EchoProcessor)
    with pytest.raises(ProcessingError, match="Cannot load"):
        manager.validate(definition(plugin="missing"))
    EchoProcessor.version = "2"
    try:
        # A plan already captured implementation/config, while profile resolves check versions.
        manager.put("review", definition(), expected_revision=0)
        EchoProcessor.version = "3"
        with pytest.raises(ProcessingError, match="version changed"):
            manager.resolve("review")
    finally:
        EchoProcessor.version = "1"


def test_isolated_input_and_no_persistence_when_processor_fails(client):
    class Fails(EchoProcessor):
        id = "tests.fail"

        def process(self, trajectory, *, context):
            raise RuntimeError("processor failed")

    client.processing.registry.register(Fails)
    data = definition()
    data["processors"].append({"id": "second", "plugin": Fails.id, "config": {}})
    plan = client.processing.validate(data)
    with pytest.raises(RuntimeError, match="processor failed"):
        client.process_trajectory({"messages": []}, namespace_id="memories", plan=plan)
    assert client.get_all_entities("memories") == []
    empty = client.processing.validate({"processors": []})
    assert client.process_trajectory({"messages": []}, namespace_id="memories", plan=empty).entities == []


def test_builtin_mode_and_config_are_captured(monkeypatch):
    from altk_evolve.processing.builtin import GuidelineProcessor
    from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult

    calls = []

    def generate(trajectory, *, options):
        calls.append(options)
        return [
            GuidelineGenerationResult(
                task_description="task",
                guidelines=[Guideline(content="Check assumptions", rationale="avoid errors", category="strategy", trigger="new task")],
            )
        ]

    monkeypatch.setattr("altk_evolve.llm.guidelines.consistency_guidelines.generate_consistency_guidelines_fast", generate)
    manager = make_manager(processor_type=GuidelineProcessor)
    plan = manager.validate(
        {
            "processors": [
                {
                    "id": "g",
                    "plugin": "evolve.guidelines",
                    "config": {
                        "guidelines_mode": "consistency",
                        "consistency_method": "fast",
                        "guidelines_model": "captured-model",
                        "segmentation_enabled": False,
                    },
                }
            ]
        }
    )
    result = manager.process({"messages": [{"role": "user", "content": "task"}]}, plan=plan)
    assert calls[0].guidelines_model == "captured-model"
    assert calls[0].segmentation_enabled is False
    assert result.entities[0].metadata["generation_method"] == "consistency-fast"
    with pytest.raises(ProcessingError):
        manager.validate({"processors": [{"id": "g", "plugin": "evolve.guidelines", "config": {"guidelines_mode": "typo"}}]})


def test_conflict_resolution_uses_captured_settings_and_stamps_after_model(client, monkeypatch):
    from altk_evolve.schema.conflict_resolution import EntityUpdate
    from altk_evolve.config.llm import llm_settings

    class Conflicts(EchoProcessor):
        def process(self, trajectory, *, context):
            result = super().process(trajectory, context=context)
            result.enable_conflict_resolution = True
            return result

    client._processing = make_manager(processor_type=Conflicts)
    monkeypatch.setenv("EVOLVE_CONFLICT_RESOLUTION_MODEL", "before")
    plan = client.processing.validate(definition())
    monkeypatch.setattr(llm_settings, "conflict_resolution_model", "after")

    def resolve(old, new, *, settings):
        assert settings.conflict_resolution_model == "before"
        return [EntityUpdate(id=new[0].id, type="note", content="resolved", event="ADD", metadata={"processing": "forged"})]

    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve)
    result = client.process_trajectory({"messages": []}, namespace_id="memories", plan=plan)
    assert client.get_all_entities("memories")[0].metadata["processing"]["operation_id"] == result.operation_id


def test_rest_mcp_cli_share_profile_manager(client, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner
    from altk_evolve.frontend.api.processing import router
    from altk_evolve.frontend.mcp import mcp_server
    from altk_evolve.cli.cli import app as cli

    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    monkeypatch.setattr("altk_evolve.cli.cli.get_client", lambda: client)
    app = FastAPI()
    app.include_router(router)
    http = TestClient(app)
    created = http.put("/processing-profiles/review", json=definition(), headers={"If-None-Match": "*"})
    assert created.status_code == 200
    assert created.headers["etag"] == '"1"'
    assert http.put("/processing-profiles/review", json=definition()).status_code == 428
    mcp_server.set_processing_profile("review", definition("mcp"), 1)
    assert http.put("/processing-profiles/review", json=definition(), headers={"If-Match": '"1"'}).status_code == 409
    assert http.get("/processing-profiles/missing").status_code == 404
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(definition("cli")))
    cli_result = CliRunner().invoke(cli, ["processing-profiles", "apply", "review", "--file", str(path), "--expected-revision", "2"])
    assert cli_result.exit_code == 0, cli_result.output
    response = http.post(
        "/trajectories", json={"namespace_id": "memories", "trajectory": {"messages": []}, "processing_profile": {"id": "review"}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["entities"][0]["content"] == "cli"
    assert mcp_server.get_processing_profile("review")["revision"] == 3
    assert mcp_server.list_processors()[0]["id"] == "tests.echo"
    assert client.get_all_entities("memories")[0].metadata["processing"]["revision"] == 3


def test_independent_application_scopes_share_destination(client):
    client.processing.put("agent-a", definition("A"), expected_revision=0)
    client.processing.put("user-b", definition("B"), expected_revision=0)
    for selection in [ProfileReference(id="agent-a"), ProfileReference(id="user-b")]:
        client.process_trajectory({"messages": []}, namespace_id="memories", processing_profile=selection)
    assert {e.content for e in client.get_all_entities("memories")} == {"A", "B"}


def test_application_selector_is_not_tied_to_namespace(client):
    client.processing.put("agent-a", definition("A"), expected_revision=0)
    client.processing.put("agent-b", definition("B"), expected_revision=0)
    client._processing_selector = lambda context: ProfileReference(id=context["agent"])
    result = client.process_trajectory({"messages": []}, namespace_id="memories", context={"agent": "agent-b"})
    assert result.entities[0].content == "B"


def test_mcp_ingestion_and_phoenix_sync_use_profiles(client, monkeypatch):
    from altk_evolve.frontend.mcp import mcp_server
    from altk_evolve.sync.phoenix_sync import PhoenixSync
    from unittest.mock import patch

    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    client.processing.put("review", definition("first"), expected_revision=0)
    with patch.object(mcp_server, "generate_guidelines", side_effect=AssertionError("legacy generation must not run")):
        saved = mcp_server.save_trajectory(
            json.dumps([{"role": "user", "content": "hello"}]), namespace_id="memories", task_id="mcp-task", processing_profile="review"
        )
    assert len(saved) == 1
    assert any(e.content == "first" for e in client.get_all_entities("memories"))
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", return_value=client):
        following = PhoenixSync(namespace_id="memories", processing_profile="review")
        pinned = PhoenixSync(namespace_id="memories", processing_profile="review", profile_revision=1)
    client.processing.put("review", definition("second"), expected_revision=1)
    trajectory = {
        "messages": [{"role": "user", "content": "hello"}],
        "trace_id": "trace",
        "span_id": "span",
        "model": "unknown",
        "timestamp": 0,
        "message_count": 1,
        "usage": {},
    }
    following._process_trajectory(trajectory)
    pinned._process_trajectory({**trajectory, "trace_id": "other-trace"})
    notes = [e for e in client.get_all_entities("memories") if e.type == "note"]
    assert [e.content for e in notes] == ["first", "second", "first"]


def test_validation_failure_and_repository_write_failure_leave_profile_unchanged():
    repository = InMemoryProfileRepository()
    manager = make_manager(repository)
    manager.put("review", definition("old"), expected_revision=0)
    original = manager.get("review")
    with pytest.raises(ProcessingError):
        manager.put("review", definition(plugin="not-installed"), expected_revision=1)
    assert manager.get("review") == original
    repository.put = Mock(side_effect=OSError("storage unavailable"))
    with pytest.raises(OSError):
        manager.put("review", definition("new"), expected_revision=1)
    assert manager.get("review") == original


def test_unknown_namespace_fails_before_processor_calls(client):
    from altk_evolve.schema.exceptions import NamespaceNotFoundException

    class Never(EchoProcessor):
        def process(self, trajectory, *, context):
            raise AssertionError("must validate destination before execution")

    client._processing = make_manager(processor_type=Never)
    plan = client.processing.validate(definition())
    with pytest.raises(NamespaceNotFoundException):
        client.process_trajectory({"messages": []}, namespace_id="missing", plan=plan)


def test_processor_owns_construction_and_instances_are_operation_local():
    calls = []

    class RequiresResource(EchoProcessor):
        def __init__(self, config, resource):
            super().__init__(config)
            self.resource = resource

        @classmethod
        def from_config(cls, config):
            resource = object()
            calls.append(resource)
            return cls(config, resource)

    manager = make_manager(processor_type=RequiresResource)
    manager.registry.inventory()
    manager.put("review", definition(), expected_revision=0)
    plan = manager.resolve("review")
    assert calls == []  # Discovery, schema validation, and resolution never construct instances.
    first = manager.process({"messages": []}, plan=plan)
    second = manager.process({"messages": []}, plan=plan)
    assert len(calls) == 2 and calls[0] is not calls[1]
    assert first.entities[0].metadata["values"] == second.entities[0].metadata["values"] == [1, 2]
    assert plan.manifest()["processors"][0]["config"]["values"] == [1]


def test_registration_requires_a_processor_owned_factory():
    class MissingFactory:
        id = "tests.missing-factory"
        api_version = 1
        version = "1"
        config_model = EchoConfig

    with pytest.raises(ProcessingError, match="from_config"):
        ProcessorRegistry().register(MissingFactory)


@pytest.mark.parametrize("field,value", [("process", None), ("version", ""), ("config_model", dict)])
def test_registry_rejects_incomplete_processor_classes(field, value):
    incomplete = type("Incomplete", (EchoProcessor,), {field: value})
    with pytest.raises(ProcessingError, match=field):
        ProcessorRegistry().register(incomplete)


def test_cli_reports_pinned_profile_setup_failure(monkeypatch):
    from typer.testing import CliRunner
    from altk_evolve.cli.cli import app
    from altk_evolve.sync import phoenix_sync

    monkeypatch.setattr(phoenix_sync, "PhoenixSync", Mock(side_effect=ProfileNotFound("missing@1")))
    result = CliRunner().invoke(app, ["sync", "phoenix", "--processing-profile", "missing", "--profile-revision", "1"])
    assert result.exit_code == 1
    assert "Sync failed: missing@1" in result.output


def test_mcp_profile_readback_filters_identity(client, monkeypatch):
    from altk_evolve.frontend.mcp import mcp_server

    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    client.processing.put("review", definition(), expected_revision=0)
    for user, session, agent in [("alice", "one", "a"), ("bob", "one", "a"), ("alice", "two", "a"), ("alice", "one", "b")]:
        saved = mcp_server.save_trajectory(
            json.dumps([{"role": "user", "content": f"{user}/{session}"}]),
            namespace_id="memories",
            task_id="same-task",
            user_id=user,
            session_id=session,
            agent_id=agent,
            processing_profile="review",
        )
        assert len(saved) == 1
        assert saved[0].metadata["user_id"] == user
        assert saved[0].metadata["session_id"] == session
        assert saved[0].metadata["agent_id"] == agent


@pytest.mark.parametrize("constrained", [False, True])
@pytest.mark.parametrize("pipeline", ["standard", "fast", "accurate", "segmentation"])
def test_schema_validation_is_per_call(pipeline, constrained, monkeypatch):
    from altk_evolve.config.guideline_runtime import GuidelineRuntime
    from altk_evolve.llm.guidelines import guidelines, consistency_guidelines, segmentation

    module = guidelines if pipeline == "standard" else segmentation if pipeline == "segmentation" else consistency_guidelines
    response = Mock()
    response.choices = [Mock(message=Mock(content=json.dumps({"subtasks": [], "guidelines": []})))]
    completion = Mock(return_value=response)
    monkeypatch.setattr(module, "completion", completion)
    options = GuidelineRuntime()
    if pipeline == "segmentation":
        monkeypatch.setattr(module, "get_supported_openai_params", lambda **kw: ["response_format"])
        monkeypatch.setattr(module, "supports_response_schema", lambda **kw: constrained)
        module.segment_trajectory([{"role": "user", "content": "hello"}], options=options)
    elif pipeline == "accurate":
        module._generate_guideline_result(
            messages=[],
            consistency_data={"step_uncertainties": {}},
            task_description="task",
            step_range=None,
            constrained_decoding_supported=constrained,
            debug_suffix="",
            config={"skip_on_no_uncertainty": False},
            options=options,
        )
    else:
        generate = module._generate_guidelines_for_segment if pipeline == "standard" else module._generate_fast_guideline_result
        generate(
            task_description="task", trajectory_slice="hello", num_steps=1, constrained_decoding_supported=constrained, options=options
        )
    assert completion.call_args.kwargs["enable_json_schema_validation"] is constrained


def test_invalid_profile_trajectory_does_not_write_raw_messages(client, monkeypatch):
    from altk_evolve.frontend.mcp import mcp_server

    monkeypatch.setattr(mcp_server, "get_client", lambda: client)
    client.processing.put("review", definition(), expected_revision=0)
    with pytest.raises(ValueError):
        mcp_server.save_trajectory(
            json.dumps([dict(role="user", content="hello")]), namespace_id="memories", processing_profile="review", tools="{}"
        )
    assert client.get_all_entities("memories") == []


def test_phoenix_marker_failure_rolls_back_outputs_and_retry_is_safe(client, monkeypatch):
    from unittest.mock import patch
    from altk_evolve.sync.phoenix_sync import PhoenixSync

    client.processing.put("review", definition(), expected_revision=0)
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", return_value=client):
        sync = PhoenixSync(namespace_id="memories", processing_profile="review")
    trajectory = dict(messages=[dict(role="user", content="hello")], trace_id="trace", span_id="span", model="unknown", timestamp=0)
    original = client.update_entities

    def fail_marker(namespace, entities, **kwargs):
        if entities[0].type == "trajectory":
            raise OSError("marker failed")
        return original(namespace, entities, **kwargs)

    monkeypatch.setattr(client, "update_entities", fail_marker)
    with pytest.raises(OSError, match="marker failed"):
        sync._process_trajectory(trajectory)
    assert client.get_all_entities("memories") == []
    monkeypatch.setattr(client, "update_entities", original)
    sync._process_trajectory(trajectory)
    # A repeat delivery (including a lost acknowledgement) must not rerun plugins.
    sync._process_trajectory(trajectory)
    assert sorted(e.type for e in client.get_all_entities("memories")) == ["note", "trajectory"]


def test_filesystem_transaction_rolls_back_updates_deletes_and_commit_failure(client, monkeypatch):
    from altk_evolve.backend import filesystem

    client.update_entities("memories", [Entity(type="note", content="existing")], enable_conflict_resolution=False)
    before = client.get_all_entities("memories")
    with pytest.raises(RuntimeError):
        with client.backend.transaction("memories"):
            client.patch_entity_metadata("memories", before[0].id, {"changed": True})
            client.delete_entity_by_id("memories", before[0].id)
            client.update_entities("memories", [Entity(type="note", content="replacement")], enable_conflict_resolution=False)
            assert client.get_all_entities("memories")[0].content == "replacement"
            raise RuntimeError("rollback")
    assert client.get_all_entities("memories") == before
    with monkeypatch.context() as patcher:
        patcher.setattr(filesystem.os, "replace", Mock(side_effect=OSError("commit failed")))
        with pytest.raises(OSError, match="commit failed"):
            with client.backend.transaction("memories"):
                client.update_entities("memories", [Entity(type="note", content="new")], enable_conflict_resolution=False)
    assert client.get_all_entities("memories") == before
    with client.backend.transaction("memories"):
        client.update_entities("memories", [Entity(type="note", content="new")], enable_conflict_resolution=False)
    assert len(client.get_all_entities("memories")) == 2


def test_concurrent_phoenix_retries_use_one_commit(client, monkeypatch):
    from unittest.mock import patch
    from altk_evolve.sync.phoenix_sync import PhoenixSync

    client.processing.put("review", definition(), expected_revision=0)
    other = EvolveClient(client.config, processing=client.processing)
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", side_effect=[client, other]):
        syncs = [PhoenixSync(namespace_id="memories", processing_profile="review") for _ in range(2)]
    trajectory = dict(messages=[dict(role="user", content="hello")], trace_id="trace", span_id="span", model="unknown", timestamp=0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda sync: sync._process_trajectory(trajectory), syncs))
    assert sorted(e.type for e in client.get_all_entities("memories")) == ["note", "trajectory"]


def test_unsupported_atomic_backend_fails_before_processing(client, monkeypatch):
    from altk_evolve.backend.base import BaseEntityBackend
    from altk_evolve.sync.phoenix_sync import PhoenixSync
    from unittest.mock import patch

    client.processing.put("review", definition(), expected_revision=0)
    monkeypatch.setattr(client.backend, "transaction", lambda namespace: BaseEntityBackend.transaction(client.backend, namespace))
    with patch("altk_evolve.sync.phoenix_sync.EvolveClient", return_value=client):
        sync = PhoenixSync(namespace_id="memories", processing_profile="review")
    process = Mock(side_effect=AssertionError("must not run"))
    monkeypatch.setattr(client, "process_trajectory", process)
    with pytest.raises(NotImplementedError, match="atomic namespace writes"):
        sync._process_trajectory(
            dict(messages=[dict(role="user", content="hi")], trace_id="trace", span_id="span", model="unknown", timestamp=0)
        )
    process.assert_not_called()
    assert client.get_all_entities("memories") == []


def test_filesystem_process_crash_rolls_back_and_releases_lock(client):
    import subprocess
    import sys
    from pathlib import Path

    script = """
import os, sys
from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.schema.core import Entity
backend = FilesystemEntityBackend(FilesystemSettings(data_dir=sys.argv[1]))
with backend.transaction("memories"):
    backend.update_entities("memories", [Entity(type="note", content="uncommitted")], False)
    os._exit(19)
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(client.backend.data_dir)],
        cwd=Path(__file__).resolve().parents[2],
        timeout=30,
        capture_output=True,
        text=True,
    )
    assert crashed.returncode == 19, crashed.stderr
    assert client.get_all_entities("memories") == []
    with client.backend.transaction("memories"):
        client.update_entities("memories", [Entity(type="note", content="retry")], enable_conflict_resolution=False)
    assert [entity.content for entity in client.get_all_entities("memories")] == ["retry"]


@pytest.mark.parametrize("mode", ["standard", "consistency", "all"])
@pytest.mark.parametrize("method", ["fast", "accurate"])
def test_builtin_factory_selects_generation_steps(mode, method, monkeypatch):
    from altk_evolve.processing.builtin import GuidelineConfig, GuidelineProcessor
    from altk_evolve.processing.models import ProcessorContext, Trajectory
    from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult

    calls = []

    def generator(name):
        def run(data, *, options):
            calls.append((name, data, options.guidelines_model))
            return [
                GuidelineGenerationResult(
                    task_description="task", guidelines=[Guideline(content=name, rationale="test", category="strategy", trigger="task")]
                )
            ]

        return run

    monkeypatch.setattr("altk_evolve.llm.guidelines.guidelines.generate_guidelines", generator("standard"))
    monkeypatch.setattr(
        "altk_evolve.llm.guidelines.consistency_guidelines.generate_consistency_guidelines_fast", generator("consistency-fast")
    )
    monkeypatch.setattr("altk_evolve.llm.guidelines.consistency_guidelines.generate_consistency_guidelines", generator("consistency"))
    processor = GuidelineProcessor.from_config(
        GuidelineConfig(
            guidelines_mode=mode,
            consistency_method=method,
            guidelines_model="captured",
            segmentation_enabled=False,
        )
    )
    assert calls == []  # Construction selects functions but does not execute generation.
    trajectory = Trajectory(messages=[{"role": "user", "content": "task"}], trace_id="trace")
    result = processor.process(trajectory, context=ProcessorContext("operation"))
    expected = (["standard"] if mode in ("standard", "all") else []) + (
        ["consistency-fast" if method == "fast" else "consistency"] if mode in ("consistency", "all") else []
    )
    assert [entity.metadata["generation_method"] for entity in result.entities] == expected
    assert [name for name, _, _ in calls] == expected
    for name, data, model in calls:
        assert data == (trajectory.messages if name == "standard" else trajectory.model_dump())
        assert model == "captured"
    assert result.enable_conflict_resolution is True


def test_builtin_admin_update_changes_next_trajectory_not_running_steps(monkeypatch):
    from altk_evolve.processing.builtin import GuidelineProcessor
    from altk_evolve.schema.guidelines import GuidelineGenerationResult

    started, resume = Event(), Event()
    calls = []

    def standard(messages, *, options):
        calls.append(("standard", options.guidelines_model))
        started.set()
        assert resume.wait(10)
        return [GuidelineGenerationResult(task_description="task", guidelines=[])]

    def fast(trajectory, *, options):
        calls.append(("fast", options.guidelines_model))
        return []

    def accurate(trajectory, *, options):
        calls.append(("accurate", options.guidelines_model))
        return []

    monkeypatch.setattr("altk_evolve.llm.guidelines.guidelines.generate_guidelines", standard)
    monkeypatch.setattr("altk_evolve.llm.guidelines.consistency_guidelines.generate_consistency_guidelines_fast", fast)
    monkeypatch.setattr("altk_evolve.llm.guidelines.consistency_guidelines.generate_consistency_guidelines", accurate)
    manager = make_manager(processor_type=GuidelineProcessor)

    def profile(method, model):
        return {
            "processors": [
                {
                    "id": "g",
                    "plugin": "evolve.guidelines",
                    "config": {
                        "guidelines_mode": "all",
                        "consistency_method": method,
                        "guidelines_model": model,
                        "segmentation_enabled": False,
                    },
                }
            ]
        }

    manager.put("review", profile("fast", "old-model"), expected_revision=0)
    pinned = manager.resolve("review", revision=1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(manager.process, {"messages": []}, plan=pinned)
        try:
            assert started.wait(10)
            manager.put("review", profile("accurate", "new-model"), expected_revision=1)
        finally:
            resume.set()
        running.result(timeout=10)
    assert calls == [("standard", "old-model"), ("fast", "old-model")]
    manager.process({"messages": []}, plan=manager.resolve("review"))
    assert calls[-2:] == [("standard", "new-model"), ("accurate", "new-model")]
    manager.process({"messages": []}, plan=pinned)
    assert calls[-2:] == [("standard", "old-model"), ("fast", "old-model")]


def test_default_profiles_share_existing_sqlite_metadata_database(tmp_path, monkeypatch):
    import sqlite3
    from altk_evolve.db.sqlite_manager import SQLiteManager

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    path = tmp_path / "metadata.sqlite"
    monkeypatch.setenv("EVOLVE_SQLITE_PATH", str(path))
    with SQLiteManager() as database:
        database.create_namespace("existing-metadata")
    config = EvolveConfig(settings=FilesystemSettings(data_dir=str(tmp_path / "entities")))
    first = EvolveClient(config)
    first.processing.put("review", {"processors": []}, expected_revision=0)
    second = EvolveClient(config)
    assert second.processing.get("review")["revision"] == 1
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT id FROM namespaces").fetchone()[0] == "existing-metadata"
        assert database.execute("SELECT id, revision FROM processing_profiles").fetchone() == ("review", 1)
    assert not (tmp_path / "entities.sqlite.db").exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_builtin_rejects_nonfinite_without_publishing(value):
    from altk_evolve.processing.builtin import GuidelineProcessor

    manager = make_manager(processor_type=GuidelineProcessor)
    profile = {
        "processors": [
            {
                "id": "g",
                "plugin": "evolve.guidelines",
                "config": {
                    "guidelines_mode": "consistency",
                    "consistency_method": "accurate",
                    "analysis_config": {"low_uncertainty_threshold": value},
                },
            }
        ]
    }
    with pytest.raises(ProcessingError, match="non-finite"):
        manager.put("bad", profile, expected_revision=0)
    with pytest.raises(ProfileNotFound):
        manager.get("bad")


def test_builtin_profile_roundtrip_and_independent_version(monkeypatch):
    import altk_evolve
    from altk_evolve.processing.builtin import GuidelineProcessor

    manager = make_manager(processor_type=GuidelineProcessor)
    profile = {
        "processors": [
            {
                "id": "g",
                "plugin": "evolve.guidelines",
                "config": {
                    "guidelines_mode": "consistency",
                    "consistency_method": "accurate",
                    "analysis_config": {},
                },
            }
        ]
    }
    saved = manager.put("review", profile, expected_revision=0)
    monkeypatch.setattr(altk_evolve, "__version__", "999.0.0")
    assert GuidelineProcessor.version == "1"
    assert manager.resolve("review").manifest() == saved["manifest"]
    assert saved["manifest"]["processors"][0]["config"]["analysis_config"]["agents"]
    monkeypatch.setattr(GuidelineProcessor, "version", "2")
    with pytest.raises(ProcessingError, match="version changed"):
        manager.resolve("review")


@pytest.mark.parametrize(
    "config", [{"max_samples": 100000}, {"max_samples": 1.5}, {"max_steps": 0}, {"agents": []}, {"high_uncertainty_threshold": 2}]
)
def test_analysis_config_rejects_invalid_controls(config):
    from altk_evolve.processing.builtin import GuidelineConfig

    with pytest.raises(ValueError):
        GuidelineConfig(guidelines_mode="consistency", consistency_method="accurate", analysis_config=config)


def test_reference_revision_argument_is_honored():
    manager = make_manager()
    manager.put("p", definition(), expected_revision=0)
    manager.put("p", definition("new"), expected_revision=1)
    assert manager.resolve(ProfileReference(id="p"), revision=1).revision == 1
    with pytest.raises(ProcessingError, match="Conflicting"):
        manager.resolve(ProfileReference(id="p", revision=2), revision=1)
    with pytest.raises(ValueError):
        manager.get("p", 0)


@pytest.mark.parametrize("manifest", [{}, {"processors": None}, {"processors": [{}]}, {"processors": [], "conflict_resolution": []}])
def test_malformed_stored_profile_is_processing_error(manifest):
    manager = make_manager()
    manager.repository.put("bad", manifest, expected_revision=0)
    with pytest.raises(ProcessingError):
        manager.resolve("bad")


def test_builtin_metadata_pins_support_and_matches_generation_fields():
    from altk_evolve.processing.builtin import GuidelineProcessor
    from altk_evolve.processing.models import ProcessorContext, Trajectory
    from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult

    guideline = Guideline(content="check", rationale="why", category="strategy", trigger="when", support=7, evidence="success")
    processor = GuidelineProcessor((("standard", lambda _: [GuidelineGenerationResult(task_description="task", guidelines=[guideline])]),))
    result = processor.process(Trajectory(messages=[], trace_id="trace"), context=ProcessorContext("operation"))
    assert result.entities[0].metadata == {
        "source_task_id": "trace",
        "task_description": "task",
        "category": guideline.category,
        "rationale": guideline.rationale,
        "trigger": guideline.trigger,
        "implementation_steps": guideline.implementation_steps,
        "generation_method": "standard",
        "support": 1,
    }


def test_provenance_is_independent_and_empty_plan_warns():
    manager = make_manager()
    profile = definition()
    profile["processors"].append({"id": "second", "plugin": "tests.echo"})
    result = manager.process({"messages": []}, plan=manager.validate(profile))
    result.entities[0].metadata["processing"]["manifest"]["processors"].clear()
    assert result.entities[1].metadata["processing"]["manifest"]["processors"]
    assert result.manifest["processors"]
    assert manager.process({"messages": []}, plan=manager.validate({"processors": []})).diagnostics["processing"]["warning"]


def test_plugin_system_exit_is_reported_and_inventory_needs_no_database(monkeypatch):
    from typer.testing import CliRunner
    from altk_evolve.cli.cli import app

    entry = Mock(name="entry")
    entry.name = "broken"
    entry.load.side_effect = SystemExit(2)
    monkeypatch.setattr("altk_evolve.processing.registry.entry_points", lambda **_: [entry])
    monkeypatch.setattr("altk_evolve.cli.processing.client", Mock(side_effect=AssertionError("database not needed")))
    result = CliRunner().invoke(app, ["processors", "list"])
    assert result.exit_code == 0, result.output
    assert "Cannot load processor broken" in result.output


def test_phoenix_validates_latest_profile_before_sync(client, monkeypatch):
    from altk_evolve.sync.phoenix_sync import PhoenixSync

    monkeypatch.setattr("altk_evolve.sync.phoenix_sync.EvolveClient", lambda: client)
    with pytest.raises(ProfileNotFound):
        PhoenixSync(processing_profile="typo")
    client.processing.put("p", definition(), expected_revision=0)
    sync = PhoenixSync(processing_profile="p")
    assert sync.processing_plan is None  # Still follows latest per trajectory.


def test_processor_completion_applies_egress_hook(monkeypatch):
    from altk_evolve.processing.models import ProcessorContext

    hook = Mock(return_value=[{"role": "user", "content": "redacted"}])
    completion = Mock(return_value="response")
    monkeypatch.setattr("altk_evolve.hooks.manager.dispatch_llm_pre_call", hook)
    monkeypatch.setattr("litellm.completion", completion)
    messages = [{"role": "user", "content": "private"}]
    assert ProcessorContext("op").complete(messages=messages, model="model", temperature=0) == "response"
    hook.assert_called_once_with(messages, purpose="trajectory_processor", model="model")
    completion.assert_called_once_with(messages=hook.return_value, model="model", temperature=0)


def test_non_roundtripping_plugin_config_never_publishes():
    from pydantic import field_validator

    class DriftingConfig(BaseModel):
        value: int

        @field_validator("value")
        @classmethod
        def increment(cls, value):
            return value + 1

    class DriftingProcessor(EchoProcessor):
        config_model = DriftingConfig

    manager = make_manager(processor_type=DriftingProcessor)
    with pytest.raises(ProcessingError, match="round-trip"):
        manager.put("bad", {"processors": [{"id": "p", "plugin": "tests.echo", "config": {"value": 1}}]}, expected_revision=0)
    with pytest.raises(ProfileNotFound):
        manager.get("bad")


def test_read_seams_reject_invalid_revision(client, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner
    from altk_evolve.frontend.api.processing import router
    from altk_evolve.cli.cli import app

    api = FastAPI()
    api.include_router(router)
    for revision in (0, -3):
        assert TestClient(api).get(f"/processing-profiles/p?revision={revision}").status_code == 422
        result = CliRunner().invoke(app, ["processing-profiles", "get", "p", "--revision", str(revision)])
        assert result.exit_code != 0
        assert "not in the range" in result.output


def test_cli_unknown_latest_profile_fails_before_fetch(client, monkeypatch):
    from typer.testing import CliRunner
    from altk_evolve.cli.cli import app

    monkeypatch.setattr("altk_evolve.sync.phoenix_sync.EvolveClient", lambda: client)
    result = CliRunner().invoke(app, ["sync", "phoenix", "--processing-profile", "typo"])
    assert result.exit_code == 1
    assert "Sync failed" in result.output
    assert "Profile not found" in result.output


@pytest.mark.parametrize("threshold", [0, True, False])
def test_profile_threshold_matches_accurate_generation_validation(threshold):
    from altk_evolve.processing.builtin import GuidelineConfig

    with pytest.raises(ValueError, match="high_uncertainty_threshold"):
        GuidelineConfig(
            guidelines_mode="consistency", consistency_method="accurate", analysis_config={"high_uncertainty_threshold": threshold}
        )


def test_profile_accepts_single_threshold_below_retired_low_default():
    from altk_evolve.processing.builtin import GuidelineConfig
    from altk_evolve.config.guideline_runtime import GuidelineRuntime

    config = GuidelineConfig(
        guidelines_mode="consistency", consistency_method="accurate", analysis_config={"high_uncertainty_threshold": 0.05}
    )
    assert config.analysis_config["high_uncertainty_threshold"] == 0.05
    assert "low_uncertainty_threshold" not in config.analysis_config
    assert GuidelineRuntime().segmentation_enabled is False


@pytest.mark.parametrize("response_type", ["text", "code"])
@pytest.mark.parametrize("metric", [None, "", " ", 1])
def test_agent_metric_required_before_profile_publication(response_type, metric):
    from altk_evolve.processing.builtin import GuidelineProcessor

    manager = make_manager(processor_type=GuidelineProcessor)
    agent = {"name": "agent", "response_type": response_type}
    if metric is not None:
        agent["metric"] = metric
    profile = {
        "processors": [
            {
                "id": "g",
                "plugin": "evolve.guidelines",
                "config": {
                    "guidelines_mode": "consistency",
                    "consistency_method": "accurate",
                    "analysis_config": {"agents": [agent]},
                },
            }
        ]
    }
    with pytest.raises(ProcessingError, match="requires a metric"):
        manager.put("review", profile, expected_revision=0)
    with pytest.raises(ProfileNotFound):
        manager.get("review")


@pytest.mark.parametrize(
    "agent",
    [
        {"name": "agent", "response_type": "text", "metric": "jaccard"},
        {"name": "agent", "response_type": "code", "metric": "sbert_large"},
        {"name": "agent", "response_type": "tool_calls", "fields": [{"name": "function_name", "metric": "jaccard"}]},
    ],
)
def test_valid_agent_metric_profiles_roundtrip(agent):
    from altk_evolve.processing.builtin import GuidelineProcessor

    manager = make_manager(processor_type=GuidelineProcessor)
    profile = {
        "processors": [
            {
                "id": "g",
                "plugin": "evolve.guidelines",
                "config": {
                    "guidelines_mode": "consistency",
                    "consistency_method": "accurate",
                    "analysis_config": {"agents": [agent]},
                },
            }
        ]
    }
    published = manager.put("review", profile, expected_revision=0)
    assert manager.resolve("review").manifest() == published["manifest"]


@pytest.mark.e2e
def test_mcp_transport_profile_revision_errors_and_valid_pins(client, monkeypatch):
    import asyncio
    from fastmcp import Client
    from altk_evolve.frontend.mcp import mcp_server as server

    monkeypatch.setattr(server, "get_client", lambda: client)
    client.processing.put("review", definition("old"), expected_revision=0)
    client.processing.put("review", definition("new"), expected_revision=1)

    async def exercise():
        async with Client(server.mcp) as mcp:
            for revision in (0, -1):
                for name, args in (
                    (
                        "process_trajectory",
                        {"trajectory": {"messages": []}, "namespace_id": "memories", "processing_profile": "review", "revision": revision},
                    ),
                    ("get_processing_profile", {"profile_id": "review", "revision": revision}),
                    (
                        "save_trajectory",
                        {"trajectory_data": "[]", "namespace_id": "memories", "processing_profile": "review", "profile_revision": revision},
                    ),
                ):
                    result = await mcp.call_tool_mcp(name, args)
                    assert result.isError
                    assert "revision must be at least 1" in result.content[0].text
            for extra, expected in (({}, "new"), ({"revision": 1}, "old")):
                result = await mcp.call_tool_mcp(
                    "process_trajectory",
                    {
                        "trajectory": {"messages": []},
                        "namespace_id": "memories",
                        "processing_profile": "review",
                        **extra,
                    },
                )
                assert not result.isError
                assert json.loads(result.content[0].text)["entities"][0]["content"] == expected

    asyncio.run(exercise())


@pytest.mark.parametrize("response_type", ["json", "react", "react_aw", "thought_code", "tool_calls"])
def test_structured_agent_requires_scoring_config(response_type):
    from altk_evolve.processing.builtin import GuidelineConfig

    agent = {"name": "agent", "response_type": response_type}
    kwargs = {"guidelines_mode": "consistency", "consistency_method": "accurate"}
    with pytest.raises(ValueError, match="requires a metric"):
        GuidelineConfig(**kwargs, analysis_config={"agents": [agent]})
    for scoring in (
        {"metric": "jaccard"},
        {"fields": [{"name": "x", "metric": "jaccard"}]},
        {"alternates": [{"fields": [{"name": "x", "metric": "jaccard"}]}]},
    ):
        GuidelineConfig(**kwargs, analysis_config={"agents": [{**agent, **scoring}]})


def test_phoenix_marker_checks_bypass_read_filters_and_verify_writes(client, monkeypatch):
    from altk_evolve.sync.phoenix_sync import PhoenixSync
    from altk_evolve.schema.exceptions import EvolveException

    client.processing.put("review", definition(), expected_revision=0)
    monkeypatch.setattr("altk_evolve.sync.phoenix_sync.EvolveClient", lambda: client)
    sync = PhoenixSync(namespace_id="memories", processing_profile="review")
    trajectory = dict(messages=[dict(role="user", content="hello")], trace_id="trace", span_id="span", model="unknown", timestamp=0)
    monkeypatch.setattr(
        "altk_evolve.backend.base.dispatch_memory_post_read",
        lambda backend, ns, entities, **kwargs: [e for e in entities if e.type != "trajectory"],
    )
    sync._process_trajectory(trajectory)
    sync._process_trajectory(trajectory)
    assert sorted(e.type for e in client.backend.scan_entities("memories")) == ["note", "trajectory"]
    monkeypatch.setattr(
        "altk_evolve.backend.base.dispatch_memory_pre_write", lambda backend, ns, entities: [e for e in entities if e.type != "trajectory"]
    )
    trajectory["trace_id"] = "new-trace"
    for _ in range(2):
        with pytest.raises(EvolveException, match="completion marker"):
            sync._process_trajectory(trajectory)
    assert sorted(e.type for e in client.backend.scan_entities("memories")) == ["note", "trajectory"]


def test_async_write_hook_reentrancy_preserves_unrelated_thread_isolation(tmp_path):
    # A subprocess timeout makes a lock regression fail instead of hanging pytest.
    import os
    import subprocess
    import sys
    from pathlib import Path
    from textwrap import dedent

    script = dedent("""
        import asyncio
        from contextlib import nullcontext
        from threading import Thread, Event
        from unittest.mock import patch
        from altk_evolve.backend.filesystem import FilesystemEntityBackend
        from altk_evolve.config.filesystem import FilesystemSettings
        from altk_evolve.schema.core import Entity
        from altk_evolve.hooks.manager import _run_sync

        backend = FilesystemEntityBackend(FilesystemSettings(data_dir="entities"))
        backend.create_namespace("ns")
        backend.update_entities("ns", [Entity(type="note", content="seed")], enable_conflict_resolution=False)

        async def main():
            for transactional in (False, True):
                started, finished = Event(), Event()
                observed = []
                def independent_reader():
                    started.set()
                    observed.extend(backend.scan_entities("ns"))
                    finished.set()
                reader = Thread(target=independent_reader)
                async def hook():
                    reader.start()
                    assert started.wait(2)
                    assert not finished.wait(0.05)
                    backend.update_entity_metadata("ns", "1", {"seen": transactional})
                    assert backend.scan_entities("ns")[0].metadata["seen"] == transactional
                    return [Entity(type="note", content="added")]
                def dispatch(*args):
                    return _run_sync(hook())
                with backend.transaction("ns") if transactional else nullcontext():
                    with patch("altk_evolve.backend.base.dispatch_memory_pre_write", dispatch):
                        backend.update_entities("ns", [Entity(type="note", content="input")], enable_conflict_resolution=False)
                reader.join(2)
                assert finished.is_set()
                assert observed[0].metadata["seen"] == transactional
        asyncio.run(main())
    """)
    root = str(Path(__file__).resolve().parents[2])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH", "")])), "EVOLVE_HOOKS_CONFIG": ""}
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
