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
