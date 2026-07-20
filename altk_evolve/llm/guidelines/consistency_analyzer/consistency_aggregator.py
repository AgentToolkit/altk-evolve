"""
Consistency aggregation functions and classes.

This module provides functions to aggregate step-level consistency scores
into trajectory-level consistency scores using various aggregation methods
(mean, RMS, geometric mean, product).
"""

import logging

logger = logging.getLogger(__name__)
import math
from typing import Callable

import numpy as np


def mean_trajectory_consistency(cns_list: list[float]) -> float:
    """
    Compute mean of consistency scores.

    Args:
        cns_list: List of consistency scores (must be >= 0)

    Returns:
        Mean consistency or -1 if list is empty
    """
    if not all([c >= 0.0 for c in cns_list]):
        raise ValueError(f"Consistency must be >= 0: {min(cns_list)}")
    consistency = float(np.mean(cns_list)) if cns_list != [] else -1
    return consistency


def rms_trajectory_consistency(cns_list: list[float]) -> float:
    """
    Compute root mean square (RMS) of consistency scores.

    Args:
        cns_list: List of consistency scores (must be >= 0)

    Returns:
        RMS consistency or -1 if list is empty
    """
    if not all([c >= 0.0 for c in cns_list]):
        raise ValueError(f"Consistency must be >= 0: {min(cns_list)}")
    consistency = math.sqrt(float(np.mean([c**2 for c in cns_list]))) if cns_list != [] else -1
    return consistency


def joint_trajectory_consistency(cns_list: list[float]) -> float:
    """
    Compute product of consistency scores.

    Args:
        cns_list: List of consistency scores (must be >= 0)

    Returns:
        Product of consistencies or -1 if list is empty
    """
    if not all([c >= 0.0 for c in cns_list]):
        raise ValueError(f"Consistency must be >= 0: {min(cns_list)}")
    consistency = float(math.prod(cns_list)) if cns_list != [] else -1
    return consistency


def geometric_mean_trajectory_consistency(cns_list: list[float]) -> float:
    """
    Compute geometric mean of consistency scores.

    Args:
        cns_list: List of consistency scores (must be >= 0)

    Returns:
        Geometric mean consistency or -1 if list is empty
    """
    if not all([c >= 0.0 for c in cns_list]):
        raise ValueError(f"Consistency must be >= 0: {min(cns_list)}")

    prod = 1.0
    for c in cns_list:
        prod *= c

    geo_mean = math.pow(prod, 1.0 / len(cns_list)) if cns_list != [] else -1
    return geo_mean


def get_agg_fcn(mode: str) -> Callable:
    """
    Get aggregation function by name.

    Args:
        mode: Aggregation mode ('mean', 'rms', 'geo_mean', 'product')

    Returns:
        Aggregation function

    Raises:
        Exception: If mode is unknown
    """
    if mode == "mean":
        return mean_trajectory_consistency
    elif mode == "rms":
        return rms_trajectory_consistency
    elif mode == "geo_mean":
        return geometric_mean_trajectory_consistency
    elif mode == "product":
        return joint_trajectory_consistency
    else:
        raise Exception(f"Aggregation mode {mode} unknown. ")


class ConsistencyAggregator:
    """
    Aggregates step-level consistency scores into trajectory-level scores.

    Supports multiple aggregation modes: mean, RMS, geometric mean, and product.
    """

    def __init__(self, config: dict):
        """
        Initialize the consistency aggregator.

        Args:
            config: Configuration dict with 'aggregation' key specifying mode
        """
        self.config = config
        self.mode = config.get("aggregation", "mean")
        self.agg_fcn = get_agg_fcn(self.mode)

    def aggregate(self, trajectories):
        """
        Aggregate consistency across trajectory steps.

        Args:
            trajectories: Either a single trajectory dict or a list of trajectory dicts

        Returns:
            The same type as input with consistency information added
        """
        if isinstance(trajectories, dict):
            return self._aggregate_single_trajectory(trajectories)
        for trajectory in trajectories:
            self._aggregate_single_trajectory(trajectory)
        return trajectories

    def _aggregate_single_trajectory(self, trajectory: dict) -> dict:
        """Aggregate consistency for a single trajectory."""
        cns_list = []
        partial_consistencies = []
        for step in trajectory["steps"]:
            if "consistency" in step:
                step_cns = step["consistency"]["step_consistency"]
                if step_cns != -1:
                    cns_list.append(step_cns)
            partial_consistencies.append(self.agg_fcn(cns_list))

        trajectory["consistency"] = {
            "num_consistency_steps": len(cns_list),
            "aggregation": self.mode,
            "aggregate_step_consistency": self.agg_fcn(cns_list),
            "partial_trajectory_consistencies": partial_consistencies,
        }
        logger.info(f"+++ [Consistency] Aggregating trajectory consistency for (1/1) with {self.mode} aggregation")
        logger.debug(f"{[f'{num:.2f}' for num in cns_list]}")
        logger.debug(trajectory["consistency"])
        return trajectory
