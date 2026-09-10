"""Retention execution shared by Python, CLI, REST, MCP, and scheduling."""

import datetime
import logging
import uuid
from typing import Any
from altk_evolve.retention.reports import audit_payload, _retention_item_payload
from altk_evolve.frontend.services.context import cancellation_requested

logger = logging.getLogger(__name__)


def execute_policy(
    client,
    store,
    namespace_id: str,
    policy_id: str,
    *,
    dry_run: bool = True,
    as_of: datetime.datetime | None = None,
    scan_limit: int | None = None,
    run_id: str | None = None,
    metadata_filters: dict[str, Any] | None = None,
    additional_matches: list[dict[str, Any]] | None = None,
    initiated_by: str | None = None,
) -> dict[str, Any]:
    """Execute a validated request through hooks and persist a content-free audit."""
    from altk_evolve.retention import RetentionEngine, RetentionItem, RetentionPolicy

    now = as_of
    metadata_filter_values = metadata_filters
    backend_filters = {f"metadata.{key}": value for key, value in metadata_filters.items()} if metadata_filters else None
    parsed_matches = additional_matches or []
    resolved_ns = namespace_id
    stored_policy = store.get_policy(namespace_id=resolved_ns, policy_id=policy_id)
    if stored_policy is None:
        return {"error": f"Retention policy {policy_id!r} not found"}
    if not stored_policy["enabled"]:
        return {"error": f"Retention policy {policy_id!r} is disabled"}
    normalized_policy = RetentionPolicy.from_mapping(stored_policy["policy"])
    resolved_run_id = run_id or str(uuid.uuid4())
    started_at = datetime.datetime.now(datetime.UTC)
    store.save_run(
        namespace_id=resolved_ns,
        run_id=resolved_run_id,
        policy_id=policy_id,
        agent_id=(metadata_filter_values or {}).get("agent_id"),
        initiated_by=initiated_by,
        status="running",
        report={"run_id": resolved_run_id, "started_at": started_at.isoformat()},
        created_at=started_at.isoformat(),
    )
    try:
        engine = RetentionEngine(client)
        report = engine.apply(resolved_ns, normalized_policy, now=now, dry_run=dry_run, scan_limit=scan_limit, filters=backend_filters)
        snapshots = {entity.id: entity for entity in engine.last_scanned_entities}
        deleted_ids = {item.entity_id for item in report.deleted}
        for match in parsed_matches:
            if report.cancelled or cancellation_requested():
                report.cancelled = True
                break
            entity_id = str(match.get("entity_id") or "").strip()
            rule = str(match.get("rule") or "external-match").strip()
            reason = str(match.get("reason") or "external_match").strip()
            detail = str(match.get("detail") or "matched an external retention criterion").strip()
            if not entity_id or entity_id in deleted_ids:
                continue
            entity = snapshots.get(entity_id)
            if entity is None:
                scoped_filters = {"id": entity_id, **(backend_filters or {})}
                matches = client.scan_entities(resolved_ns, filters=scoped_filters, limit=1)
                entity = matches[0] if matches else None
            if entity is None:
                report.errors.append(f"external match {entity_id}: entity was not found in the requested scope")
                continue
            snapshots[entity_id] = entity
            item = RetentionItem(entity_id, entity.type, "delete", reason, rule, detail)
            report.flagged[:] = [entry for entry in report.flagged if entry.entity_id != entity_id]
            report.skipped[:] = [entry for entry in report.skipped if entry.entity_id != entity_id]
            try:
                if not dry_run:
                    client.delete_entity_by_id(resolved_ns, entity_id)
                report.deleted.append(item)
                deleted_ids.add(entity_id)
            except Exception as exc:
                logger.warning("retention: failed to delete external match %s: %s", entity_id, exc)
                report.errors.append(f"delete {entity_id}: {exc}")
                report.skipped.append(RetentionItem(entity_id, entity.type, "skip", "delete_failed", rule, f"deletion failed: {exc}"))
        completed_at = datetime.datetime.now(datetime.UTC)
        result = {
            "run_id": resolved_run_id,
            "namespace_id": resolved_ns,
            "policy_id": policy_id,
            "policy_name": stored_policy["name"],
            "initiated_by": initiated_by,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "as_of": (now or completed_at).isoformat(),
            "dry_run": report.dry_run,
            "cancelled": report.cancelled,
            "policy": normalized_policy.model_dump(mode="json"),
            "metadata_filters": metadata_filter_values,
            "summary": report.summary(),
            "flagged": [_retention_item_payload(item, snapshots.get(item.entity_id), dry_run=dry_run) for item in report.flagged],
            "deleted": [_retention_item_payload(item, snapshots.get(item.entity_id), dry_run=dry_run) for item in report.deleted],
            "skipped": [_retention_item_payload(item, snapshots.get(item.entity_id), dry_run=dry_run) for item in report.skipped],
            "errors": report.errors,
            "warnings": report.warnings,
        }
        store.save_run(
            namespace_id=resolved_ns,
            run_id=resolved_run_id,
            policy_id=policy_id,
            agent_id=(metadata_filter_values or {}).get("agent_id"),
            initiated_by=initiated_by,
            status="cancelled" if report.cancelled else "failed" if report.errors else "completed",
            report=audit_payload(result),
            created_at=started_at.isoformat(),
        )
        return result
    except Exception as exc:
        logger.exception("retention run %s failed", resolved_run_id)
        completed_at = datetime.datetime.now(datetime.UTC)
        failed_result = {
            "run_id": resolved_run_id,
            "namespace_id": resolved_ns,
            "policy_id": policy_id,
            "policy_name": stored_policy["name"],
            "initiated_by": initiated_by,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "as_of": (now or completed_at).isoformat(),
            "dry_run": dry_run,
            "errors": ["Retention execution failed"],
            "warnings": [],
            "failure": {"type": type(exc).__name__},
        }
        failed_audit = audit_payload(failed_result)
        failed_audit["failure"] = failed_result["failure"]
        try:
            store.save_run(
                namespace_id=resolved_ns,
                run_id=resolved_run_id,
                policy_id=policy_id,
                agent_id=(metadata_filter_values or {}).get("agent_id"),
                initiated_by=initiated_by,
                status="failed",
                report=failed_audit,
                created_at=started_at.isoformat(),
            )
        except Exception:
            logger.exception("retention run %s failed to persist its terminal status", resolved_run_id)
        return {"error": "Retention run failed", "run_id": resolved_run_id, "failure": {"type": type(exc).__name__}}
