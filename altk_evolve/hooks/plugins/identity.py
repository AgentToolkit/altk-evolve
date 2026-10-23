"""Structural metadata that redactors must not rewrite.

These values bind storage ownership, authorization, and source-deletion receipts.
They are identifiers, not free-text fields such as titles or descriptions.
"""

import re
from typing import Any

IDENTITY_METADATA_KEYS = frozenset(
    {
        "user_id",
        "owner_id",
        "agent_id",
        "namespace_id",
        "tenant_id",
        "thread_id",
        "session_id",
        "trace_id",
        "task_id",
        "source_task_id",
    }
)

#: Fields of each ``metadata["sources"]`` association. They hold the same identifiers
#: as the root keys above, plus the association's status and time, and deletion
#: receipts are matched on them, so they are structural too. Any other field a
#: source record carries is still free text.
SOURCE_ASSOCIATION_KEYS = frozenset({"conversation_id", "task_id", "user_id", "agent_id", "status", "associated_at"})

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


def is_merged_from_entry(value: Any) -> bool:
    """Whether ``value`` is a well-formed ``metadata["merged_from"]`` entry.

    A consolidated guideline records each original it absorbed as ``{"id", "content_sha256"}``.
    Consolidation matches stored IDs against them, and a numeric ID can look like a phone
    number (a digest like a hex secret) to a redactor, so these entries are structural.
    Only this exact shape is: any other value under the key is free text, and consolidation
    ignores it too.
    """
    return (
        isinstance(value, dict)
        and value.keys() == {"id", "content_sha256"}
        and isinstance(value["id"], str)
        and isinstance(value["content_sha256"], str)
        and _SHA256_HEX.fullmatch(value["content_sha256"]) is not None
    )


def redactable_metadata(metadata: dict) -> dict:
    """``metadata`` without the structural values a redactor must not see."""
    result = {key: value for key, value in metadata.items() if key not in IDENTITY_METADATA_KEYS}
    associations = _associations(metadata)
    if associations is not None:
        result["sources"] = [{key: value for key, value in a.items() if key not in SOURCE_ASSOCIATION_KEYS} for a in associations]
    return without_merged_from_entries(result)


def restore_structural(redacted: dict, original: dict) -> dict:
    """``redacted`` (a redacted :func:`redactable_metadata` view) with ``original``'s structural values put back."""
    result = {**redacted, **{key: value for key, value in original.items() if key in IDENTITY_METADATA_KEYS}}
    associations = _associations(original)
    if associations is not None:
        redacted_associations = redacted.get("sources")
        if not isinstance(redacted_associations, list) or len(redacted_associations) != len(associations):
            raise ValueError("Redaction changed the source associations")
        result["sources"] = [
            {**view, **{key: value for key, value in a.items() if key in SOURCE_ASSOCIATION_KEYS}}
            for view, a in zip(redacted_associations, associations, strict=True)
        ]
    return restore_merged_from_entries(result, original)


def without_merged_from_entries(metadata: dict) -> dict:
    """``metadata`` without the well-formed ``merged_from`` entries (see :func:`is_merged_from_entry`)."""
    merged_from = metadata.get("merged_from")
    if not isinstance(merged_from, list):
        return metadata
    return {**metadata, "merged_from": [entry for entry in merged_from if not is_merged_from_entry(entry)]}


def restore_merged_from_entries(redacted: dict, original: dict) -> dict:
    """``redacted`` (a redacted :func:`without_merged_from_entries` view) with ``original``'s entries put back in place."""
    merged_from = original.get("merged_from")
    if not isinstance(merged_from, list):
        return redacted
    rest = redacted.get("merged_from")
    if not isinstance(rest, list) or len(rest) != sum(not is_merged_from_entry(entry) for entry in merged_from):
        raise ValueError("Redaction changed the merged_from entries")
    remaining = iter(rest)
    return {**redacted, "merged_from": [entry if is_merged_from_entry(entry) else next(remaining) for entry in merged_from]}


def _associations(metadata: dict) -> list[dict] | None:
    # Only a well-formed list is source provenance; anything else is ordinary metadata.
    value: Any = metadata.get("sources")
    return value if isinstance(value, list) and all(isinstance(a, dict) for a in value) else None
