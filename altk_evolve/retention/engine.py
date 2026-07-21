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
  an unused rule degrades into an age rule** — so a ``delete`` rule matching on
  a missing stamp does NOT delete by default. The per-rule
  ``on_missing_access_signal`` knob (``skip`` default / ``flag`` / ``delete``)
  governs that; ``skip`` spares the entity and records it in
  ``RetentionReport.skipped``.
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
    #: Entities a rule matched but the engine declined to act on because the
    #: signal was degraded (an ``unused`` DELETE with no real ``last_accessed``
    #: stamp, under ``on_missing_access_signal: skip``). Recorded so a dry run /
    #: CLI can show what was spared and why, rather than silently dropping it.
    skipped: list[RetentionItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Non-fatal caveats about the run (e.g. degraded "unused" signal).
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        verb = "would flag/delete" if self.dry_run else "flagged/deleted"
        summary = f"{verb}: {len(self.flagged)} flagged, {len(self.deleted)} deleted, {len(self.errors)} errors"
        if self.skipped:
            summary += f", {len(self.skipped)} skipped (degraded signal)"
        return summary


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
            # A falsy id (None, "", 0, False) is not a real provenance link;
            # coercing it would create a degenerate cascade bucket that swallows
            # every entity sharing that non-link (an empty-string trace_id would
            # cascade-delete everything with an empty-string source_task_id). We
            # require a real value, then normalize to ``str`` so an int trace_id
            # matches a str source_task_id of the same value and never an empty.
            if value:
                return str(value)
        return None

    def _match(self, entity: RecordedEntity, rule: RetentionRule, now: datetime.datetime) -> tuple[str, str, bool] | None:
        """Return ``(reason, detail, degraded)`` if *rule* matches, else ``None``.

        ``degraded`` is True only for an ``unused`` match whose disuse was
        measured from ``created_at`` because the entity carried no real
        ``metadata.last_accessed`` stamp. ``on_missing_access_signal`` governs
        whether such a match is still allowed to delete.
        """
        if rule.entity_type is not None and entity.type != rule.entity_type:
            return None
        if rule.max_age_days is not None:
            age = self._age_days(entity, now)
            if age > rule.max_age_days:
                return "age", f"created {age:.1f}d ago > max_age_days={rule.max_age_days}", False
        if rule.max_unused_days is not None:
            idle, stamped = self._unused_days(entity, now)
            if idle > rule.max_unused_days:
                detail = f"not read for {idle:.1f}d > max_unused_days={rule.max_unused_days}"
                detail += " (from metadata.last_accessed)" if stamped else f" — {NO_ACCESS_SIGNAL_HINT}"
                return "unused", detail, not stamped
        return None

    def _first_match(
        self, entity: RecordedEntity, rules: list[RetentionRule], now: datetime.datetime
    ) -> tuple[RetentionRule, str, str, bool] | None:
        for rule in rules:
            matched = self._match(entity, rule, now)
            if matched is not None:
                return rule, matched[0], matched[1], matched[2]
        return None

    # ── evaluation ────────────────────────────────────────────────────

    def evaluate(
        self,
        namespace_id: str,
        policy: RetentionPolicy,
        now: datetime.datetime | None = None,
        warnings: list[str] | None = None,
        skipped: list[RetentionItem] | None = None,
        scan_limit: int | None = None,
    ) -> list[RetentionItem]:
        """Compute the actions a policy implies, without mutating anything.

        When *warnings* is passed, non-fatal caveats about the run are appended
        to it (``apply`` uses this to populate ``RetentionReport.warnings``).
        When *skipped* is passed, entities a rule matched but the engine
        declined to act on (degraded ``unused`` signal under
        ``on_missing_access_signal: skip``) are appended to it.

        *scan_limit* caps how many entities are fetched from the namespace in
        one call; it defaults to :attr:`FETCH_LIMIT`. When the fetch returns
        exactly the limit a warning is emitted, since entities beyond it were
        not evaluated (and therefore not cascaded).
        """
        now = _as_aware(now) if now else datetime.datetime.now(datetime.UTC)
        limit = self.FETCH_LIMIT if scan_limit is None else scan_limit
        entities = self.client.get_all_entities(namespace_id, limit=limit)
        if warnings is not None and len(entities) >= limit:
            warnings.append(
                f"scan hit the fetch limit of {limit} entities in namespace {namespace_id!r}; entities beyond it "
                "were not evaluated (and any memories they would have cascaded were not deleted). Raise scan_limit "
                "or batch the namespace."
            )
        by_id: dict[str, RecordedEntity] = {e.id: e for e in entities}

        # Provenance index: trace id -> derived entity ids. A falsy
        # source_task_id is not a real link (see ``_trace_id``), so those
        # entities are skipped rather than bucketed under an empty/degenerate
        # key — otherwise a session with a falsy trace id would cascade-delete
        # every entity that merely lacks provenance.
        derived_by_trace: dict[str, list[str]] = {}
        for e in entities:
            src = (e.metadata or {}).get(self.SOURCE_KEY)
            if not src:
                continue
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
            rule, reason, detail, degraded = match

            action = rule.action
            # A degraded "unused" match (no real last_accessed stamp, disuse
            # measured from created_at) only reaches a destructive action when
            # the rule opts in. Default `skip` spares it; `flag` downgrades the
            # delete to a non-destructive flag; `delete` keeps the old behavior.
            # This only bites deletes — a flag action is not data loss, so a
            # flag rule flags a never-stamped entity as usual.
            if degraded and rule.action == "delete":
                choice = rule.on_missing_access_signal
                if choice == "skip":
                    if skipped is not None:
                        skipped.append(
                            RetentionItem(
                                e.id,
                                e.type,
                                "skip",
                                reason,
                                rule.name,
                                f"matched but not deleted: {detail}; on_missing_access_signal=skip",
                            )
                        )
                    continue
                if choice == "flag":
                    action = "flag"
                    detail += "; downgraded delete->flag (on_missing_access_signal=flag)"

            record(RetentionItem(e.id, e.type, action, reason, rule.name, detail))

            if action == "delete" and rule.cascade_derived:
                trace = self._trace_id(e)
                if not trace:
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
        scan_limit: int | None = None,
    ) -> RetentionReport:
        """Evaluate and — unless *dry_run* — flag/delete the matched entities.

        Dry run is the default: nothing is mutated and the report describes what
        *would* happen. *scan_limit* caps how many entities are fetched per
        namespace (defaults to :attr:`FETCH_LIMIT`); a boundary hit is surfaced
        in ``report.warnings``.
        """
        now = _as_aware(now) if now else datetime.datetime.now(datetime.UTC)
        report = RetentionReport(dry_run=dry_run)
        items = self.evaluate(
            namespace_id,
            policy,
            now,
            warnings=report.warnings,
            skipped=report.skipped,
            scan_limit=scan_limit,
        )
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
