"""Response schemas sent to an LLM carry no numeric bounds; local validation still enforces them."""

import json

import pytest
from litellm.utils import type_to_response_format_param
from pydantic import ValidationError

from altk_evolve.schema.guidelines import ConsolidatedGuidelineResponse, GuidelineGenerationResponse, SegmentationResponse

pytestmark = pytest.mark.unit

RESPONSES = [GuidelineGenerationResponse, ConsolidatedGuidelineResponse, SegmentationResponse]
BOUNDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf")


def _keys(schema):
    if isinstance(schema, dict):
        for key, value in schema.items():
            yield key
            yield from _keys(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from _keys(item)


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
def test_schema_sent_through_litellm_has_no_numeric_bounds(response):
    # What litellm puts in response_format: Bedrock refuses the request if it holds "minimum".
    sent = type_to_response_format_param(response)
    assert not set(BOUNDS) & set(_keys(sent))


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
def test_schema_keeps_its_fields(response):
    # Only bound keywords are dropped, so the model is still told every field and type.
    schema = json.dumps(response.model_json_schema())
    for word in ("properties", "required", "type"):
        assert f'"{word}"' in schema


def test_guideline_support_still_validated_locally():
    guideline = {"content": "c", "rationale": "r", "category": "strategy", "trigger": "t"}
    assert GuidelineGenerationResponse.model_validate({"guidelines": [guideline]}).guidelines[0].support == 1
    with pytest.raises(ValidationError):
        GuidelineGenerationResponse.model_validate({"guidelines": [{**guideline, "support": 0}]})


def test_segment_steps_still_validated_locally():
    segment = {"generalized_description": "d", "start_step": 1, "end_step": 2, "purpose": "p"}
    assert SegmentationResponse.model_validate({"subtasks": [segment]}).subtasks[0].end_step == 2
    with pytest.raises(ValidationError):
        SegmentationResponse.model_validate({"subtasks": [{**segment, "start_step": 0}]})
