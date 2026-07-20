"""
Similarity and distance metrics for consistency computation.

This module provides various similarity and distance functions used to compute
consistency scores across agent response samples, including:
- Jaccard similarity for word-level comparison
- Damerau-Levenshtein edit distance for sequence comparison
- Numeric fraction similarity for numerical values
- Sentence transformer embedding similarity
- Categorical entropy-based consistency
"""

import logging

logger = logging.getLogger(__name__)
import statistics as st
import numpy as np
import pandas as pd
from scipy.stats import entropy

from abc import ABC, abstractmethod

# Module-level caching for sentence transformer models (populated on first use)
sentence_transformer_model_small = None
sentence_transformer_model_large = None


def get_sentence_transformer_small():
    global sentence_transformer_model_small
    if sentence_transformer_model_small is None:
        from sentence_transformers import SentenceTransformer

        sentence_transformer_model_small = SentenceTransformer("all-MiniLM-L6-v2")
    return sentence_transformer_model_small


def get_sentence_transformer_large():
    global sentence_transformer_model_large
    if sentence_transformer_model_large is None:
        from sentence_transformers import SentenceTransformer

        sentence_transformer_model_large = SentenceTransformer("nomic-ai/CodeRankEmbed", trust_remote_code=True)
    return sentence_transformer_model_large


def get_metric_instance(metric: str):
    """
    Instantiate a consistency metric by name with proper dependencies.

    This is the single source of truth for metric instantiation, properly
    handling model loading for embedding metrics.

    Args:
        metric: Metric name (jaccard, sbert_small, numeric, etc.)

    Returns:
        Instantiated metric object ready to use

    Raises:
        ValueError: If metric name is unknown
    """
    if metric == "jaccard":
        return JaccardConsistencyMetric()
    elif metric == "numeric":
        return NumericFractionConsistencyMetric()
    elif metric == "sbert_small":
        model = get_sentence_transformer_small()
        return EmbeddingConsistencyMetric(model)
    elif metric == "sbert_large":
        model = get_sentence_transformer_large()
        return EmbeddingConsistencyMetric(model)
    elif metric == "cat_entropy":
        return CategoricalEntropyConsistencyMetric()
    elif metric == "damerau_levenshtein":
        return DamerauLevenshteinConsistencyMetric()
    else:
        raise ValueError(f"Metric {metric} not supported in get_metric_instance")


def get_consistency_by_metric(
    samples: list,
    metric: str,
) -> tuple[float, float]:
    """
    Compute consistency and distance for samples using specified metric.

    Args:
        samples: List of samples to compute metrics for
        metric: Metric name string

    Returns:
        Tuple of (consistency, distance)
    """
    if metric == "jaccard":
        str_samples = get_sample_strings(samples, " ")
        jaccard_metric = get_metric_instance("jaccard")
        consistency, distance = jaccard_metric.get_consistency_and_distance(str_samples)
        return float(consistency), float(distance)

    elif metric == "numeric":
        if not isinstance(samples[0], (int, float)):
            logger.debug(f"++++ Cannot apply metric {metric} to data of type {type(samples[0])}: consistency = -1")
            return -1.0, -1.0
        numeric_metric = get_metric_instance("numeric")
        consistency, distance = numeric_metric.get_consistency_and_distance(samples)
        return float(consistency), float(distance)

    elif metric == "sbert_small":
        str_samples = get_sample_strings(samples, " ")
        embedding_metric = get_metric_instance("sbert_small")
        consistency, distance = embedding_metric.get_consistency_and_distance(str_samples)
        return float(consistency), float(distance)

    elif metric == "sbert_large":
        str_samples = get_sample_strings(samples, " ")
        embedding_metric = get_metric_instance("sbert_large")
        consistency, distance = embedding_metric.get_consistency_and_distance(str_samples)
        return float(consistency), float(distance)

    elif metric == "cat_entropy":
        cat_samples = get_sample_strings(samples, "_")
        entropy_metric = get_metric_instance("cat_entropy")
        consistency, distance = entropy_metric.get_consistency_and_distance(cat_samples)
        return float(consistency), float(distance)

    elif metric == "damerau_levenshtein":
        dl_metric = get_metric_instance("damerau_levenshtein")
        consistency, distance = dl_metric.get_consistency_and_distance(samples)
        return float(consistency), float(distance)

    elif metric == "None":
        return -1.0, -1.0

    else:
        logger.debug(f"++++ Metric {metric} not supported: consistency = -1")
        return -1.0, -1.0


