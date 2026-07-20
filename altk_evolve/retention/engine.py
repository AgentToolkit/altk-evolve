"""Retention engine (issue #275).

Evaluates a :class:`~altk_evolve.retention.policy.RetentionPolicy` against the
entities in a namespace and either flags or deletes the matches. It drives the
:class:`~altk_evolve.frontend.client.evolve_client.EvolveClient` public API, so
it is backend-agnostic (filesystem / postgres / milvus) and every delete it
issues flows through the ``memory_pre_delete`` hook — a legal-hold plugin can
veto a retention delete.

Signals:

- **age** — ``RecordedEntity.created_at``.
- **unused** — ``metadata.last_accessed``. That key is stamped automatically by
  ``altk_evolve.hooks.plugins.access_stamp.AccessStampPlugin`` on every public
  read, and explicitly by ``EvolveClient.record_access``. Entities carrying no
  stamp fall back to ``created_at`` — the engine records that fallback on the
  item's ``detail`` and in ``RetentionReport.warnings`` rather than silently
  pretending it measured disuse. **Without an access-stamping mechanism enabled
  an unused rule degrades into an age rule.**
- **provenance** — a session entity carries ``metadata.trace_id``; entities
  derived from it carry ``metadata.source_task_id == trace_id``. A delete rule
  with ``cascade_derived`` removes those derived memories alongside the session.
  The MCP server historically wrote ``task_id`` where Phoenix sync wrote
  ``trace_id``; ``MetadataNormalizerPlugin`` now copies one into the other at
  write time, and this engine additionally falls back to ``task_id`` at read
  time so sessions written before the normalizer still cascade.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any

from altk_evolve.retention.policy import RetentionPolicy, RetentionRule
from altk_evolve.schema.core import RecordedEntity

logger = logging.getLogger(__name__)

#: Hint appended to items whose "unused" verdict had no real recall signal.
NO_ACCESS_SIGNAL_HINT = (
    "no metadata.last_accessed on this entity, so disuse was measured from created_at; "
    "enable AccessStampPlugin (or call EvolveClient.record_access) for a real recall signal"
)


@dataclass
class RetentionItem:
    """One entity the engine decided to act on."""

    entity_id: str
    entity_type: str
    action: str  # "flag" | "delete"
    reason: str  # "age" | "unused" | "cascade:<trace_id>"
    rule: str
    #: Human-readable "why", including which signal was used and any fallback.
    detail: str = ""


@dataclass
class RetentionReport:
    dry_run: bool
    flagged: list[RetentionItem] = field(default_factory=list)
    deleted: list[RetentionItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Non-fatal caveats about the run (e.g. degraded "unused" signal).
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        verb = "would flag/delete" if self.dry_run else "flagged/deleted"
        return f"{verb}: {len(self.flagged)} flagged, {len(self.deleted)} deleted, {len(self.errors)} errors"


def _as_aware(dt: datetime.datetime) -> datetime.datetime:
    """Treat naive datetimes as UTC so comparisons never raise."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.UTC)
    return dt


