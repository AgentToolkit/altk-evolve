import pytest
from pydantic import ValidationError

from altk_evolve.config.evolve import EvolveConfig

pytestmark = pytest.mark.unit


def test_segmentation_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("EVOLVE_SEGMENTATION_ENABLED", raising=False)

    assert EvolveConfig(_env_file=None).segmentation_enabled is False


def test_segmentation_can_be_enabled_from_environment(monkeypatch):
    monkeypatch.setenv("EVOLVE_SEGMENTATION_ENABLED", "true")

    assert EvolveConfig(_env_file=None).segmentation_enabled is True


def test_trajectory_limits_default_to_the_previously_hard_coded_window(monkeypatch):
    for var in ("EVOLVE_TRAJECTORY_MAX_STEPS", "EVOLVE_TRAJECTORY_MAX_STEP_CHARS", "EVOLVE_TRAJECTORY_TAIL_STEPS"):
        monkeypatch.delenv(var, raising=False)

    config = EvolveConfig(_env_file=None)

    assert (config.trajectory_max_steps, config.trajectory_max_step_chars) == (50, 2000)
    # 0 keeps head-only truncation, so making the limits configurable changed no behaviour.
    assert config.trajectory_tail_steps == 0


def test_trajectory_limits_can_be_set_from_environment(monkeypatch):
    monkeypatch.setenv("EVOLVE_TRAJECTORY_MAX_STEPS", "120")
    monkeypatch.setenv("EVOLVE_TRAJECTORY_MAX_STEP_CHARS", "500")
    monkeypatch.setenv("EVOLVE_TRAJECTORY_TAIL_STEPS", "20")

    config = EvolveConfig(_env_file=None)

    assert (config.trajectory_max_steps, config.trajectory_max_step_chars, config.trajectory_tail_steps) == (120, 500, 20)


def test_a_tail_that_consumes_the_whole_step_budget_is_rejected(monkeypatch):
    """The tail is carved out of max_steps, so tail == max_steps would render no head at all."""
    monkeypatch.setenv("EVOLVE_TRAJECTORY_MAX_STEPS", "10")
    monkeypatch.setenv("EVOLVE_TRAJECTORY_TAIL_STEPS", "10")

    with pytest.raises(ValidationError, match="must be < trajectory_max_steps"):
        EvolveConfig(_env_file=None)
