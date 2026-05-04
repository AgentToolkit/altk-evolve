from pathlib import Path

import pytest

from altk_evolve.backend.filesystem import FilesystemEntityBackend
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient


@pytest.fixture
def client(tmp_path: Path) -> EvolveClient:
    return EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))


@pytest.fixture
def backend(tmp_path: Path) -> FilesystemEntityBackend:
    return FilesystemEntityBackend(config=FilesystemSettings(data_dir=str(tmp_path)))


@pytest.mark.unit
def test_ensure_namespace_recovers_from_zero_byte_file(client: EvolveClient, tmp_path: Path):
    stale = tmp_path / "ns_stale.json"
    stale.write_text("")
    assert stale.exists() and stale.stat().st_size == 0

    ns = client.ensure_namespace("ns_stale")

    assert ns.id == "ns_stale"
    assert ns.num_entities == 0
    assert stale.exists() and stale.stat().st_size > 0


@pytest.mark.unit
def test_ensure_namespace_recovers_from_corrupt_json(client: EvolveClient, tmp_path: Path):
    corrupt = tmp_path / "ns_corrupt.json"
    corrupt.write_text("{not json")

    ns = client.ensure_namespace("ns_corrupt")

    assert ns.id == "ns_corrupt"
    assert ns.num_entities == 0


@pytest.mark.unit
def test_ensure_namespace_recovers_from_schema_invalid_json(client: EvolveClient, tmp_path: Path):
    """Valid JSON that doesn't match FilesystemNamespace must also trigger recovery,
    not propagate pydantic.ValidationError and wedge startup."""
    bogus = tmp_path / "ns_bogus.json"
    bogus.write_text('{"not_a_namespace": true}')

    ns = client.ensure_namespace("ns_bogus")

    assert ns.id == "ns_bogus"
    assert ns.num_entities == 0


@pytest.mark.unit
def test_save_tolerates_stale_shared_tmp(backend: FilesystemEntityBackend, tmp_path: Path):
    """Stale <ns>.json.tmp from an interrupted write must not block subsequent saves.

    Regression guard for concurrent CLI+MCP writers that used to share the tmp name.
    """
    (tmp_path / "ns_busy.json.tmp").write_text("leftover")

    backend.create_namespace("ns_busy")

    target = tmp_path / "ns_busy.json"
    assert target.exists() and target.stat().st_size > 0