class ConsistencyMetric(ABC):
    """
    Abstract base class for consistency metrics.

    All consistency metrics should inherit from this class and implement
    the three abstract methods for computing consistency and distance scores.
    """

    @abstractmethod
    def get_consistency_and_distance(self, samples: list) -> tuple[float, float]:
        """
        Compute both consistency and distance scores for the given samples.

        Args:
            samples: List of samples to compute metrics for

        Returns:
            Tuple of (consistency_score, distance_score)
        """
        pass

    @abstractmethod
    def _get_consistency(self, samples: list) -> float:
        """
        Compute the consistency score for the given samples.

        Args:
            samples: List of samples to compute consistency for

        Returns:
            Consistency score as a float
        """
        pass

    @abstractmethod
    def _get_distance(self, samples: list) -> float:
        """
        Compute the distance score for the given samples.

        Args:
            samples: List of samples to compute distance for

        Returns:
            Distance score as a float
        """
        pass

    @abstractmethod
    def get_distance_from_chosen_trajectory(self, samples: list, chosen, **kwargs) -> list:
        """
        Compute distance from each sample to the chosen reference response.

        Args:
            samples: List of sampled responses
            chosen: The chosen reference response (actual model output)
            **kwargs: Additional metric-specific parameters

        Returns:
            List of distance values, one per sample, typically in range [0, 1]
            Returns empty list if samples is empty or chosen is None
            May return -1.0 for individual invalid samples
        """
        pass


class JaccardConsistencyMetric(ConsistencyMetric):
    """Jaccard similarity-based consistency metric for word-level comparison."""

    def get_consistency_and_distance(self, samples: list) -> tuple[float, float]:
        consistency = self._get_consistency(samples)
        distance = 1.0 - consistency
        return consistency, distance

    def _get_consistency(self, samples: list) -> float:
        # computes a list of jaccard similarity scores with one score for each pair of samples
        sim_list = []
        # iterate through the pairs of samples
        for i, sample in enumerate(samples):
            if i + 1 >= len(samples):
                break
            for j in range(len(samples) - (i + 1)):
                sim_list.append(jaccard_similarity(sample, samples[i + 1 + j]))

        # aggregate over all pairwise similarities
        mean_sim = st.mean(sim_list) if sim_list != [] else -1.0
        return mean_sim

    def _get_distance(self, samples: list) -> float:
        return 1.0 - self._get_consistency(samples)

    def get_distance_from_chosen_trajectory(self, samples: list, chosen, **kwargs) -> list:
        if not samples or chosen is None:
            return []

        distances = []
        chosen_str = str(chosen)
        for sample in samples:
            sample_str = str(sample)
            similarity = jaccard_similarity(sample_str, chosen_str)
            distances.append(1.0 - similarity)

        return distances


def jaccard_similarity(x: str, y: str) -> float:
    set1 = set(x.split())
    set2 = set(y.split())
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))

    if union == 0:
        # Both strings are empty — the field was absent in all samples,
        # which means maximum inconsistency (the original step had a value
        # that no resample reproduced), not perfect consistency.
        return 0.0
    return intersection / union


