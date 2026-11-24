"""Response schemas sent to an LLM carry no numeric bounds; local validation still enforces them.
A reply whose list arrived as a string of JSON is repaired."""

import json
import logging

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


# A reply whose list arrived as a string of JSON (#488).

_ITEMS = {
    GuidelineGenerationResponse: ("guidelines", {"content": "c", "rationale": "r", "category": "strategy", "trigger": "t"}),
    ConsolidatedGuidelineResponse: (
        "guidelines",
        {"content": "c", "rationale": "r", "category": "strategy", "trigger": "t", "source_indices": [0]},
    ),
    SegmentationResponse: ("subtasks", {"generalized_description": "d", "start_step": 1, "end_step": 2, "purpose": "p"}),
}


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
@pytest.mark.parametrize("holds", ["object", "list"])
def test_a_list_sent_as_a_json_string_is_decoded(response, holds, caplog):
    """The string holds the whole answer, as claude-sonnet-5 sends it, or just the list."""
    key, item = _ITEMS[response]
    encoded = json.dumps({key: [item]} if holds == "object" else [item])
    with caplog.at_level(logging.INFO):
        result = response.model_validate_reply({key: encoded}, "test")
    assert len(getattr(result, key)) == 1
    assert f'Recovered test response after repair: decoded "{key}" from a JSON string.' in caplog.text


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
def test_a_well_formed_reply_is_not_reported_as_repaired(response, caplog):
    key, item = _ITEMS[response]
    with caplog.at_level(logging.INFO):
        result = response.model_validate_reply({key: [item]}, "test")
    assert len(getattr(result, key)) == 1
    assert "after repair" not in caplog.text


@pytest.mark.parametrize("value", ["not json", "3", json.dumps({"other": []})], ids=["not-json", "a-number", "another-key"])
def test_a_string_that_does_not_hold_the_list_raises_the_error_for_the_reply_as_sent(value):
    with pytest.raises(ValidationError) as excinfo:
        GuidelineGenerationResponse.model_validate_reply({"guidelines": value}, "test")
    assert excinfo.value.errors()[0]["input"] == value


_TEXT_FIELD = {
    GuidelineGenerationResponse: "content",
    ConsolidatedGuidelineResponse: "content",
    SegmentationResponse: "generalized_description",
}


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
def test_decoded_text_with_a_single_backslash_control_escape_is_refused(response, caplog):
    r"""C:\temp in text decoded from a JSON string would come back with a tab in it."""
    key, item = _ITEMS[response]
    inner = json.dumps([{**item, _TEXT_FIELD[response]: "Open C:@"}]).replace("@", "\\temp")
    with caplog.at_level(logging.WARNING), pytest.raises(ValidationError) as excinfo:
        response.model_validate_reply({key: inner}, "test")
    assert excinfo.value.errors()[0]["input"] == inner
    assert f'Discarding test response: "{key}" had to be decoded from a JSON string' in caplog.text
    assert "Recovered" not in caplog.text


@pytest.mark.parametrize("response", RESPONSES, ids=lambda r: r.__name__)
def test_decoded_text_keeps_an_escaped_backslash(response):
    key, item = _ITEMS[response]
    field = _TEXT_FIELD[response]
    result = response.model_validate_reply({key: json.dumps([{**item, field: r"Open C:\temp"}])}, "test")
    assert getattr(getattr(result, key)[0], field) == r"Open C:\temp"


def test_a_decoded_list_is_still_validated():
    bad = {"content": "c", "rationale": "r", "category": "strategy", "trigger": "t", "support": 0}
    with pytest.raises(ValidationError):
        GuidelineGenerationResponse.model_validate_reply({"guidelines": json.dumps([bad])}, "test")


class _Mixed(_ModelFacingSchema):
    note: str
    items: list[int]


def test_only_list_fields_are_decoded():
    decoded, names = _Mixed.decode_string_fields({"note": "[1]", "items": "[1, 2]"})
    assert names == ["items"]
    assert decoded == {"note": "[1]", "items": [1, 2]}


@pytest.mark.parametrize("payload", [[1], "x", None, {"guidelines": []}, {"guidelines": "not json"}])
def test_nothing_to_decode_leaves_the_payload_as_it_is(payload):
    assert GuidelineGenerationResponse.decode_string_fields(payload) == (payload, [])
