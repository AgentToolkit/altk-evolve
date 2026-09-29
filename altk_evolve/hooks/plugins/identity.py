"""Structural metadata that redactors must not rewrite.

These values bind storage ownership, authorization, and source-deletion receipts.
They are identifiers, not free-text fields such as titles or descriptions.
"""

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