class DamerauLevenshteinConsistencyMetric(ConsistencyMetric):
    """Damerau-Levenshtein edit distance-based consistency metric."""

    def get_consistency_and_distance(self, samples: list) -> tuple[float, float]:
        distance = self._get_distance(samples)
        if distance == -1.0:
            return -1.0, -1.0
        consistency = 1.0 - distance
        return consistency, distance

    def _get_consistency(self, samples: list) -> float:
        distance = self._get_distance(samples)
        if distance == -1.0:
            return -1.0
        return 1.0 - distance

    def _get_distance(self, samples: list) -> float:
        """
        Compute mean of pairwise normalized Damerau-Levenshtein distances.

        Returns:
            Mean distance in [0, 1], or -1.0 if not enough samples
        """
        if len(samples) <= 1:
            return -1.0

        distance_list = self._compute_pairwise_distances(samples)
        if not distance_list:
            return -1.0

        return st.mean(distance_list)

    def _normalized_distance_pair(self, list_a, list_b) -> float:
        """
        Compute normalized Damerau-Levenshtein distance between two lists of strings.

        Parameters
        ----------
        list_a, list_b : Sequence[str]

        Returns
        -------
        float
            Normalized distance in [0, 1].
        """
        D = damerau_levenshtein_distance(list_a, list_b)
        la, lb = len(list_a), len(list_b)
        base = max(la, lb)
        normalized_dl_dist = D / base if base > 0 else 0.0
        return normalized_dl_dist

    def _compute_pairwise_distances(self, samples: list) -> list:
        """
        Compute normalized pairwise Damerau-Levenshtein distances.

        Returns:
            List of pairwise distances
        """
        distance_list = []
        n = len(samples)
        for i in range(n):
            for j in range(i + 1, n):
                dist = self._normalized_distance_pair(samples[i], samples[j])
                distance_list.append(dist)
        return distance_list

    def get_distance_from_chosen_trajectory(self, samples: list, chosen, **kwargs) -> list:
        if not samples or chosen is None:
            return []

        distances = []
        for sample in samples:
            dist = self._normalized_distance_pair(sample, chosen)
            distances.append(dist)

        return distances


def damerau_levenshtein_distance(a: str | list[str], b: str | list[str]) -> int:
    """
    Full (unrestricted) Damerau–Levenshtein distance on sequences of strings.
    Supports multiple transpositions across the sequence.
    """
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n

    # Set of tokens observed
    alphabet = set(a) | set(b)
    INF = n + m
    # Last row positions of each token in a
    da = {tok: 0 for tok in alphabet}

    # H has shape (n+2) x (m+2)
    H = [[0] * (m + 2) for _ in range(n + 2)]
    H[0][0] = INF
    for i in range(0, n + 1):
        H[i + 1][0] = INF
        H[i + 1][1] = i
    for j in range(0, m + 1):
        H[0][j + 1] = INF
        H[1][j + 1] = j

    for i in range(1, n + 1):
        db = 0
        for j in range(1, m + 1):
            i1 = da.get(b[j - 1], 0)
            j1 = db

            cost = 0 if a[i - 1] == b[j - 1] else 1
            if cost == 0:
                db = j

            H[i + 1][j + 1] = min(
                H[i][j] + cost,  # substitution/match
                H[i + 1][j] + 1,  # insertion
                H[i][j + 1] + 1,  # deletion
                # transposition accounting for gaps
                H[i1][j1] + (i - i1 - 1) + 1 + (j - j1 - 1),
            )
        da[a[i - 1]] = i

    return H[n + 1][m + 1]


def get_sample_strings(samples: list, sep: str = "") -> list:
    # turn the list of samples into a list of strings
    str_samples = []
    for sample in samples:
        if isinstance(sample, list):
            # create a string from the list contents
            str_sample = ""
            for elem in sample:
                str_sample += sep + str(elem)
            str_samples.append(str_sample)
        else:
            str_samples.append(str(sample))
    return str_samples


class CategoricalEntropyConsistencyMetric(ConsistencyMetric):
    """Entropy-based consistency metric for categorical data."""

    def get_consistency_and_distance(self, samples: list) -> tuple[float, float]:
        if len(samples) <= 1:
            return -1.0, -1.0

        distance = self._get_distance(samples)
        return 1.0 - distance, distance

    def _get_consistency(self, samples: list) -> float:
        step_consistency = -1
        # not enough samples were collected for this step
        if len(samples) <= 1:
            return step_consistency

        return 1.0 - self._get_distance(samples)

    def _get_distance(self, samples: list) -> float:
        # extract value series
        val_series = pd.Series(samples)
        unique_vals = val_series.value_counts(normalize=True)
        val_probs = unique_vals.to_list()

        # Calculate normalized entropy
        val_entropy = entropy(val_probs, base=2)
        max_entropy = np.log2(len(samples))

        if max_entropy == 0:  # this can happen if we only have 1 sample
            return 0.0

        normalized_entropy = val_entropy / max_entropy
        if normalized_entropy > 1.0:  # correct anomalies from rounding errors
            normalized_entropy = 1.0

        return normalized_entropy

    def get_distance_from_chosen_trajectory(self, samples: list, chosen, **kwargs) -> list:
        if not samples or chosen is None:
            return []

        # Convert to categorical string format (same as in _get_distance)
        chosen_str = get_sample_strings([chosen], "_")[0] if isinstance(chosen, list) else str(chosen)

        distances = []
        for sample in samples:
            sample_str = str(sample) if not isinstance(sample, list) else get_sample_strings([sample], "_")[0]
            # Binary distance: 0 if equal, 1 if different
            distances.append(0.0 if sample_str == chosen_str else 1.0)

        return distances


