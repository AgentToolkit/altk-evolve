import pytest

from altk_forge.sources import filters


def test_clean_criteria_drops_empty_values_and_types_conditions():
    c = filters.clean_criteria(
        {
            "name": {"op": "starts with", "value": " bot "},
            "user_ids": "u1, u2",
            "tags": {"values": []},
            "bookmarked": "false",
            "conditions": [
                {"column": "latency", "operator": ">=", "value": "1.5"},
                {"column": "metadata", "key": " env ", "operator": "=", "value": "prod"},
                {"column": "level", "operator": "any of", "value": "ERROR,WARNING"},
            ],
        }
    )
    assert c["name"] == {"op": "starts with", "value": "bot"}
    assert c["user_ids"] == ["u1", "u2"]
    assert "tags" not in c
    assert c["bookmarked"] is False
    assert c["conditions"] == [
        {"column": "latency", "operator": ">=", "value": 1.5},
        {"column": "metadata", "key": "env", "operator": "=", "value": "prod"},
        {"column": "level", "operator": "any of", "value": ["ERROR", "WARNING"]},
    ]


@pytest.mark.parametrize(
    "criteria, message",
    [
        ({"name": {"op": "like", "value": "x"}}, "Unknown name operator"),
        ({"from": "2026-02-01", "to": "2026-01-01"}, "'From' is after 'To'"),
        ({"conditions": [{"column": "nope", "operator": "="}]}, "unknown column"),
        ({"conditions": [{"column": "latency", "operator": "contains", "value": 1}]}, "takes"),
        ({"conditions": [{"column": "metadata", "operator": "=", "value": "x"}]}, "needs a metadata key"),
        ({"conditions": [{"column": "latency", "operator": ">", "value": "slow"}]}, "needs a number"),
    ],
)
def test_clean_criteria_rejects_bad_input(criteria, message):
    with pytest.raises(filters.FilterError, match=message):
        filters.clean_criteria(criteria)


def test_langfuse_conditions():
    criteria = filters.clean_criteria(
        {
            "name": {"op": "=", "value": "bot"},
            "from": "2026-01-01T00:00:00Z",
            "environments": ["prod"],
            "tags": {"op": "all of", "values": ["a"]},
            "conditions": [{"column": "scores_avg", "key": "correctness", "operator": ">", "value": 50}],
        }
    )
    conds = filters.langfuse_conditions(criteria, since="2026-03-01T00:00:00Z")
    assert {"type": "string", "column": "name", "operator": "=", "value": "bot"} in conds
    assert {"type": "datetime", "column": "timestamp", "operator": ">=", "value": "2026-03-01T00:00:00Z"} in conds
    assert {"type": "stringOptions", "column": "environment", "operator": "any of", "value": ["prod"]} in conds
    assert {"type": "arrayOptions", "column": "tags", "operator": "all of", "value": ["a"]} in conds
    assert {"type": "numberObject", "column": "scores_avg", "operator": ">", "value": 50.0, "key": "correctness"} in conds
