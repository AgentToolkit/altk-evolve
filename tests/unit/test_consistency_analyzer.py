"""Unit tests for the vendored consistency_analyzer modules."""

import math

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# utils.py
# ---------------------------------------------------------------------------


class TestInvertListOfDictionaries:
    from altk_evolve.llm.guidelines.consistency_analyzer.utils import invert_list_of_dictionaries

    def test_basic_inversion(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import invert_list_of_dictionaries

        result = invert_list_of_dictionaries([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
        assert result == {"a": [1, 3], "b": [2, 4]}

    def test_empty_list(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import invert_list_of_dictionaries

        assert invert_list_of_dictionaries([]) == {}

    def test_single_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import invert_list_of_dictionaries

        assert invert_list_of_dictionaries([{"x": 10}]) == {"x": [10]}


class TestFlattenResponse:
    def test_flat_dict_unchanged(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import flatten_response

        assert flatten_response({"a": 1, "b": "x"}) == {"a": 1, "b": "x"}

    def test_nested_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import flatten_response

        result = flatten_response({"outer": {"inner": 42}})
        assert result == {"outer_inner": 42}

    def test_list_of_dicts_inverted(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import flatten_response

        result = flatten_response({"calls": [{"name": "add"}, {"name": "mul"}]})
        assert result == {"calls_name": ["add", "mul"]}

    def test_non_dict_passthrough(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import flatten_response

        assert flatten_response("hello") == "hello"
        assert flatten_response(42) == 42

    def test_list_of_primitives_kept(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import flatten_response

        result = flatten_response({"items": [1, 2, 3]})
        assert result == {"items": [1, 2, 3]}


class TestExtractFieldValuesFromResponses:
    def test_single_field_name(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import extract_field_values_from_responses

        responses = [{"action": "search"}, {"action": "click"}]
        result = extract_field_values_from_responses(responses, {"name": "action"})
        assert result == ["search", "click"]

    def test_multi_field_name_concatenated(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import extract_field_values_from_responses

        responses = [{"fn": "add", "args": "1,2"}, {"fn": "sub", "args": "3,4"}]
        result = extract_field_values_from_responses(responses, {"name": ["fn", "args"]})
        assert result == ["add1,2", "sub3,4"]

    def test_missing_field_returns_empty_string(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import extract_field_values_from_responses

        responses = [{"other": "x"}]
        result = extract_field_values_from_responses(responses, {"name": "action"})
        assert result == [""]

    def test_non_dict_response_returns_empty_string(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import extract_field_values_from_responses

        result = extract_field_values_from_responses(["not_a_dict"], {"name": "action"})
        assert result == [""]

    def test_list_value_joined(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import extract_field_values_from_responses

        responses = [{"tags": ["a", "b", "c"]}]
        result = extract_field_values_from_responses(responses, {"name": "tags"})
        assert result == ["a b c"]


class TestFindMatchingAlternate:
    def test_match_found(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import find_matching_alternate

        alternates = [{"fields": [{"name": "action"}, {"name": "thought"}]}]
        parsed = {"action": "click", "thought": "I should click"}
        result = find_matching_alternate(alternates, parsed)
        assert result == alternates[0]

    def test_no_match_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import find_matching_alternate

        alternates = [{"fields": [{"name": "final_ans"}]}]
        parsed = {"action": "click"}
        assert find_matching_alternate(alternates, parsed) == {}

    def test_list_field_name_matched(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import find_matching_alternate

        alternates = [{"fields": [{"name": ["action", "thought"]}]}]
        parsed = {"action": "x", "thought": "y"}
        assert find_matching_alternate(alternates, parsed) == alternates[0]

    def test_first_matching_alternate_returned(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import find_matching_alternate

        alt1 = {"fields": [{"name": "action"}], "id": 1}
        alt2 = {"fields": [{"name": "action"}], "id": 2}
        parsed = {"action": "click"}
        result = find_matching_alternate([alt1, alt2], parsed)
        assert result["id"] == 1


class TestRescaleWeights:
    def test_equal_default_weights_when_all_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import rescale_weights

        items = [{"weight": -1, "consistency": 0.8}, {"weight": -1, "consistency": 0.6}]
        result = rescale_weights(items)
        assert pytest.approx(result[0]["weight"]) == 0.5
        assert pytest.approx(result[1]["weight"]) == 0.5

    def test_explicit_weights_normalized(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import rescale_weights

        items = [{"weight": 1.0, "consistency": 0.8}, {"weight": 3.0, "consistency": 0.6}]
        result = rescale_weights(items)
        assert pytest.approx(result[0]["weight"]) == 0.25
        assert pytest.approx(result[1]["weight"]) == 0.75

    def test_zero_total_weight_falls_back_to_default(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import rescale_weights

        items = [{"weight": 0, "consistency": 0.5}, {"weight": 0, "consistency": 0.7}]
        result = rescale_weights(items)
        assert pytest.approx(result[0]["weight"]) == 0.5


class TestComputeWeightedSumConsistency:
    def test_equal_weights(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import compute_weighted_sum_consistency

        items = [
            {"consistency": 0.8, "weight": -1, "name": "f1"},
            {"consistency": 0.6, "weight": -1, "name": "f2"},
        ]
        field_cns = {"f1": {"weight": -1}, "f2": {"weight": -1}}
        consistency, _ = compute_weighted_sum_consistency(items, field_cns)
        assert pytest.approx(consistency) == 0.7

    def test_explicit_weights(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.utils import compute_weighted_sum_consistency

        items = [
            {"consistency": 1.0, "weight": 1.0, "name": "f1"},
            {"consistency": 0.0, "weight": 3.0, "name": "f2"},
        ]
        field_cns = {"f1": {}, "f2": {}}
        consistency, _ = compute_weighted_sum_consistency(items, field_cns)
        assert pytest.approx(consistency) == 0.25  # 1.0*0.25 + 0.0*0.75


# ---------------------------------------------------------------------------
# consistency_aggregator.py
# ---------------------------------------------------------------------------


class TestAggregationFunctions:
    def test_mean_empty_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import mean_trajectory_consistency

        assert mean_trajectory_consistency([]) == -1

    def test_mean_single_element(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import mean_trajectory_consistency

        assert pytest.approx(mean_trajectory_consistency([0.6])) == 0.6

    def test_mean_multiple(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import mean_trajectory_consistency

        assert pytest.approx(mean_trajectory_consistency([0.4, 0.8])) == 0.6

    def test_mean_raises_on_negative(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import mean_trajectory_consistency

        with pytest.raises(ValueError):
            mean_trajectory_consistency([-0.1, 0.5])

    def test_rms_empty_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import rms_trajectory_consistency

        assert rms_trajectory_consistency([]) == -1

    def test_rms_values(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import rms_trajectory_consistency

        result = rms_trajectory_consistency([0.6, 0.8])
        expected = math.sqrt((0.6**2 + 0.8**2) / 2)
        assert pytest.approx(result) == expected

    def test_product_empty_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import joint_trajectory_consistency

        assert joint_trajectory_consistency([]) == -1

    def test_product_values(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import joint_trajectory_consistency

        assert pytest.approx(joint_trajectory_consistency([0.5, 0.8])) == 0.4

    def test_geo_mean_empty_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import geometric_mean_trajectory_consistency

        assert geometric_mean_trajectory_consistency([]) == -1

    def test_geo_mean_values(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import geometric_mean_trajectory_consistency

        result = geometric_mean_trajectory_consistency([0.25, 1.0])
        assert pytest.approx(result) == math.sqrt(0.25)


class TestGetAggFcn:
    def test_returns_mean(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import (
            get_agg_fcn,
            mean_trajectory_consistency,
        )

        assert get_agg_fcn("mean") is mean_trajectory_consistency

    def test_returns_rms(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import (
            get_agg_fcn,
            rms_trajectory_consistency,
        )

        assert get_agg_fcn("rms") is rms_trajectory_consistency

    def test_returns_geo_mean(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import (
            get_agg_fcn,
            geometric_mean_trajectory_consistency,
        )

        assert get_agg_fcn("geo_mean") is geometric_mean_trajectory_consistency

    def test_returns_product(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import (
            get_agg_fcn,
            joint_trajectory_consistency,
        )

        assert get_agg_fcn("product") is joint_trajectory_consistency

    def test_unknown_mode_raises(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import get_agg_fcn

        with pytest.raises(Exception):
            get_agg_fcn("unknown")


class TestConsistencyAggregator:
    def _make_trajectory(self, step_consistencies):
        steps = []
        for i, cns in enumerate(step_consistencies):
            step = {"name": f"step{i}"}
            if cns is not None:
                step["consistency"] = {"step_consistency": cns}
            steps.append(step)
        return {"steps": steps}

    def test_single_trajectory_mean(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator

        agg = ConsistencyAggregator({"aggregation": "mean"})
        traj = self._make_trajectory([0.4, 0.8])
        result = agg.aggregate(traj)
        assert pytest.approx(result["consistency"]["aggregate_step_consistency"]) == 0.6

    def test_steps_without_consistency_skipped(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator

        agg = ConsistencyAggregator({"aggregation": "mean"})
        traj = self._make_trajectory([None, 0.6])
        result = agg.aggregate(traj)
        assert result["consistency"]["num_consistency_steps"] == 1
        assert pytest.approx(result["consistency"]["aggregate_step_consistency"]) == 0.6

    def test_step_with_minus_one_skipped(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator

        agg = ConsistencyAggregator({"aggregation": "mean"})
        traj = self._make_trajectory([0.5, -1])
        # -1 is explicitly excluded from cns_list
        result = agg.aggregate(traj)
        assert result["consistency"]["num_consistency_steps"] == 1

    def test_list_of_trajectories(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator

        agg = ConsistencyAggregator({"aggregation": "mean"})
        trajs = [self._make_trajectory([1.0]), self._make_trajectory([0.0])]
        result = agg.aggregate(trajs)
        assert isinstance(result, list)
        assert pytest.approx(result[0]["consistency"]["aggregate_step_consistency"]) == 1.0
        assert pytest.approx(result[1]["consistency"]["aggregate_step_consistency"]) == 0.0

    def test_partial_consistencies_accumulated(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator

        agg = ConsistencyAggregator({"aggregation": "mean"})
        traj = self._make_trajectory([0.4, 0.8])
        result = agg.aggregate(traj)
        partials = result["consistency"]["partial_trajectory_consistencies"]
        assert len(partials) == 2
        assert pytest.approx(partials[0]) == 0.4
        assert pytest.approx(partials[1]) == 0.6


# ---------------------------------------------------------------------------
# consistency_metric.py
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    def test_identical_strings(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import jaccard_similarity

        assert jaccard_similarity("hello world", "hello world") == 1.0

    def test_disjoint_strings(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import jaccard_similarity

        assert jaccard_similarity("hello", "world") == 0.0

    def test_partial_overlap(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import jaccard_similarity

        # {"a", "b"} ∩ {"a", "c"} = {"a"}, union = {"a","b","c"} → 1/3
        result = jaccard_similarity("a b", "a c")
        assert pytest.approx(result) == 1 / 3

    def test_both_empty_returns_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import jaccard_similarity

        assert jaccard_similarity("", "") == 1.0


class TestJaccardConsistencyMetric:
    def test_single_sample_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import JaccardConsistencyMetric

        m = JaccardConsistencyMetric()
        cns, dist = m.get_consistency_and_distance(["only one"])
        assert cns == -1.0
        assert dist == 2.0  # 1 - (-1)

    def test_identical_samples(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import JaccardConsistencyMetric

        m = JaccardConsistencyMetric()
        cns, dist = m.get_consistency_and_distance(["hello world", "hello world"])
        assert pytest.approx(cns) == 1.0
        assert pytest.approx(dist) == 0.0

    def test_disjoint_samples(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import JaccardConsistencyMetric

        m = JaccardConsistencyMetric()
        cns, dist = m.get_consistency_and_distance(["aaa", "bbb"])
        assert pytest.approx(cns) == 0.0

    def test_distance_from_chosen(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import JaccardConsistencyMetric

        m = JaccardConsistencyMetric()
        distances = m.get_distance_from_chosen_trajectory(["hello world", "foo bar"], "hello world")
        assert pytest.approx(distances[0]) == 0.0
        assert pytest.approx(distances[1]) == 1.0

    def test_distance_from_chosen_empty_samples(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import JaccardConsistencyMetric

        m = JaccardConsistencyMetric()
        assert m.get_distance_from_chosen_trajectory([], "hello") == []
        assert m.get_distance_from_chosen_trajectory(["x"], None) == []


class TestDamerauLevenshteinDistance:
    def test_identical_sequences(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import damerau_levenshtein_distance

        assert damerau_levenshtein_distance(["a", "b", "c"], ["a", "b", "c"]) == 0

    def test_empty_vs_nonempty(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import damerau_levenshtein_distance

        assert damerau_levenshtein_distance([], ["a", "b"]) == 2
        assert damerau_levenshtein_distance(["a"], []) == 1

    def test_single_substitution(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import damerau_levenshtein_distance

        assert damerau_levenshtein_distance(["a", "b"], ["a", "c"]) == 1

    def test_transposition(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import damerau_levenshtein_distance

        assert damerau_levenshtein_distance(["a", "b"], ["b", "a"]) == 1


class TestDamerauLevenshteinConsistencyMetric:
    def test_single_sample_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import DamerauLevenshteinConsistencyMetric

        m = DamerauLevenshteinConsistencyMetric()
        cns, dist = m.get_consistency_and_distance([["a", "b"]])
        assert cns == -1.0

    def test_identical_samples(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import DamerauLevenshteinConsistencyMetric

        m = DamerauLevenshteinConsistencyMetric()
        cns, dist = m.get_consistency_and_distance([["a", "b"], ["a", "b"]])
        assert pytest.approx(cns) == 1.0
        assert pytest.approx(dist) == 0.0

    def test_completely_different_samples(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import DamerauLevenshteinConsistencyMetric

        m = DamerauLevenshteinConsistencyMetric()
        cns, dist = m.get_consistency_and_distance([["a", "b"], ["c", "d"]])
        assert cns < 1.0

    def test_distance_from_chosen(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import DamerauLevenshteinConsistencyMetric

        m = DamerauLevenshteinConsistencyMetric()
        distances = m.get_distance_from_chosen_trajectory([["a", "b"], ["a", "b"]], ["a", "b"])
        assert all(pytest.approx(d) == 0.0 for d in distances)


class TestNumericFractionConsistencyMetric:
    def test_identical_values(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import NumericFractionConsistencyMetric

        m = NumericFractionConsistencyMetric()
        cns, dist = m.get_consistency_and_distance([5.0, 5.0])
        assert pytest.approx(cns) == 1.0
        assert pytest.approx(dist) == 0.0

    def test_single_sample_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import NumericFractionConsistencyMetric

        m = NumericFractionConsistencyMetric()
        cns, dist = m.get_consistency_and_distance([3.0])
        assert cns == -1.0
        assert dist == -1.0

    def test_known_distance(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import NumericFractionConsistencyMetric

        m = NumericFractionConsistencyMetric()
        # pairwise diff: |1 - 3| = 2, mean = 2
        cns, dist = m.get_consistency_and_distance([1.0, 3.0])
        assert pytest.approx(dist) == 2.0

    def test_three_samples_mean_pairwise_distance(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import NumericFractionConsistencyMetric

        m = NumericFractionConsistencyMetric()
        # pairs: |1-3|=2, |1-5|=4, |3-5|=2 → mean = 8/3
        cns, dist = m.get_consistency_and_distance([1.0, 3.0, 5.0])
        assert pytest.approx(dist) == 8 / 3

    def test_distance_from_chosen(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import NumericFractionConsistencyMetric

        m = NumericFractionConsistencyMetric()
        distances = m.get_distance_from_chosen_trajectory([10.0, 12.0], 10.0)
        assert pytest.approx(distances[0]) == 0.0
        assert pytest.approx(distances[1]) == 2.0


class TestCategoricalEntropyConsistencyMetric:
    def test_all_identical_returns_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import CategoricalEntropyConsistencyMetric

        m = CategoricalEntropyConsistencyMetric()
        cns, dist = m.get_consistency_and_distance(["A", "A", "A"])
        assert pytest.approx(cns) == 1.0
        assert pytest.approx(dist) == 0.0

    def test_single_sample_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import CategoricalEntropyConsistencyMetric

        m = CategoricalEntropyConsistencyMetric()
        cns, dist = m.get_consistency_and_distance(["A"])
        assert cns == -1.0

    def test_two_different_values_lower_consistency(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import CategoricalEntropyConsistencyMetric

        m = CategoricalEntropyConsistencyMetric()
        cns_uniform, _ = m.get_consistency_and_distance(["A", "B"])
        cns_biased, _ = m.get_consistency_and_distance(["A", "A", "B"])
        assert cns_biased > cns_uniform

    def test_distance_from_chosen_binary(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import CategoricalEntropyConsistencyMetric

        m = CategoricalEntropyConsistencyMetric()
        distances = m.get_distance_from_chosen_trajectory(["A", "B", "A"], "A")
        assert distances == [0.0, 1.0, 0.0]


class TestGetSampleStrings:
    def test_strings_unchanged(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_sample_strings

        assert get_sample_strings(["hello", "world"]) == ["hello", "world"]

    def test_list_elements_joined_with_sep(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_sample_strings

        result = get_sample_strings([["a", "b"], ["c"]], sep="_")
        assert result == ["_a_b", "_c"]

    def test_non_string_converted(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_sample_strings

        assert get_sample_strings([42]) == ["42"]


class TestGetConsistencyByMetric:
    def test_jaccard_dispatch(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_consistency_by_metric

        cns, dist = get_consistency_by_metric(["hello world", "hello world"], "jaccard")
        assert pytest.approx(cns) == 1.0

    def test_numeric_non_numeric_input_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_consistency_by_metric

        cns, dist = get_consistency_by_metric(["not_a_number"], "numeric")
        assert cns == -1.0

    def test_none_metric_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_consistency_by_metric

        cns, dist = get_consistency_by_metric(["a", "b"], "None")
        assert cns == -1.0

    def test_unknown_metric_returns_minus_one(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_consistency_by_metric

        cns, dist = get_consistency_by_metric(["a", "b"], "nonexistent_metric")
        assert cns == -1.0


class TestGetMetricInstance:
    def test_jaccard_instance(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import (
            get_metric_instance,
            JaccardConsistencyMetric,
        )

        assert isinstance(get_metric_instance("jaccard"), JaccardConsistencyMetric)

    def test_numeric_instance(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import (
            get_metric_instance,
            NumericFractionConsistencyMetric,
        )

        assert isinstance(get_metric_instance("numeric"), NumericFractionConsistencyMetric)

    def test_cat_entropy_instance(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import (
            get_metric_instance,
            CategoricalEntropyConsistencyMetric,
        )

        assert isinstance(get_metric_instance("cat_entropy"), CategoricalEntropyConsistencyMetric)

    def test_damerau_levenshtein_instance(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import (
            get_metric_instance,
            DamerauLevenshteinConsistencyMetric,
        )

        assert isinstance(get_metric_instance("damerau_levenshtein"), DamerauLevenshteinConsistencyMetric)

    def test_unknown_metric_raises(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_metric_instance

        with pytest.raises(ValueError):
            get_metric_instance("unknown_metric")


# ---------------------------------------------------------------------------
# sample_preprocessing.py
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    def test_valid_json_string(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_json_response

        result = parse_json_response('{"action": "click", "target": "button"}')
        assert result == {"action": "click", "target": "button"}

    def test_json_in_markdown_fence(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_json_response

        raw = '```json\n{"key": "value"}\n```'
        result = parse_json_response(raw)
        assert result == {"key": "value"}

    def test_empty_string_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_json_response

        assert parse_json_response("") == {}

    def test_unparseable_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_json_response

        assert parse_json_response("not json at all") == {}


class TestParseCodeResponse:
    def test_strips_python_fence(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_code_response

        raw = "```python\nresult = 1 + 2\n```"
        result = parse_code_response(raw)
        assert "result = 1 + 2" in result
        assert "```" not in result

    def test_strips_single_line_comments(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_code_response

        raw = "```python\nx = 1  # this is a comment\n```"
        result = parse_code_response(raw)
        assert "# this is a comment" not in result
        assert "x = 1" in result

    def test_strips_docstring(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_code_response

        raw = '```python\n"""A docstring."""\nx = 2\n```'
        result = parse_code_response(raw)
        assert "docstring" not in result
        assert "x = 2" in result


class TestParseToolCallsResponse:
    def test_valid_list_of_dicts(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_tool_calls_response

        calls = [
            {"id": "1", "type": "function", "function": {"name": "add"}},
            {"id": "2", "type": "function", "function": {"name": "mul"}},
        ]
        result = parse_tool_calls_response(calls)
        assert result["id"] == ["1", "2"]
        assert result["type"] == ["function", "function"]

    def test_non_list_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_tool_calls_response

        assert parse_tool_calls_response("not a list") == {}

    def test_list_of_non_dicts_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_tool_calls_response

        assert parse_tool_calls_response(["a", "b"]) == {}

    def test_empty_list_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_tool_calls_response

        assert parse_tool_calls_response([]) == {}


class TestParseThoughtCodeResponse:
    def test_with_code_block(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_thought_code_response

        raw = "I should compute this.\n```python\nresult = 2 + 2\n```"
        result = parse_thought_code_response(raw)
        assert "I should compute this" in result["thought"]
        assert "result = 2 + 2" in result["code"]

    def test_without_code_block(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import parse_thought_code_response

        raw = "Just a thought, no code here."
        result = parse_thought_code_response(raw)
        assert result["thought"] == "Just a thought, no code here."
        assert result["code"] == ""


class TestGetAgentConfig:
    _CONFIG = {"agents": [{"name": "MyAgent", "metric": "jaccard"}, {"name": "Other", "metric": "numeric"}]}

    def test_found(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import get_agent_config

        result = get_agent_config("MyAgent", self._CONFIG)
        assert result == {"name": "MyAgent", "metric": "jaccard"}

    def test_not_found_returns_empty_dict(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import get_agent_config

        assert get_agent_config("Missing", self._CONFIG) == {}


class TestInnerFieldBackfill:
    def test_adds_missing_field(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import inner_field_backfill

        config = {"fields": [{"name": "action", "backfill": "none"}]}
        result = inner_field_backfill({}, config)
        assert result["action"] == "none"

    def test_does_not_override_existing(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import inner_field_backfill

        config = {"fields": [{"name": "action", "backfill": "none"}]}
        result = inner_field_backfill({"action": "click"}, config)
        assert result["action"] == "click"

    def test_no_fields_key_returns_unchanged(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import inner_field_backfill

        result = inner_field_backfill({"action": "click"}, {})
        assert result == {"action": "click"}

    def test_non_dict_response_reset_to_empty(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import inner_field_backfill

        config = {"fields": [{"name": "action", "backfill": "none"}]}
        result = inner_field_backfill("not_a_dict", config)
        assert result["action"] == "none"


# ---------------------------------------------------------------------------
# single_step_consistency.py
# ---------------------------------------------------------------------------


class TestCheckSampleValidity:
    _CONFIG = {
        "agents": [
            {"name": "TextAgent", "response_type": "text", "metric": "jaccard"},
            {"name": "JsonAgent", "response_type": "json", "fields": [{"name": "action", "metric": "jaccard"}]},
        ]
    }

    def test_no_sampling_invalid(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import check_sample_validity

        step = {"name": "TextAgent"}
        valid, msg = check_sample_validity(step, self._CONFIG)
        assert not valid
        assert "No samples" in msg

    def test_no_metric_config_invalid(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import check_sample_validity

        step = {"name": "UnknownAgent", "sampling": {"raw_samples": ["a"]}}
        valid, msg = check_sample_validity(step, self._CONFIG)
        assert not valid
        assert "metric configuration" in msg

    def test_text_with_raw_samples_valid(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import check_sample_validity

        step = {"name": "TextAgent", "sampling": {"raw_samples": ["hello", "world"]}}
        valid, _ = check_sample_validity(step, self._CONFIG)
        assert valid

    def test_text_with_empty_raw_samples_invalid(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import check_sample_validity

        step = {"name": "TextAgent", "sampling": {"raw_samples": []}}
        valid, _ = check_sample_validity(step, self._CONFIG)
        assert not valid

    def test_json_missing_parsed_samples_invalid(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import check_sample_validity

        step = {"name": "JsonAgent", "sampling": {"parsed_samples": []}}
        valid, msg = check_sample_validity(step, self._CONFIG)
        assert not valid
        assert "parsed" in msg


class TestComputeStepConsistency:
    _CONFIG = {
        "agents": [{"name": "TextAgent", "response_type": "text", "metric": "jaccard"}],
    }

    def _make_trajectory(self, raw_samples):
        return {
            "name": "traj",
            "steps": [{"name": "TextAgent", "sampling": {"raw_samples": raw_samples, "num_samples": len(raw_samples)}}],
        }

    def test_identical_samples_yield_high_consistency(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import compute_step_consistency

        traj = self._make_trajectory(["hello world", "hello world", "hello world"])
        result = compute_step_consistency(traj, self._CONFIG)
        assert pytest.approx(result["steps"][0]["consistency"]["step_consistency"]) == 1.0

    def test_diverse_samples_yield_lower_consistency(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import compute_step_consistency

        traj = self._make_trajectory(["hello world", "foo bar", "baz qux"])
        result = compute_step_consistency(traj, self._CONFIG)
        assert result["steps"][0]["consistency"]["step_consistency"] < 1.0

    def test_step_without_sampling_gets_undefined(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import compute_step_consistency

        traj = {"name": "traj", "steps": [{"name": "TextAgent"}]}
        result = compute_step_consistency(traj, self._CONFIG)
        assert result["steps"][0]["consistency"]["step_consistency"] == -1


# ---------------------------------------------------------------------------
# consistency_analysis.py
# ---------------------------------------------------------------------------


class TestCreateConsistencyScoreCard:
    def _make_trajectory(self, step_consistencies):
        steps = []
        for i, cns in enumerate(step_consistencies):
            step = {"name": f"step{i}", "step_number": i + 1}
            if cns is not None:
                step["consistency"] = {"step_consistency": cns, "metric": "jaccard"}
            steps.append(step)
        return {
            "task": "Do something",
            "steps": steps,
            "consistency": {
                "aggregation": "mean",
                "aggregate_step_consistency": sum(c for c in step_consistencies if c is not None)
                / max(1, sum(1 for c in step_consistencies if c is not None)),
            },
        }

    def test_score_card_fields(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import create_consistency_score_card

        traj = self._make_trajectory([0.8, 0.6])
        card = create_consistency_score_card(traj)
        assert card["task"] == "Do something"
        assert card["total_steps"] == 2
        assert card["aggregation"] == "mean"
        assert len(card["steps"]) == 2

    def test_step_uncertainty_computed(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import create_consistency_score_card

        traj = self._make_trajectory([0.8])
        card = create_consistency_score_card(traj)
        assert pytest.approx(card["steps"][0]["step_uncertainty"]) == 0.2

    def test_steps_without_consistency_excluded(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import create_consistency_score_card

        traj = self._make_trajectory([None, 0.6])
        card = create_consistency_score_card(traj)
        assert len(card["steps"]) == 1


class TestAnalyzeConsistency:
    """End-to-end test using a synthetic text-type trajectory (no LLM calls)."""

    _CONFIG = {
        "aggregation": "mean",
        "agents": [{"name": "TextAgent", "response_type": "text", "metric": "jaccard"}],
    }

    def _make_trajectory(self, raw_samples_per_step):
        steps = []
        for i, samples in enumerate(raw_samples_per_step):
            steps.append(
                {
                    "name": "TextAgent",
                    "step_number": i + 1,
                    "sampling": {"raw_samples": samples, "num_samples": len(samples)},
                }
            )
        return {"task": "Test task", "steps": steps}

    def test_returns_score_card_and_trajectory(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency

        traj = self._make_trajectory([["hello world", "hello world"]])
        score_card, out_traj = analyze_consistency(traj, self._CONFIG)
        assert "steps" in score_card
        assert "consistency" in out_traj

    def test_consistent_trajectory_yields_low_uncertainty(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency

        traj = self._make_trajectory([["same text", "same text", "same text"]])
        score_card, _ = analyze_consistency(traj, self._CONFIG)
        assert score_card["aggregate_trajectory_uncertainty"] < 0.1

    def test_inconsistent_trajectory_yields_higher_uncertainty(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency

        traj = self._make_trajectory([["aaa bbb", "ccc ddd", "eee fff"]])
        score_card, _ = analyze_consistency(traj, self._CONFIG)
        assert score_card["aggregate_trajectory_uncertainty"] > 0.5

    def test_multi_step_aggregation(self):
        from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency

        traj = self._make_trajectory(
            [
                ["same text", "same text"],
                ["foo bar", "baz qux"],
            ]
        )
        score_card, _ = analyze_consistency(traj, self._CONFIG)
        # Score card should have entries for both steps
        assert score_card["total_steps"] == 2