class EmbeddingConsistencyMetric(ConsistencyMetric):
    """Sentence transformer embedding-based consistency metric."""

    def __init__(self, sentence_transformer_model):
        self.sentence_transformer_model = sentence_transformer_model

    def get_consistency_and_distance(self, samples: list) -> tuple[float, float]:
        consistency = self._get_consistency(samples)
        distance = 1.0 - consistency
        return consistency, distance

    def _get_consistency(self, samples: list) -> float:
        if len(samples) <= 1:
            return -1.0

        # extract embeddings
        embeddings = self.sentence_transformer_model.encode(samples)

        # returns a matrix of pairwise similarities
        similarities = np.asarray(self.sentence_transformer_model.similarity(embeddings, embeddings))

        # select upper-triangle pairs directly (avoids using 0 as a sentinel,
        # which would discard legitimately zero similarities and bias the mean)
        n = similarities.shape[0]
        idx = np.triu_indices(n, k=1)
        embedding_pairwise_similarities = similarities[idx]
        if len(embedding_pairwise_similarities) == 0:
            return 0.0
        mean_embedding_similarity = float(np.mean(embedding_pairwise_similarities))
        # cosine similarity is in [-1, 1]; clamp to [0, 1] so the aggregator
        # never receives a negative value
        return max(0.0, mean_embedding_similarity)

    def _get_distance(self, samples: list) -> float:
        return 1.0 - self._get_consistency(samples)

    def get_distance_from_chosen_trajectory(self, samples: list, chosen, **kwargs) -> list:
        if not samples or chosen is None:
            return []

        # Convert to strings
        sample_strs = [str(s) for s in samples]
        chosen_str = str(chosen)

        # Encode samples and chosen
        sample_embeddings = self.sentence_transformer_model.encode(sample_strs)
        chosen_embedding = self.sentence_transformer_model.encode([chosen_str])

        # Compute similarities between each sample and chosen
        similarities = self.sentence_transformer_model.similarity(sample_embeddings, chosen_embedding)

        # Convert to distances (1 - similarity)
        # similarities is shape (num_samples, 1), flatten and convert to distances
        distances = [1.0 - float(sim[0]) for sim in similarities]

        return distances


class NumericFractionConsistencyMetric(ConsistencyMetric):
    """Numeric fraction-based consistency metric for numerical values."""

    def get_consistency_and_distance(self, samples: list) -> tuple[float, float]:
        distance = self._get_distance(samples)
        if distance == -1.0:
            return -1.0, -1.0
        consistency = 1.0 - distance
        return consistency, distance

    def _get_consistency(self, samples: list) -> float:
        distance = self._get_distance(samples)
        if distance == -1.0:
            return -1.0
        return 1.0 - distance

    def _get_distance(self, samples: list) -> float:
        """
        Compute mean of pairwise absolute differences between numeric samples,
        normalized to [0, 1) via d / (1 + d) so that consistency = 1 - distance
        stays within [0, 1].

        Returns:
            Normalized mean distance in [0, 1), or -1.0 if no samples
        """
        diff_list = []
        # iterate through the pairs of samples
        for i, sample in enumerate(samples):
            if i + 1 >= len(samples):
                break
            for j in range(len(samples) - (i + 1)):
                diff_list.append(abs(float(sample) - float(samples[i + 1 + j])))

        if not diff_list:
            return -1.0
        raw = st.mean(diff_list)
        return raw / (1.0 + raw)

    def get_distance_from_chosen_trajectory(self, samples: list, chosen, **kwargs) -> list:
        if not samples or chosen is None:
            return []

        try:
            chosen_val = float(chosen)
        except (TypeError, ValueError):
            return []

        distances = []
        for sample in samples:
            try:
                sample_val = float(sample)
                raw = abs(sample_val - chosen_val)
                distances.append(raw / (1.0 + raw))
            except (TypeError, ValueError):
                # Skip invalid samples
                distances.append(-1.0)

        return distances
