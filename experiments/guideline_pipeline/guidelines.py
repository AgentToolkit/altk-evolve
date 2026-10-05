"""A namespace's guidelines as ordered rows: the shared input of the export and lineage stages."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from altk_evolve.frontend.client.evolve_client import EvolveClient

# The library's support definition (dosage-aware retrieval): metadata["support"],
# 1 when absent or unusable. Imported rather than redefined so core / min-support
# here mean exactly what they mean to EvolveClient.select_guidelines.
from altk_evolve.llm.guidelines.retrieval import _support as library_support
from altk_evolve.schema.core import RecordedEntity

FETCH_LIMIT = 100_000


@dataclass(frozen=True)
class GuidelineRow:
    """One stored guideline with its rule text and support resolved."""

    entity: RecordedEntity
    rule: str
    support: int

    @property
    def metadata(self) -> dict[str, Any]:
        return self.entity.metadata or {}


def fetch_guidelines(client: EvolveClient, namespace_id: str, *, limit: int = FETCH_LIMIT) -> list[RecordedEntity]:
    """Every guideline in the namespace, read through the public seam (read hooks apply).

    Raises NamespaceNotFoundException for a missing namespace, and ValueError rather
    than exporting a silently truncated set when the namespace reaches limit.
    """
    client.get_namespace_details(namespace_id)
    entities = client.get_all_entities(namespace_id, filters={"type": "guideline"}, limit=limit)
    if len(entities) >= limit:
        raise ValueError(f"namespace {namespace_id!r} has at least {limit} guidelines; refusing to export a truncated set")
    return entities


def rule_text(entity: RecordedEntity) -> str:
    content = entity.content if isinstance(entity.content, str) else json.dumps(entity.content, ensure_ascii=False)
    return content.strip()


def _by_support(row: GuidelineRow) -> tuple:
    # Support descending; the rest only makes the order total and reproducible.
    return (-row.support, row.rule.casefold(), row.rule, str(row.metadata.get("evidence")), row.entity.id)


# The one place ordering is defined. Add a key here (for example by evidence) to
# offer another order; every output built from rows follows it.
ORDERS: dict[str, Callable[[GuidelineRow], tuple]] = {"support": _by_support}


def guideline_rows(entities: Iterable[RecordedEntity], *, order: str = "support") -> tuple[list[GuidelineRow], int]:
    """Rows for every guideline with non-empty text, ordered; also returns how many were empty."""
    rows: list[GuidelineRow] = []
    empty = 0
    for entity in entities:
        rule = rule_text(entity)
        if not rule:
            empty += 1
            continue
        rows.append(GuidelineRow(entity=entity, rule=rule, support=library_support(entity)))
    return sorted(rows, key=ORDERS[order]), empty
