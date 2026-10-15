"""Categorical entropy contracts without pandas or SciPy at test/runtime."""

import math
import numpy as np

from hypothesis import given, settings, strategies as st
import pytest

from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import (
    CategoricalEntropyConsistencyMetric,
    get_consistency_by_metric,
)

pytestmark = pytest.mark.unit


@given(st.lists(st.integers(min_value=1, max_value=30), min_size=1, max_size=30))
@settings(max_examples=500)
def test_category_frequencies_match_entropy(counts):
    samples = [str(category) for category, count in enumerate(counts) for _ in range(count)]
    result = get_consistency_by_metric(samples, "cat_entropy")
    if len(samples) == 1:
        assert result == (-1.0, -1.0)
        return
    n = len(samples)
    expected = sum(count * math.log2(n / count) for count in counts) / (n * math.log2(n))
    assert result == pytest.approx((1 - expected, expected), abs=1e-12)
    assert result == get_consistency_by_metric(samples[::-1], "cat_entropy")


@given(st.lists(st.one_of(st.none(), st.booleans(), st.integers(), st.floats(), st.text()), max_size=50))
@settings(max_examples=500)
def test_dispatcher_preserves_string_categories(samples):
    result = get_consistency_by_metric(samples, "cat_entropy")
    assert result == get_consistency_by_metric([str(value) for value in samples], "cat_entropy")
    if len(samples) > 1:
        assert all(0 <= value <= 1 for value in result)
        assert sum(result) == pytest.approx(1)


@pytest.mark.parametrize(
    "samples, expected",
    [
        ([], (-1, -1)),
        ([None], (-1, -1)),
        ([None, float("nan")], (1, 0)),
        ([float("nan"), "a"], (1, 0)),
        ([None, "a", "b"], (1 - 1 / math.log2(3), 1 / math.log2(3))),
        ([1, 1.0], (1, 0)),
        ([2**53, 2**53 + 1, 0.5], (0.42061983571430495, 0.579380164285695)),
        ([2**53, 2**53 + 1, None], (1, 0)),
        ([[1], [1], [2]], (0.42061983571430495, 0.579380164285695)),
        ([{"a": 1}, {"a": 1}], (1, 0)),
    ],
)
def test_direct_call_legacy_results(samples, expected):
    assert CategoricalEntropyConsistencyMetric().get_consistency_and_distance(samples) == pytest.approx(expected)


@pytest.mark.parametrize("kind", [np.datetime64, np.timedelta64])
def test_missing_temporal_values_do_not_form_categories(kind):
    samples = [kind("NaT"), kind(1, "D"), kind(2, "D")]
    distance = 1 / math.log2(3)
    assert CategoricalEntropyConsistencyMetric().get_consistency_and_distance(samples) == pytest.approx((1 - distance, distance))
