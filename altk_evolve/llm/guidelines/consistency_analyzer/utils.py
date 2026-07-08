"""Shared utility functions for consistency analysis."""

import logging

logger = logging.getLogger(__name__)
from collections import defaultdict


def extract_field_values_from_responses(flat_responses: list[dict], field: dict) -> list[str]:
    """
    Extract field values from flattened responses.

    This function handles both single field names (e.g., "action") and
    multi-field names (e.g., ["action", "action_input"]), concatenating
    values from multiple fields with space separation.

    Args:
        flat_responses: List of flattened response dictionaries
        field: Field config dict with 'name' key (str or list[str])

    Returns:
        List of field values, one per response
    """
    field_samples = []

    # Handle both string and list field names
    if isinstance(field["name"], str):
        names = [field["name"]]
    elif isinstance(field["name"], list):
        names = field["name"]
    else:
        raise ValueError(f"Invalid field name type: {type(field['name'])}")

    for response in flat_responses:
        if not isinstance(response, dict):
            field_samples.append("")
            continue

        # Concatenate values from all field names
        field_val = ""
        for name in names:
            if name in response:
                value = response[name]
                if isinstance(value, list):
                    field_val += " ".join(str(item) for item in value)
                else:
                    field_val += str(value)

        field_samples.append(field_val)

    return field_samples


def find_matching_alternate(alternates: dict, parsed_actual: dict) -> dict:
    """
    Find the first alternate configuration that matches the actual parsed response.

    A match occurs when all fields mentioned in the alternate config are present
    in the actual response.

    Args:
        alternates: List of alternate configuration dictionaries
        parsed_actual: The actual parsed response dictionary

    Returns:
        The matching alternate configuration, or empty dict if no match found
    """
    # We consider it a match if we can find every field mentioned in the alternate config in the actual response
    for alternate in alternates:
        field_list = alternate["fields"]
        matched = True
        for field in field_list:
            if isinstance(field.get("name"), str):
                names = [field["name"]]
            elif isinstance(field.get("name"), list):
                names = field["name"]
            else:
                raise TypeError(f"Field name must be str or list, got {type(field.get('name'))}")
            for name in names:
                if name not in parsed_actual:
                    matched = False
                    break
        if matched:
            return alternate

    return {}


def invert_list_of_dictionaries(list_of_dicts):
    """
    Inverts a list of dictionaries into a dictionary of lists.

    Args:
        list_of_dicts: A list where each element is a dictionary with the same keys.

    Returns:
        A dictionary where keys are the common keys from the input dictionaries,
        and values are lists containing the corresponding values from each dictionary.
    """
    inverted_dict = defaultdict(list)
    for d in list_of_dicts:
        for key, value in d.items():
            inverted_dict[key].append(value)
    return dict(inverted_dict)


def flatten_response(d, parent_key="", sep="_"):
    """
    Recursively flatten a nested dictionary structure.

    Converts nested dictionaries into a flat dictionary with concatenated keys.
    Handles lists of dictionaries by inverting them into dictionaries of lists.

    Args:
        d: Dictionary to flatten (or non-dict value to return as-is)
        parent_key: Prefix for keys (used in recursion)
        sep: Separator for concatenating keys (default: '_')

    Returns:
        Flattened dictionary with concatenated keys
    """
    if not isinstance(d, dict):
        return d

    items = []
    for k, v in d.items():
        new_key = parent_key + sep + str(k) if parent_key else str(k)
        if isinstance(v, dict):
            items.extend(flatten_response(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            if v == [] or not isinstance(v[0], dict):
                items.append((new_key, v))
            else:
                # v is a list of dicts - invert it to a dict of lists
                inverted_v = invert_list_of_dictionaries(v)
                for in_k, in_v in inverted_v.items():
                    items.append((new_key + sep + in_k, in_v))
        else:
            items.append((new_key, v))
    return dict(items)


def rescale_weights(step_cns_list: list) -> list:
    """
    Rescale weights to enforce that they add up to 1.

    Args:
        step_cns_list: List of dicts with 'weight' and 'consistency' keys

    Returns:
        List with rescaled weights
    """
    # first fix any anomalies such as missing or negative weights
    default_weight = 1 / len(step_cns_list)
    for field in step_cns_list:
        if field["weight"] == -1:  # this happens when there was no weight in the config
            field["weight"] = default_weight
        elif field["weight"] < 0:
            field["weight"] = 0  # treat invalid negative weights as 0 weight

    total_weight = sum([field["weight"] for field in step_cns_list])
    if total_weight == 0:
        # treat this anomoly as if no weights were specified: by assigning the default_weight
        for field in step_cns_list:
            field["weight"] = default_weight
    else:
        scale_factor = 1 / total_weight

        for field in step_cns_list:
            field["weight"] = field["weight"] * scale_factor

    return step_cns_list


def compute_weighted_sum_consistency(step_cns_list: list, field_consistencies: dict) -> tuple[float, dict]:
    """
    Compute weighted sum of field consistencies.

    Args:
        step_cns_list: List of dicts with 'consistency', 'weight', and 'name' keys

    Returns:
        Weighted sum of consistencies
    """
    step_cns_list = rescale_weights(step_cns_list)
    consistency = 0
    for field in step_cns_list:
        consistency += field["consistency"] * field["weight"]
        # update field_consistencies with the rescaled weights
        field_name = "-".join(field["name"]) if isinstance(field["name"], list) else field["name"]
        if field_name in field_consistencies:
            field_consistencies[field_name]["weight"] = field["weight"]

    logger.debug(f"+++ Processing weighted sum step consistencies: {step_cns_list}")
    return consistency, field_consistencies
