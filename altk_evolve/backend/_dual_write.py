"""Dual-write wrapper for the Phase 1 → Phase 3 cutover bake-in window.

Mirrors every mutating operation to a *shadow* backend so the legacy
backend can be used as a rollback target. Reads always come from the
primary backend.

Failure semantics (Phase 1 — best-effort):
- Primary failure → exception propagates to the caller; shadow not touched.
- Shadow failure → logged at WARN with a `pending_shadow_writes` counter
  bump on a private metric; primary's result is returned to the caller.

Phase 3 hardens this with a durable outbox + drift-check + the
`legacy_rollback_safe` invariant (see design_doc/implementation_plan.md
§7 "Rollback consistency strategy"). For Phase 1, the goal is just to
keep the legacy backend warm so we have *something* to roll back to;
strict consistency comes later.
"""

from __future__ import annotations

import logging
from typing import Any

from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.core import Entity, Namespace, RecordedEntity


logger = logging.getLogger(__name__)


class DualWriteBackend(BaseEntityBackend):
    """Composite backend forwarding writes to two backends, reads to one.

    `primary` is the source of truth. `shadow` is best-effort; a shadow
    failure does not propagate.
    """

    def __init__(self, primary: BaseEntityBackend, shadow: BaseEntityBackend) -> None:
        # Skip BaseEntityBackend.__init__ (it just `pass`es) — we don't have
        # our own settings type.
        self.primary = primary
        self.shadow = shadow
        self.pending_shadow_writes: int = 0  # crude counter; Phase 3 replaces with a durable outbox.

    # ── lifecycle (read-only delegates to primary) ─────────────────────────

    def ready(self) -> bool:
        return self.primary.ready()

    def details(self) -> dict:
        return {
            "wrapper": "DualWriteBackend",
            "primary": self.primary.details(),
            "shadow": self.shadow.details(),
            "pending_shadow_writes": self.pending_shadow_writes,
        }

    def close(self) -> None:
        try:
            self.primary.close()
        finally:
            try:
                self.shadow.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("shadow.close() failed: %s", exc)

    # ── reads (primary only) ──────────────────────────────────────────────

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        return self.primary.get_namespace_details(namespace_id)

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        return self.primary.search_namespaces(limit)

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        return self.primary.search_entities(namespace_id, query, filters, limit)

    # ── writes (primary then shadow, shadow best-effort) ──────────────────

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        ns = self.primary.create_namespace(namespace_id)
        self._mirror("create_namespace", lambda: self.shadow.create_namespace(ns.id))
        return ns

    def delete_namespace(self, namespace_id: str) -> None:
        self.primary.delete_namespace(namespace_id)
        self._mirror("delete_namespace", lambda: self.shadow.delete_namespace(namespace_id))

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        self.primary.delete_entity_by_id(namespace_id, entity_id)
        self._mirror(
            "delete_entity_by_id",
            lambda: self.shadow.delete_entity_by_id(namespace_id, entity_id),
        )

    def update_entities(
        self,
        namespace_id: str,
        entities: list[Entity],
        enable_conflict_resolution: bool = True,
    ) -> list[EntityUpdate]:
        result = self.primary.update_entities(namespace_id, entities, enable_conflict_resolution)
        self._mirror(
            "update_entities",
            lambda: self.shadow.update_entities(namespace_id, entities, enable_conflict_resolution),
        )
        return result

    def patch_entity(
        self,
        namespace_id: str,
        entity_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> None:
        self.primary.patch_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata)
        self._mirror(
            "patch_entity",
            lambda: self.shadow.patch_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata),
        )

    def update_entity_metadata(self, namespace_id: str, entity_id: str, metadata_patch: dict) -> RecordedEntity:
        result = self.primary.update_entity_metadata(namespace_id, entity_id, metadata_patch)
        self._mirror(
            "update_entity_metadata",
            lambda: self.shadow.update_entity_metadata(namespace_id, entity_id, metadata_patch),
        )
        return result

    # ── abstract method stubs (delegated to primary) ──────────────────────
    #
    # The base class declares these as abstract; we satisfy the contract by
    # forwarding to the primary. The dual-write wrapper sits *above* the
    # template-method machinery, so update_entities is overridden directly
    # rather than going through these hooks.

    def _validate_namespace(self, namespace_id: str) -> None:
        return self.primary._validate_namespace(namespace_id)

    def _add_entity(
        self,
        namespace_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> str:
        return self.primary._add_entity(namespace_id, entity_type, content_str, timestamp, metadata)

    def _update_entity(
        self,
        namespace_id: str,
        entity_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> None:
        return self.primary._update_entity(namespace_id, entity_id, entity_type, content_str, timestamp, metadata)

    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        return self.primary._delete_entity(namespace_id, entity_id)

    # ── helpers ───────────────────────────────────────────────────────────

    def _mirror(self, op: str, fn: Any) -> None:
        """Run `fn()` on the shadow backend; swallow + log on failure."""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.pending_shadow_writes += 1
            logger.warning(
                "shadow.%s failed (pending_shadow_writes=%d): %s",
                op,
                self.pending_shadow_writes,
                exc,
            )
