"""End-to-end consistency analysis pipeline: preprocessing, scoring, aggregation, and score card generation."""

import logging

logger = logging.getLogger(__name__)

from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import extract_parsed_responses_from_trajectory
from altk_evolve.llm.guidelines.consistency_analyzer.single_step_consistency import compute_step_consistency
from altk_evolve.llm.guidelines.consistency_analyzer.consistency_aggregator import ConsistencyAggregator


def create_consistency_score_card(trajectory: dict) -> dict:
    steps = []
    for i, step in enumerate(trajectory["steps"]):
        if "consistency" not in step:
            continue
        step_consistency = step["consistency"].get("step_consistency", -1)
        step_uncertainty = -1 if step_consistency == -1 else 1.0 - step_consistency
        if step_uncertainty != -1:
            steps.append(
                {
                    "name": step.get("name", "None"),
                    "step_number": step.get("step_number", i),
                    "step_uncertainty": round(step_uncertainty, 4),
                    "metric": step["consistency"].get("metric", "Mixed"),
                }
            )
    return {
        "task": trajectory.get("task", "Task instruction not provided"),
        "total_steps": len(trajectory["steps"]),
        "aggregation": trajectory["consistency"].get("aggregation", "None"),
        "aggregate_trajectory_uncertainty": 1.0 - trajectory["consistency"].get("aggregate_step_consistency", -2),
        "steps": steps,
    }


def analyze_consistency(trajectory: dict, config: dict) -> tuple[dict, dict]:
    """
    Analyze consistency of a sampled agent trajectory end-to-end.

    This function performs the complete consistency analysis pipeline:
    1. Parse sampled responses into structured format
    2. Compute step-level consistency scores
    3. Aggregate into trajectory-level consistency
    4. Create a consistency score card

    Args:
        trajectory: Trajectory dict with sampling data in each step
        config: Configuration dict specifying metrics and aggregation method

    Returns:
        Tuple of (score_card, trajectory) where score_card is a dict containing:
        - trajectory_name: Name of the trajectory
        - task_score: Task success score (0.0 or 1.0)
        - aggregate_consistency: Overall trajectory consistency
        - step_consistencies: List of per-step consistency details
        and trajectory is the fully-annotated trajectory dict after all pipeline
        stages have been applied.

    Example:
        >>> import yaml
        >>> with open('config.yaml') as f:
        ...     config = yaml.safe_load(f)
        >>> score_card, trajectory = analyze_consistency(trajectory, config)
        >>> logger.debug(f"Consistency: {score_card['aggregate_consistency']}")
    """
    logger.info("\n+++ Step 1: Pre-processing to parse samples +++")
    # Pre-process the samples
    trajectory = extract_parsed_responses_from_trajectory(trajectory, config)

    logger.info("\n+++ Step 2: Compute step consistencies +++")
    # Compute consistency metrics
    trajectory = compute_step_consistency(trajectory, config)

    logger.info("\n+++ Step 3: Compute aggregate trajectory consistency +++")
    aggregator = ConsistencyAggregator(config)
    trajectory = aggregator.aggregate(trajectory)

    logger.info("\n+++ Step 4: Create consistency score card +++")
    # Create consistency score card
    score_card = create_consistency_score_card(trajectory)

    return score_card, trajectory
