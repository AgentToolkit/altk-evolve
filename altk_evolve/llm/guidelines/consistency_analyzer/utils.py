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


def _has_uniform_keys(list_of_dicts: list) -> bool:
    """Whether every dict in the list carries the same key set.

    invert_list_of_dictionaries appends per key with no positional padding, so a ragged
    list loses the correspondence between an element and its values: [{"tool": "search",
    "args": "q"}, {"tool": "write"}] and the same "args" attached to the *other* tool
    both invert to {"tool": [...], "args": ["q"]}. Downstream that reads as perfect
    consistency for two different responses, so a ragged list must not be inverted.
    """
    expected = list_of_dicts[0].keys()
    return all(item.keys() == expected for item in list_of_dicts)


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

    A top-level list is also left untouched when it is *ragged* — its element dicts do
    not share one key set. Inverting one misaligns element values, so two responses that
    attach the same value to different elements would flatten identically and score as
    perfectly consistent; returning the list unchanged instead reaches the scorer as
    unscorable and is honestly reported undefined.

    Known limits, both shared with the pre-flattening behaviour:

    - A list whose inverted value is itself a *list of lists* of dicts is not
      descended into, so those inner dicts stay raw under the intermediate key
      (e.g. ``[{"y": [{"z": 1}]}, {"y": [{"z": 2}]}]`` keeps ``z`` unreachable).
    - The raggedness guard above covers only the top level. A ragged list nested under a
      key is still inverted and can still misalign, exactly as it did before flattening
      handled top-level lists at all.
    - Not idempotent: a retained intermediate key holds a live list of dicts, so
      re-flattening the result descends into it. Every call site flattens once.

    Args:
        d: Dictionary to flatten (or non-dict value to return as-is)
        parent_key: Prefix for keys (used in recursion)
        sep: Separator for concatenating keys (default: '_')

    Returns:
        Flattened dictionary with concatenated keys
    """
    # Top-level list of dicts: invert to dict of lists so field extraction works. A ragged
    # list is returned untouched instead — inverting it silently misaligns element values
    # and scores two different responses as identical, where an un-inverted list reaches the
    # scorer as unscorable and is honestly reported undefined.
    if isinstance(d, list):
        if _is_list_of_dicts(d) and _has_uniform_keys(d):
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
                    # Always emit the intermediate key. A config field may be named for it —
                    # agent_config.yaml's function_arguments is, whenever tool-call arguments
                    # are dict-valued — and replacing it with deeper keys would resolve that
                    # field to "" and drop it from scoring with no error.
                    items.append((nested_key, in_v))
                    # Descend only when the inner list will actually flatten to a dict. A
                    # ragged one comes back from the guard above as a list, with no .items().
                    if _is_list_of_dicts(in_v) and _has_uniform_keys(in_v):
                        items.extend(flatten_response(in_v, nested_key, sep=sep).items())
        else:
            items.append((new_key, v))

    # dict() keeps the last value for a repeated key, so a post-inversion collision (e.g. a
    # literal "a_b" alongside a nested a -> b) drops one. Not resolvable here without
    # changing the key scheme; this only records it.
    #
    # Deliberately debug, not warning, and only for differing values. Equal values lose
    # nothing, and a differing collision is not reliably data loss either: the intermediate
    # keys emitted above collide with their own deep projection, where the "dropped" value is
    # still reachable under the intermediate key. Distinguishing that from real loss needs
    # provenance this function doesn't track, so an alert here would cry wolf on its own
    # normal output — once per sample per step — and get tuned out.
    grouped: dict[str, list] = defaultdict(list)
    for key, value in items:
        grouped[key].append(value)
    collisions = sorted(key for key, values in grouped.items() if len(values) > 1 and any(v != values[-1] for v in values))
    if collisions:
        logger.debug("flatten_response: flattened key collision on %s — only the last value for each is kept.", collisions)
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
