import json
from pathlib import Path

import pytest

from altk_evolve.llm.guidelines.segmentation import segment_trajectory

pytestmark = pytest.mark.e2e

TRAJECTORY = json.loads((Path(__file__).parent.parent / "fixtures" / "appworld_venmo_task_trajectory.json").read_text())


def test_segment_trajectory_min_subtasks():
    """Real trajectory produces at least 7 subtasks (gold standard has 7)."""
    subtasks = segment_trajectory(TRAJECTORY)
    assert len(subtasks) >= 7, f"Expected >= 7 subtasks, got {len(subtasks)}: {[s.generalized_description for s in subtasks]}"
