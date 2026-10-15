import json
from unittest.mock import MagicMock, patch

import pytest

from altk_evolve.schema.guidelines import SubtaskSegment

pytestmark = pytest.mark.unit

MESSAGES = [{"role": "user", "content": "Do something"}, {"role": "assistant", "content": "Step 1 done"}]

VALID_SEGMENTATION_JSON = json.dumps(
    {
        "subtasks": [
            {"generalized_description": "Authenticate with service", "start_step": 1, "end_step": 2, "purpose": "Get access"},
            {"generalized_description": "Retrieve data", "start_step": 3, "end_step": 5, "purpose": "Fetch records"},
        ]
    }
)


def _mock_completion(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices[0].message.content = content
    return resp


@patch("altk_evolve.llm.guidelines.segmentation.completion")
@patch("altk_evolve.llm.guidelines.segmentation.get_supported_openai_params", return_value=[])
@patch("altk_evolve.llm.guidelines.segmentation.supports_response_schema", return_value=False)
def test_segment_trajectory_returns_subtasks(mock_schema, mock_params, mock_completion):
    mock_completion.return_value = _mock_completion(VALID_SEGMENTATION_JSON)

    from altk_evolve.llm.guidelines.segmentation import segment_trajectory

    result = segment_trajectory(MESSAGES)

    assert len(result) == 2
    assert isinstance(result[0], SubtaskSegment)
    assert result[0].generalized_description == "Authenticate with service"
    assert result[0].start_step == 1
    assert result[0].end_step == 2
    assert result[1].generalized_description == "Retrieve data"


@patch("altk_evolve.llm.guidelines.segmentation.completion")
@patch("altk_evolve.llm.guidelines.segmentation.get_supported_openai_params", return_value=[])
@patch("altk_evolve.llm.guidelines.segmentation.supports_response_schema", return_value=False)
def test_segment_trajectory_propagates_non_parse_errors(mock_schema, mock_params, mock_completion):
    mock_completion.side_effect = Exception("LLM unavailable")

    from altk_evolve.llm.guidelines.segmentation import segment_trajectory

    with pytest.raises(Exception, match="LLM unavailable"):
        segment_trajectory(MESSAGES)


@patch("altk_evolve.llm.guidelines.segmentation.completion")
@patch("altk_evolve.llm.guidelines.segmentation.get_supported_openai_params", return_value=[])
@patch("altk_evolve.llm.guidelines.segmentation.supports_response_schema", return_value=False)
def test_segment_trajectory_retries_on_parse_failure(mock_schema, mock_params, mock_completion):
    bad = _mock_completion("not valid json at all")
    good = _mock_completion(VALID_SEGMENTATION_JSON)
    mock_completion.side_effect = [bad, bad, good]

    from altk_evolve.llm.guidelines.segmentation import segment_trajectory

    result = segment_trajectory(MESSAGES)

    assert len(result) == 2
    assert mock_completion.call_count == 3


@patch("altk_evolve.llm.guidelines.segmentation.completion")
@patch("altk_evolve.llm.guidelines.segmentation.get_supported_openai_params", return_value=[])
@patch("altk_evolve.llm.guidelines.segmentation.supports_response_schema", return_value=False)
def test_segment_trajectory_returns_empty_after_max_retries(mock_schema, mock_params, mock_completion):
    mock_completion.return_value = _mock_completion("not valid json at all")

    from altk_evolve.llm.guidelines.segmentation import segment_trajectory

    result = segment_trajectory(MESSAGES)

    assert result == []
    assert mock_completion.call_count == 3


def test_subtask_segment_rejects_inverted_range():
    from pydantic import ValidationError as PydanticValidationError

    from altk_evolve.schema.guidelines import SubtaskSegment

    with pytest.raises(PydanticValidationError, match="end_step must be >= start_step"):
        SubtaskSegment(
            generalized_description="Bad range",
            start_step=10,
            end_step=5,
            purpose="Should fail",
        )


def test_subtask_segment_accepts_valid_range():
    from altk_evolve.schema.guidelines import SubtaskSegment

    seg = SubtaskSegment(
        generalized_description="Good range",
        start_step=3,
        end_step=7,
        purpose="Should pass",
    )
    assert seg.start_step == 3
    assert seg.end_step == 7


def _seg(start: int, end: int, description: str = "d") -> SubtaskSegment:
    return SubtaskSegment(generalized_description=description, start_step=start, end_step=end, purpose="p")


def _parsed(steps: list[str], marker_index: int | None = None) -> dict:
    """The parts of parse_openai_agents_trajectory's output that subtask_slices reads."""
    return {"steps_list": steps, "marker_index": marker_index}


def test_subtask_slices_keeps_ranges_that_share_a_start():
    """Dedupe compares the whole clamped range: [1,2] and [1,4] are different slices."""
    from altk_evolve.llm.guidelines.segmentation import subtask_slices

    steps = [f"s{i}" for i in range(1, 7)]
    slices = subtask_slices([_seg(1, 2), _seg(1, 4), _seg(5, 6)], _parsed(steps))
    assert [s for _, s, _ in slices] == [["s1", "s2"], ["s1", "s2", "s3", "s4"], ["s5", "s6"]]


def test_subtask_slices_drops_ranges_that_share_an_end_only_when_equal():
    from altk_evolve.llm.guidelines.segmentation import subtask_slices

    steps = [f"s{i}" for i in range(1, 7)]
    slices = subtask_slices([_seg(1, 6), _seg(3, 6), _seg(1, 9)], _parsed(steps))
    assert [s for _, s, _ in slices] == [steps, ["s3", "s4", "s5", "s6"]]


@pytest.mark.parametrize(
    "ranges,expected",
    [
        pytest.param([], "verbatim request", id="no-subtasks"),
        pytest.param([(1, 3)], "Gen 0", id="one-range"),
        pytest.param([(1, 3), (1, 3)], "Gen 0", id="one-range-repeated"),
        pytest.param([(9, 9)], "verbatim request", id="out-of-range-only"),
    ],
)
def test_one_pass_task_description(ranges, expected):
    from altk_evolve.llm.guidelines.segmentation import one_pass_task_description, subtask_slices

    steps = [f"s{i}" for i in range(1, 7)]
    subtasks = [_seg(a, b, f"Gen {i}") for i, (a, b) in enumerate(ranges)]
    assert one_pass_task_description(subtask_slices(subtasks, _parsed(steps)), "verbatim request") == expected
