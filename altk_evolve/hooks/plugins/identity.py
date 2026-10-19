"""Structural metadata that redactors must not rewrite.

These values bind storage ownership, authorization, and source-deletion receipts.
They are identifiers, not free-text fields such as titles or descriptions.
"""

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
        # Entity IDs and content digests of the originals a consolidated guideline
        # absorbed; consolidation matches stored IDs against them, and a numeric ID can
        # look like a phone number to a redactor.
        "merged_from",
    }
)

#: Fields of each ``metadata["sources"]`` association. They hold the same identifiers
#: as the root keys above, plus the association's status and time, and deletion
#: receipts are matched on them, so they are structural too. Any other field a
#: source record carries is still free text.
SOURCE_ASSOCIATION_KEYS = frozenset({"conversation_id", "task_id", "user_id", "agent_id", "status", "associated_at"})


def redactable_metadata(metadata: dict) -> dict:
    """``metadata`` without the structural values a redactor must not see."""
    result = {key: value for key, value in metadata.items() if key not in IDENTITY_METADATA_KEYS}
    associations = _associations(metadata)
    if associations is not None:
        result["sources"] = [{key: value for key, value in a.items() if key not in SOURCE_ASSOCIATION_KEYS} for a in associations]
    return result


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
    return result


def _associations(metadata: dict) -> list[dict] | None:
    # Only a well-formed list is source provenance; anything else is ordinary metadata.
    value: Any = metadata.get("sources")
    return value if isinstance(value, list) and all(isinstance(a, dict) for a in value) else None
