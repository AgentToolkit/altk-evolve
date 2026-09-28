"""Transport-independent retention report projections."""

import json
from dataclasses import asdict
from typing import Any
from altk_evolve.schema.core import RecordedEntity


def _entity_payload(entity: RecordedEntity, *, include_content: bool = True) -> dict[str, Any]:
    content = entity.content
    preview_source = content if isinstance(content, str) else json.dumps(content, default=str)
    payload: dict[str, Any] = {
        "id": entity.id,
        "type": entity.type,
        "content_preview": preview_source[:240],
        "created_at": entity.created_at.isoformat(),
        "metadata": entity.metadata or {},
    }
    if include_content:
        payload["content"] = content
    return payload


def _retention_item_payload(item: Any, entity: RecordedEntity | None, *, dry_run: bool) -> dict[str, Any]:
    payload = asdict(item)
    applied_outcomes = {"flag": "flagged", "delete": "deleted", "skip": "skipped"}
    payload["outcome"] = f"would_{item.action}" if dry_run else applied_outcomes.get(item.action, item.action)
    if entity is not None:
        snapshot = _entity_payload(entity, include_content=False)
        metadata = entity.metadata or {}
        payload.update(
            {
                "created_at": snapshot["created_at"],
                "content_preview": snapshot["content_preview"],
                "metadata": metadata,
                "user_id": metadata.get("user_id") or metadata.get("owner_id"),
                "agent_id": metadata.get("agent_id"),
                "session_id": metadata.get("session_id") or metadata.get("thread_id"),
                "source_task_id": metadata.get("source_task_id"),
            }
        )
    return payload


def audit_payload(report: dict[str, Any]) -> dict[str, Any]:
    """Keep durable run history useful without retaining deleted memory content."""
    audit: dict[str, Any] = {
        key: report[key]
        for key in (
            "run_id",
            "namespace_id",
            "policy_id",
            "policy_name",
            "initiated_by",
            "started_at",
            "completed_at",
            "as_of",
            "dry_run",
            "cancelled",
        )
        if key in report
    }
    audit["error_count"] = report.get("error_count", len(report.get("errors", [])))
    audit["warning_count"] = report.get("warning_count", len(report.get("warnings", [])))
    item_fields = {"entity_id", "entity_type", "created_at", "action", "outcome", "reason", "rule"}
    for bucket in ("flagged", "deleted", "skipped"):
        items = []
        for item in report.get(bucket, []):
            if not isinstance(item, dict):
                continue
            projected = {key: item[key] for key in item_fields if key in item}
            reason = projected.get("reason")
            if isinstance(reason, str) and reason.startswith("cascade:"):
                projected["reason"] = "cascade"
            items.append(projected)
        audit[bucket] = items
    return audit
