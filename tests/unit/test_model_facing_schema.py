"""Response schemas sent to an LLM carry no numeric bounds; local validation still enforces them."""

import pytest
from litellm.utils import type_to_response_format_param
from pydantic import BaseModel, Field, ValidationError, create_model

from altk_evolve.schema.guidelines import (
    ConsolidatedGuidelineResponse,
    GuidelineGenerationResponse,
    SegmentationResponse,
    _ModelFacingSchema,
)

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


def _unstripped(model):
    """The schema pydantic generates, before the model-facing override."""
    return BaseModel.model_json_schema.__func__(model)


def _leaves(node, path=()):
    """Every leaf of a JSON value, keyed by its path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, path + (index,))
    else:
        yield path, node


def _objects(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from _objects(value)


def _assert_only_bounds_dropped(model):
    """Independent of the stripping code: the sent schema is the generated one minus bound keywords.

    Every leaf of the generated schema is kept with its value, unless the key it sits under is a
    bound keyword; nothing is added; every required name is a property; every $ref resolves.
    """
    before, after = dict(_leaves(_unstripped(model))), dict(_leaves(model.model_json_schema()))
    dropped = {path for path in before if path not in after}
    assert all(path[-1] in BOUNDS for path in dropped), sorted(dropped)
    assert all(after[path] == before[path] for path in after), "a kept value changed"
    assert set(after) <= set(before), "keys were added"
    schema = model.model_json_schema()
    for node in _objects(schema):
        if isinstance(node.get("properties"), dict) and isinstance(node.get("required"), list):
            assert set(node["required"]) <= set(node["properties"]), node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            assert ref.removeprefix("#/$defs/") in schema.get("$defs", {}), ref


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
def test_schema_keeps_everything_but_the_bounds(response):
    # The model is still told every field, type, requirement and description.
    _assert_only_bounds_dropped(response)
    assert set(response.model_json_schema()["properties"]) == set(response.model_fields)


# A model whose class name, and so its $defs name, is a bound keyword. Built with create_model so
# the Python name doesn't clash with the field called minimum below.
_NamedMinimum = create_model("minimum", x=(int, Field(ge=1)))


class _Collisions(_ModelFacingSchema):
    """Names and data that look like bound keywords, which only schema nodes may lose."""

    minimum: int = Field(ge=0)
    maximum: str = "x"
    cfg: dict = Field(default={"maximum": 3, "multipleOf": 2})
    nested: _NamedMinimum  # type: ignore[valid-type]
    pick: int = Field(default=1, json_schema_extra={"examples": [{"minimum": 5}]})


def test_a_field_named_like_a_bound_keeps_its_place():
    schema = _Collisions.model_json_schema()
    assert {"minimum", "maximum"} <= set(schema["properties"])
    assert set(schema["required"]) <= set(schema["properties"])
    assert "minimum" not in schema["properties"]["minimum"]  # its own bound is still dropped


def test_data_that_contains_a_bound_keyword_is_kept():
    properties = _Collisions.model_json_schema()["properties"]
    assert properties["cfg"]["default"] == {"maximum": 3, "multipleOf": 2}
    assert properties["pick"]["examples"] == [{"minimum": 5}]


def test_a_model_named_like_a_bound_keeps_its_definition():
    schema = _Collisions.model_json_schema()
    assert schema["properties"]["nested"]["$ref"] == "#/$defs/minimum"
    assert "x" in schema["$defs"]["minimum"]["properties"]
    assert "minimum" not in schema["$defs"]["minimum"]["properties"]["x"]


def test_collisions_lose_only_their_bounds():
    _assert_only_bounds_dropped(_Collisions)


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
