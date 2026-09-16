"""Shared utility functions for consistency analysis."""

import logging

logger = logging.getLogger(__name__)
from collections import Counter, defaultdict


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


def find_matching_alternate(alternates: list[dict], parsed_actual: dict) -> dict:
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
    # A non-mapping response (a JSON primitive, null, or a list preserved by
    # flatten_response) has no fields to match against, and the `name not in parsed_actual`
    # test below raises TypeError for anything non-iterable. Report no-match instead: callers
    # already treat that as the unsupported-response case and mark consistency undefined.
    if not isinstance(parsed_actual, dict):
        return {}

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


def _is_list_of_dicts(value) -> bool:
    """True for a non-empty list whose every element is a dict.

    Every element is checked, not just the first: invert_list_of_dictionaries calls
    .items() on each one, so a mixed list like [{"a": 1}, 2] would raise AttributeError.
    """
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def flatten_response(d, parent_key="", sep="_"):
    """
    Recursively flatten a nested dictionary structure.

    Converts nested dictionaries into a flat dictionary with concatenated keys.
    Handles lists of dictionaries by inverting them into dictionaries of lists.
    When the top-level value is itself a list of dicts (e.g. a JSON-array
    response), it is inverted first so field extraction works normally.

    Only *homogeneous* lists of dicts are inverted. A mixed list (or a list of
    non-dicts) is preserved as a plain value under its existing key: inverting one
    would call .items() on a non-dict and raise, and consumers such as
    single_step_consistency.py expect to receive such lists intact.

    Known limits, both shared with the pre-flattening behaviour:

    - A list whose inverted value is itself a *list of lists* of dicts is not
      descended into, so those inner dicts stay raw under the intermediate key
      (e.g. ``[{"y": [{"z": 1}]}, {"y": [{"z": 2}]}]`` keeps ``z`` unreachable).
    - ``invert_list_of_dictionaries`` appends per key without positional padding, so
      ragged element dicts lose their alignment: two responses that attach the same
      value to *different* elements can flatten identically.

    Args:
        d: Dictionary to flatten (or non-dict value to return as-is)
        parent_key: Prefix for keys (used in recursion)
        sep: Separator for concatenating keys (default: '_')

    Returns:
        Flattened dictionary with concatenated keys
    """
    # Top-level list of dicts: invert to dict of lists so field extraction works
    if isinstance(d, list):
        if _is_list_of_dicts(d):
            d = invert_list_of_dictionaries(d)
        else:
            return d
    if not isinstance(d, dict):
        return d

    items = []
    for k, v in d.items():
        new_key = parent_key + sep + str(k) if parent_key else str(k)
        if isinstance(v, dict):
            items.extend(flatten_response(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            if not _is_list_of_dicts(v):
                items.append((new_key, v))
            else:
                # v is a list of dicts - invert it to a dict of lists, then recurse
                inverted_v = invert_list_of_dictionaries(v)
                for in_k, in_v in inverted_v.items():
                    nested_key = new_key + sep + in_k
                    if _is_list_of_dicts(in_v):
                        # Emit the intermediate key as well as the deeper ones. A config
                        # field may be named for it — agent_config.yaml's
                        # function_arguments is, whenever tool-call arguments are
                        # dict-valued — and replacing it with deeper keys would resolve
                        # that field to "" and drop it from scoring with no error.
                        items.append((nested_key, in_v))
                        items.extend(flatten_response(in_v, nested_key, sep=sep).items())
                    else:
                        items.append((nested_key, in_v))
        else:
            items.append((new_key, v))

    # dict() keeps the last value for a repeated key, so a post-inversion collision
    # (e.g. a literal "a_b" alongside a nested a -> b) drops data. Can't be resolved
    # here without changing the key scheme, but it should at least be audible.
    collisions = sorted({key for key, count in Counter(key for key, _ in items).items() if count > 1})
    if collisions:
        logger.warning("flatten_response: flattened key collision on %s — only the last value for each is kept.", collisions)
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
