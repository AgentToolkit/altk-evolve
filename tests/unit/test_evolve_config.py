import pytest

from altk_evolve.config.evolve import EvolveConfig

pytestmark = pytest.mark.unit


def test_segmentation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EVOLVE_SEGMENTATION_ENABLED", raising=False)

    assert EvolveConfig(_env_file=None).segmentation_enabled is False


def test_segmentation_can_be_enabled_from_environment(monkeypatch):
    monkeypatch.setenv("EVOLVE_SEGMENTATION_ENABLED", "true")

    assert EvolveConfig(_env_file=None).segmentation_enabled is True
