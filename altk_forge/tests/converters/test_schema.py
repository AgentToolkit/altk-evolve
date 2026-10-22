import pytest

from altk_forge.converters.schema import compare_schema, decode, infer_schema, merge_schema, schema_paths, verdict_of


def test_decode_parses_json_hidden_in_strings():
    raw = {"a": '{"b": "[1, 2]"}', "c": "{not json}", "d": "plain"}
    assert decode(raw) == {"a": {"b": [1, 2]}, "c": "{not json}", "d": "plain"}


def test_infer_schema_types_required_and_items():
    s = infer_schema({"name": "x", "n": 1, "gone": None, "items": [{"a": 1}, {"a": 2, "b": "x"}]})
    assert s["type"] == "object"
    assert s["required"] == ["items", "n", "name"]
    assert s["properties"]["gone"] == {"type": "null"}
    item = s["properties"]["items"]["items"]
    assert item["required"] == ["a"] and set(item["properties"]) == {"a", "b"}


def test_id_keys_become_a_map_and_payloads_keep_only_their_type():
    s = infer_schema({"by_id": {"0123456789abcdef": {"x": 1}, "9.60.1.2;db": {"x": 2}}, "data": [{"col": 1}]})
    by_id = s["properties"]["by_id"]
    assert "properties" not in by_id and by_id["additionalProperties"]["properties"] == {"x": {"type": "number"}}
    assert s["properties"]["data"] == {"type": "array"}


def test_merge_schema_keeps_required_only_where_every_object_had_it():
    m = merge_schema(infer_schema({"a": 1, "b": 2}), infer_schema({"a": "x"}))
    assert m["required"] == ["a"]
    assert m["properties"]["a"]["type"] == ["number", "string"]


@pytest.mark.parametrize(
    "new, verdict",
    [
        ({"a": 1, "b": {"c": "x"}}, "same"),
        ({"a": 1}, "same"),  # narrower: a key every earlier trace had is missing
        ({"a": 1, "b": {"c": "x"}, "extra": True}, "wider"),
        ({"a": "one", "b": {"c": "x"}}, "different"),
    ],
)
def test_compare_schema_verdicts(new, verdict):
    known = infer_schema({"a": 1, "b": {"c": "x"}})
    diff = compare_schema(infer_schema(new), known)
    assert verdict_of(diff) == verdict


def test_compare_schema_reports_paths():
    known = infer_schema({"obs": [{"role": "user"}]})
    diff = compare_schema(infer_schema({"obs": [{"role": 1, "new": "x"}]}), known)
    assert diff["changed"] == ["obs[].role: was string, now number"]
    assert diff["added"] == ["obs[].new: string"]


def test_a_type_where_only_null_was_seen_is_an_addition():
    diff = compare_schema(infer_schema({"a": "x"}), infer_schema({"a": None}))
    assert verdict_of(diff) == "wider"


def test_schema_paths():
    s = infer_schema({"a": {"b": 1}, "l": [{"c": 1}]})
    assert sorted(schema_paths(s)) == ["a", "a.b", "l", "l[].c"]
