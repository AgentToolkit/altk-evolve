"""Retention engine (issue #275).

Evaluates a :class:`~altk_evolve.retention.policy.RetentionPolicy` against the
entities in a namespace and either flags or deletes the matches. Works against
the :class:`~altk_evolve.frontend.client.evolve_client.EvolveClient` public API
so it is backend-agnostic (filesystem / postgres / milvus).

Signals:

- **age** — ``RecordedEntity.created_at``.
- **unused** — ``metadata.last_accessed`` (stamped by ``EvolveClient.record_access``)
  with a fallback to ``created_at`` when an entity has never been accessed.
- **provenance** — a trajectory entity carries ``metadata.trace_id``; entities
  derived from it carry ``metadata.source_task_id == trace_id``. A delete rule
  with ``cascade_derived`` removes those derived memories alongside the session.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from altk_evolve.retention.policy import RetentionPolicy, RetentionRule
from altk_evolve.schema.core import RecordedEntity

logger = logging.getLogger(__name__)


@dataclass
class RetentionItem:
    """One entity the engine decided to act on."""

    entity_id: str
    entity_type: str
    action: str  # "flag" | "delete"
    reason: str  # "age" | "unused" | "cascade:<trace_id>"
    rule: str


@dataclass
class RetentionReport:
    dry_run: bool
    flagged: list[RetentionItem] = field(default_factory=list)
    deleted: list[RetentionItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        verb = "would flag/delete" if self.dry_run else "flagged/deleted"
        return f"{verb}: {len(self.flagged)} flagged, {len(self.deleted)} deleted, {len(self.errors)} errors"


def _as_aware(dt: datetime.datetime) -> datetime.datetime:
    """Treat naive datetimes as UTC so comparisons never raise."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.UTC)
    return dt


def _parse_iso(value) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_aware(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


class RetentionEngine:
    #: Trajectory metadata key holding the session/trace identifier.
    TRACE_KEY = "trace_id"
    #: Derived-entity metadata key linking back to a trajectory's trace_id.
    SOURCE_KEY = "source_task_id"
    #: How many entities to scan per namespace.
    FETCH_LIMIT = 100_000

    def __init__(self, client):
        self.client = client

    # ── signal helpers ────────────────────────────────────────────────

    def _age_days(self, entity: RecordedEntity, now: datetime.datetime) -> float:
        return (now - _as_aware(entity.created_at)).total_seconds() / 86400.0

    def _unused_days(self, entity: RecordedEntity, now: datetime.datetime) -> float:
        last = _parse_iso((entity.metadata or {}).get("last_accessed")) or _as_aware(entity.created_at)
        return (now - last).total_seconds() / 86400.0

    def _match(self, entity: RecordedEntity, rule: RetentionRule, now: datetime.datetime) -> str | None:
        """Return the trigger reason ('age'|'unused') if *rule* matches, else None."""
        if rule.entity_type is not None and entity.type != rule.entity_type:
            return None
        if rule.max_age_days is not None and self._age_days(entity, now) > rule.max_age_days:
            return "age"
        if rule.max_unused_days is not None and self._unused_days(entity, now) > rule.max_unused_days:
            return "unused"
        return None

    def _first_match(self, entity, rules, now) -> tuple[RetentionRule, str] | None:
        for rule in rules:
            reason = self._match(entity, rule, now)
            if reason is not None:
                return rule, reason
        return None

    # ── evaluation ────────────────────────────────────────────────────

    def evaluate(self, namespace_id: str, policy: RetentionPolicy, now: datetime.datetime | None = None) -> list[RetentionItem]:
        """Compute the actions a policy implies, without mutating anything."""
        now = _as_aware(now) if now else datetime.datetime.now(datetime.UTC)
        entities = self.client.get_all_entities(namespace_id, limit=self.FETCH_LIMIT)
        by_id: dict[str, RecordedEntity] = {e.id: e for e in entities}

        # Provenance index: trace_id -> [derived entity ids]
        derived_by_trace: dict[str, list[str]] = {}
        for e in entities:
            src = (e.metadata or {}).get(self.SOURCE_KEY)
            if src is not None:
                derived_by_trace.setdefault(str(src), []).append(e.id)

        # delete supersedes flag for the same entity; first writer otherwise wins.
        actions: dict[str, RetentionItem] = {}

        def record(eid: str, etype: str, action: str, reason: str, rule_name: str) -> None:
            existing = actions.get(eid)
            if existing is not None and (existing.action == "delete" or action == "flag"):
                return
            actions[eid] = RetentionItem(eid, etype, action, reason, rule_name)

        for e in entities:
            match = self._first_match(e, policy.rules, now)
            if match is None:
                continue
            rule, reason = match
            record(e.id, e.type, rule.action, reason, rule.name)

            if rule.action == "delete" and rule.cascade_derived:
                trace = (e.metadata or {}).get(self.TRACE_KEY)
                if trace is None:
                    continue
                for did in derived_by_trace.get(str(trace), []):
                    if did == e.id:
                        continue
                    record(did, by_id[did].type, "delete", f"cascade:{trace}", rule.name)

        return list(actions.values())

    # ── application ───────────────────────────────────────────────────

    def apply(
        self,
        namespace_id: str,
        policy: RetentionPolicy,
        now: datetime.datetime | None = None,
        dry_run: bool = True,
    ) -> RetentionReport:
        """Evaluate and (unless *dry_run*) flag/delete matched entities."""
        now = _as_aware(now) if now else datetime.datetime.now(datetime.UTC)
        items = self.evaluate(namespace_id, policy, now)
        report = RetentionReport(dry_run=dry_run)
        flagged_at = now.isoformat()

        for item in items:
            try:
                if item.action == "delete":
                    if not dry_run:
                        self.client.delete_entity_by_id(namespace_id, item.entity_id)
                    report.deleted.append(item)
                else:  # flag
                    if not dry_run:
                        self.client.patch_entity_metadata(
                            namespace_id,
                            item.entity_id,
                            {
                                "retention_flagged_at": flagged_at,
                                "retention_reason": item.reason,
                                "retention_rule": item.rule,
                            },
                        )
                    report.flagged.append(item)
            except Exception as exc:  # don't let one bad entity abort the sweep
                logger.warning("retention: failed to %s entity %s: %s", item.action, item.entity_id, exc)
                report.errors.append(f"{item.action} {item.entity_id}: {exc}")

        return report
