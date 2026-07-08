"""
Single-step consistency computation for agent trajectories.

This module computes consistency metrics for individual steps in agent trajectories:
- Validates sample data and configurations
- Computes field-level and step-level consistency
- Handles multiple response types (JSON, text, code, ReAct)
- Supports weighted aggregation across multiple fields
"""

import logging

logger = logging.getLogger(__name__)
import json

from altk_evolve.llm.guidelines.consistency_analyzer.sample_preprocessing import get_agent_config
from altk_evolve.llm.guidelines.consistency_analyzer.consistency_metric import get_consistency_by_metric
from altk_evolve.llm.guidelines.consistency_analyzer.utils import (
    compute_weighted_sum_consistency,
    extract_field_values_from_responses,
    find_matching_alternate,
    flatten_response,
)

# The minimal fraction of raw samples that we need parsed in order to compute consistency
MIN_FRACTION = 0.5


def compute_json_step_consistency(parsed_responses: list, metric_config: dict, min_samples: int) -> tuple[float, dict]:
    """
    Compute step consistency for JSON/structured responses.

    Args:
        parsed_responses: List of parsed response dicts
        metric_config: Metric configuration dict with 'fields' list
        min_samples: Minimum number of samples required

    Returns:
        Tuple of (consistency_score, metadata_dict)
    """
    if metric_config == {}:
        return -1, {}
    if "fields" not in metric_config:
        consistency, _ = get_consistency_by_metric(parsed_responses, metric_config["metric"])
        return consistency, {}

    flat_responses = [flatten_response(r) for r in parsed_responses]
    field_consistencies = {}
    step_consistency_list = []

    for field in metric_config["fields"]:
        field_samples = extract_field_values_from_responses(flat_responses, field)
        metric = field.get("metric", "None")
        if field_samples and len(field_samples) > min_samples and metric != "None":
            cns, _ = get_consistency_by_metric(field_samples, metric)
            logger.debug(f"+++ Processing field {field}, {len(field_samples)} samples ----consistency ({metric}): {cns}")
            field_name = field["name"] if isinstance(field["name"], str) else "-".join(field["name"])
            field_consistencies[field_name] = {
                "consistency": cns,
                "metric": field["metric"],
                "weight": field.get("weight", -1),
            }
            if cns != -1:
                step_consistency_list.append({"consistency": cns, "weight": field.get("weight", -1), "name": field_name})
        else:
            logger.debug(f"+++ Processing field {field} --- found {len(field_samples)} samples - skipping")

    if step_consistency_list:
        consistency, field_consistencies = compute_weighted_sum_consistency(step_consistency_list, field_consistencies)
    else:
        consistency = -1

    return consistency, {"field_consistencies": field_consistencies}


def get_undefined_consistency() -> dict:
    consistency = {"step_consistency": -1, "field_consistencies": {}, "metric": "undefined"}
    return consistency


def check_sample_validity(step: dict, config: dict) -> tuple[bool, str]:
    """
    Check if samples are valid for consistency computation.

    Validates all conditions required for computing step consistency:
    - Sampling data exists
    - Metric configuration exists for the agent
    - Appropriate samples exist based on response type
    - Alternate configuration matches (if applicable)

    Args:
        step: The step dictionary containing sampling data and agent info
        config: The configuration dictionary with agent metric configs

    Returns:
        Tuple of (is_valid, error_message).
        If is_valid is False, error_message contains the reason.
        If is_valid is True, error_message is empty string.
    """
    step_name = step["name"]

    # Check 1: sampling exists
    if "sampling" not in step:
        return False, f"No samples found for {step_name} - consistency undefined"

    # Check 2: metric configuration exists
    metric_config = get_agent_config(step_name, config)
    if metric_config == {}:
        return False, f"Cannot find metric configuration for agent {step_name} - consistency undefined"

    # Check 3: response type specific validations
    response_type = metric_config["response_type"]

    if response_type in ["json", "react", "react_aw", "thought_code", "tool_calls"]:
        samples = step["sampling"].get("parsed_samples", [])
        if samples == []:
            return False, f"Missing parsed responses for response type {response_type} in {step_name} - consistency undefined"

        # Check 4: alternates validation (if applicable)
        if "alternates" in metric_config:
            if "parsed_response" not in step:
                return False, f"Missing parsed_response for {step_name} with alternates config - consistency undefined"
            this_config = find_matching_alternate(metric_config["alternates"], flatten_response(step["parsed_response"]))
            if this_config == {}:
                return (
                    False,
                    f"Alternates: Could not find matching alternate config for {json.dumps(samples[0], indent=4)} - consistency undefined",
                )

    elif response_type in ["code", "text"]:
        samples = step["sampling"].get("raw_samples", [])
        if samples == []:
            return False, f"Missing samples for response type {response_type} in {step_name} - consistency undefined"

    else:
        return False, f"Cannot handle response type {response_type} in {step_name} - consistency undefined"

    return True, ""


def compute_step_consistency(trajectory: dict, config: dict) -> dict:
    """
    Compute step consistency for each step in a trajectory.

    Args:
        trajectory: A trajectory dictionary containing steps
        config: Configuration dictionary with agent metric configs

    Returns:
        The trajectory with consistency information added to each step
    """
    traj_name = trajectory.get("name", "")
    logger.info(f"\n\n+++ [Mixed Consistency] Computing step consistencies for trajectory {traj_name}")

    for step in trajectory["steps"]:
        if "sampling" not in step:
            logger.debug(f"+++ Skipping step {step['name']} - no sampling information")
            step["consistency"] = get_undefined_consistency()
            continue

        max_samples = config.get("max_samples", step["sampling"]["num_samples"])
        logger.debug(f"+ [Mixed Consistency] with {max_samples} samples")

        # Validate samples and get error message if invalid
        is_valid, error_message = check_sample_validity(step, config)
        if not is_valid:
            logger.debug(f"+++ {error_message}")
            step["consistency"] = get_undefined_consistency()
            continue

        # Get metric configuration (already validated in check_sample_validity)
        metric_config = get_agent_config(step["name"], config)

        # compute consistency according to response type
        metadata = {}
        if metric_config["response_type"] in ["json", "react", "react_aw", "thought_code", "tool_calls"]:
            samples = step["sampling"].get("parsed_samples", [])

            this_config = metric_config
            if "alternates" in metric_config and "parsed_response" in step:
                this_config = find_matching_alternate(metric_config["alternates"], flatten_response(step["parsed_response"]))

            consistency, metadata = compute_json_step_consistency(
                samples,
                this_config,
                int(MIN_FRACTION * max_samples),
            )
            metric = "mixed" if "fields" in metric_config else metric_config.get("metric", "mixed")

        elif metric_config["response_type"] in ["code", "text"]:
            samples = step["sampling"].get("parsed_samples", [])
            if samples == []:  # we don't have any parsed samples
                samples = step["sampling"].get("raw_samples", [])
            metric = metric_config["metric"]
            consistency, _ = get_consistency_by_metric(samples, metric)
            metadata = {"field_consistencies": {}}

        else:
            # This should never happen as check_sample_validity already validates response_type
            raise ValueError(f"Unexpected response type {metric_config['response_type']} in {step['name']}")

        logger.debug(f"+++ Step consistency ({metric_config['response_type']}/{metric}): {consistency}")
        step["consistency"] = {
            "step_consistency": consistency,
            "field_consistencies": metadata.get("field_consistencies", {}),
            "metric": metric,
        }
    return trajectory