def _parse_iso(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _as_aware(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


class RetentionEngine:
    """Applies a retention policy to a namespace."""

    #: Session metadata keys holding the trace/session identifier, in priority
    #: order. ``task_id`` is the MCP server's spelling; MetadataNormalizerPlugin
    #: copies it to ``trace_id`` on write, and this fallback covers entities
    #: written before that plugin existed.
    TRACE_KEYS = ("trace_id", "task_id")
    #: Derived-entity metadata key linking back to a session's trace id.
    SOURCE_KEY = "source_task_id"
    #: How many entities to scan per namespace.
    FETCH_LIMIT = 100_000

    def __init__(self, client: Any) -> None:
        self.client = client

    # ── signal helpers ────────────────────────────────────────────────

    def _age_days(self, entity: RecordedEntity, now: datetime.datetime) -> float:
        return (now - _as_aware(entity.created_at)).total_seconds() / 86400.0

    def _unused_days(self, entity: RecordedEntity, now: datetime.datetime) -> tuple[float, bool]:
        """Return ``(days_since_last_read, had_access_stamp)``.

        ``had_access_stamp`` is False when the entity carried no usable
        ``metadata.last_accessed`` and ``created_at`` was used instead.
        """
        last = _parse_iso((entity.metadata or {}).get("last_accessed"))
        stamped = last is not None
        if last is None:
            last = _as_aware(entity.created_at)
        return (now - last).total_seconds() / 86400.0, stamped

    def _trace_id(self, entity: RecordedEntity) -> str | None:
        metadata = entity.metadata or {}
        for key in self.TRACE_KEYS:
            value = metadata.get(key)
            if value is not None:
                return str(value)
        return None

    def _match(self, entity: RecordedEntity, rule: RetentionRule, now: datetime.datetime) -> tuple[str, str] | None:
        """Return ``(reason, detail)`` if *rule* matches *entity*, else ``None``."""
        if rule.entity_type is not None and entity.type != rule.entity_type:
            return None
        if rule.max_age_days is not None:
            age = self._age_days(entity, now)
            if age > rule.max_age_days:
                return "age", f"created {age:.1f}d ago > max_age_days={rule.max_age_days}"
        if rule.max_unused_days is not None:
            idle, stamped = self._unused_days(entity, now)
            if idle > rule.max_unused_days:
                detail = f"not read for {idle:.1f}d > max_unused_days={rule.max_unused_days}"
                detail += " (from metadata.last_accessed)" if stamped else f" — {NO_ACCESS_SIGNAL_HINT}"
                return "unused", detail
        return None

    def _first_match(
        self, entity: RecordedEntity, rules: list[RetentionRule], now: datetime.datetime
    ) -> tuple[RetentionRule, str, str] | None:
        for rule in rules:
            matched = self._match(entity, rule, now)
            if matched is not None:
                return rule, matched[0], matched[1]
        return None

    # ── evaluation ────────────────────────────────────────────────────

    def evaluate(
        self,
        namespace_id: str,
        policy: RetentionPolicy,
        now: datetime.datetime | None = None,
        warnings: list[str] | None = None,
    ) -> list[RetentionItem]:
        """Compute the actions a policy implies, without mutating anything.

        When *warnings* is passed, non-fatal caveats about the run are appended
        to it (``apply`` uses this to populate ``RetentionReport.warnings``).
        """
        now = _as_aware(now) if now else datetime.datetime.now(datetime.UTC)
        entities = self.client.get_all_entities(namespace_id, limit=self.FETCH_LIMIT)
        by_id: dict[str, RecordedEntity] = {e.id: e for e in entities}

        # Provenance index: trace id -> derived entity ids.
        derived_by_trace: dict[str, list[str]] = {}
        for e in entities:
            src = (e.metadata or {}).get(self.SOURCE_KEY)
            if src is not None:
                derived_by_trace.setdefault(str(src), []).append(e.id)

        # delete supersedes flag for the same entity; first writer otherwise wins.
        actions: dict[str, RetentionItem] = {}

        def record(item: RetentionItem) -> None:
            existing = actions.get(item.entity_id)
            if existing is not None and (existing.action == "delete" or item.action == "flag"):
                return
            actions[item.entity_id] = item

        unstamped = 0
        uses_unused_rule = any(r.max_unused_days is not None for r in policy.rules)

        for e in entities:
            if uses_unused_rule and "last_accessed" not in (e.metadata or {}):
                unstamped += 1

            match = self._first_match(e, policy.rules, now)
            if match is None:
                continue
            rule, reason, detail = match
            record(RetentionItem(e.id, e.type, rule.action, reason, rule.name, detail))

            if rule.action == "delete" and rule.cascade_derived:
                trace = self._trace_id(e)
                if trace is None:
                    continue
                for did in derived_by_trace.get(trace, []):
                    if did == e.id:
                        continue
                    record(
                        RetentionItem(
                            did,
                            by_id[did].type,
                            "delete",
                            f"cascade:{trace}",
                            rule.name,
                            f"derived from session {e.id} (metadata.{self.SOURCE_KEY} == {trace}), which this rule deletes",
                        )
                    )

        if warnings is not None and unstamped:
            warnings.append(
                f"{unstamped} of {len(entities)} entities carry no metadata.last_accessed, so their disuse was "
                "measured from created_at — for those entities an unused rule behaves like an age rule. Enable "
                "AccessStampPlugin (or call EvolveClient.record_access) for a real recall signal."
            )

        return list(actions.values())

    # ── application ───────────────────────────────────────────────────

    def apply(
        self,
        namespace_id: str,
        policy: RetentionPolicy,
        now: datetime.datetime | None = None,
        dry_run: bool = True,
    ) -> RetentionReport:
        """Evaluate and — unless *dry_run* — flag/delete the matched entities.

        Dry run is the default: nothing is mutated and the report describes what
        *would* happen.
        """
        now = _as_aware(now) if now else datetime.datetime.now(datetime.UTC)
        report = RetentionReport(dry_run=dry_run)
        items = self.evaluate(namespace_id, policy, now, warnings=report.warnings)
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
            except Exception as exc:  # one bad entity must not abort the sweep
                logger.warning("retention: failed to %s entity %s: %s", item.action, item.entity_id, exc)
                report.errors.append(f"{item.action} {item.entity_id}: {exc}")

        return report
