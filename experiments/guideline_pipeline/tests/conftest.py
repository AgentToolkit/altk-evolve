"""A filesystem-backed client whose only processor is the echo fake, plus a tiny dataset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import ProcessingManager, ProcessorRegistry, SQLiteProfileRepository

from experiments.guideline_pipeline.tests.fakes import EchoProcessor, ScriptedGuidelineProcessor, guideline_profile, profile


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EvolveClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    registry = ProcessorRegistry()
    registry.register(EchoProcessor)
    registry.register(ScriptedGuidelineProcessor)
    manager = ProcessingManager(registry=registry, repository=SQLiteProfileRepository(tmp_path / "profiles.db"))
    client = EvolveClient(
        EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path / "entities"))), processing=manager
    )
    client.processing.put("echo", profile(), expected_revision=0)
    client.processing.put("guidelines", guideline_profile(), expected_revision=0)
    return client


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(
        json.dumps(
            [
                {"task": "task-1", "answer": "done", "success": True},
                {"task": "task-2", "answer": "wrong", "success": False},
                {"task": "task-3", "answer": "done", "success": True},
            ]
        )
    )
    return path
