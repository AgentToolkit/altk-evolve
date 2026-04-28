"""Tests for entity_io directory-resolution functions."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"))
import entity_io

pytestmark = pytest.mark.platform_integrations


# ---------------------------------------------------------------------------
# get_evolve_dir
# ---------------------------------------------------------------------------


def test_get_evolve_dir_default(monkeypatch, tmp_path):
    monkeypatch.delenv("EVOLVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert entity_io.get_evolve_dir() == Path(".evolve")


def test_get_evolve_dir_env_var(monkeypatch, tmp_path):
    custom = tmp_path / "my-evolve"
    monkeypatch.setenv("EVOLVE_DIR", str(custom))
    assert entity_io.get_evolve_dir() == custom


# ---------------------------------------------------------------------------
# find_entities_dir
# ---------------------------------------------------------------------------


def test_find_entities_dir_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("EVOLVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert entity_io.find_entities_dir() is None


def test_find_entities_dir_cwd_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("EVOLVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    entities = tmp_path / ".evolve" / "entities"
    entities.mkdir(parents=True)
    assert entity_io.find_entities_dir() == Path(".evolve") / "entities"


def test_find_entities_dir_uses_evolve_dir_env(monkeypatch, tmp_path):
    custom = tmp_path / "custom-evolve"
    entities = custom / "entities"
    entities.mkdir(parents=True)
    monkeypatch.setenv("EVOLVE_DIR", str(custom))
    assert entity_io.find_entities_dir() == entities


def test_find_entities_dir_env_missing_subdir_returns_none(monkeypatch, tmp_path):
    custom = tmp_path / "custom-evolve"
    custom.mkdir()
    # entities/ subdirectory does NOT exist
    monkeypatch.setenv("EVOLVE_DIR", str(custom))
    assert entity_io.find_entities_dir() is None


@pytest.mark.unit
def test_find_recall_entity_dirs_returns_entities_dir(monkeypatch, temp_project_dir):
    custom = temp_project_dir / "custom-evolve"
    entities = custom / "entities"
    entities.mkdir(parents=True)
    monkeypatch.setenv("EVOLVE_DIR", str(custom))
    assert entity_io.find_recall_entity_dirs() == [entities]


@pytest.mark.unit
def test_find_recall_entity_dirs_empty_when_entities_dir_missing(monkeypatch, temp_project_dir):
    custom = temp_project_dir / "custom-evolve"
    custom.mkdir(parents=True)
    monkeypatch.setenv("EVOLVE_DIR", str(custom))
    assert entity_io.find_recall_entity_dirs() == []


# ---------------------------------------------------------------------------
# get_default_entities_dir
# ---------------------------------------------------------------------------


def test_get_default_entities_dir_creates_cwd_path(monkeypatch, tmp_path):
    monkeypatch.delenv("EVOLVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    result = entity_io.get_default_entities_dir()
    expected = (tmp_path / ".evolve" / "entities").resolve()
    assert result == expected
    assert result.is_dir()


def test_get_default_entities_dir_uses_evolve_dir_env(monkeypatch, tmp_path):
    custom = tmp_path / "custom-evolve"
    monkeypatch.setenv("EVOLVE_DIR", str(custom))
    result = entity_io.get_default_entities_dir()
    expected = (custom / "entities").resolve()
    assert result == expected
    assert result.is_dir()


def test_get_default_entities_dir_idempotent(monkeypatch, tmp_path):
    monkeypatch.delenv("EVOLVE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    r1 = entity_io.get_default_entities_dir()
    r2 = entity_io.get_default_entities_dir()
    assert r1 == r2
