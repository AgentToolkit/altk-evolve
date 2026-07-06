"""
Agent Consistency Analyzer

A framework for analyzing and estimating consistency in multi-step agent trajectories.
"""

__version__ = "0.1.0"

# Import key functions for external use
from altk_evolve.llm.guidelines.consistency_analyzer.resampling import resample_trajectory
from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import extract_parsed_responses_from_trajectory
from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import compute_step_consistency
from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator
from altk_evolve.llm.guidelines.consistency_analyzer.consistency_analysis import analyze_consistency

__all__ = [
    "__version__",
    "resample_trajectory",
    "extract_parsed_responses_from_trajectory",
    "compute_step_consistency",
    "ConsistencyAggregator",
    "analyze_consistency",
]
