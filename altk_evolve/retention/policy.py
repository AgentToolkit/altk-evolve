"""Retention policy schema (issue #275).

A policy is an ordered list of rules. Each rule selects entities by type and by
an age threshold, and either *flags* them (writes ``retention_flagged_at`` and
friends into ``metadata`` — a non-destructive marker for review) or *deletes*
them. A rule matching ``trajectory`` entities may additionally cascade-delete
the memories derived from those sessions, found via provenance metadata.

Rules are evaluated top-to-bottom and the first match wins per entity, so put
the narrow rules first.

Policies load from a YAML or JSON file::

    rules:
      - name: stale-guidelines
        entity_type: guideline
        max_age_days: 90
        action: flag
      - name: unused-guidelines
        entity_type: guideline
        max_unused_days: 180
        action: delete
      - name: old-sessions
        entity_type: trajectory
        max_age_days: 365
        action: delete
        cascade_derived: true
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

RetentionAction = Literal["flag", "delete"]


class RetentionRule(BaseModel):
    """One retention rule. At least one of the age thresholds must be set."""

    name: str = Field(description="Human-readable rule name, surfaced in reports.")
    entity_type: str | None = Field(
        default=None,
        description="Only match this entity type (e.g. 'guideline', 'trajectory'). None matches every type.",
    )
    max_age_days: int | None = Field(
        default=None,
        description="Match entities whose created_at is older than this many days.",
    )
    max_unused_days: int | None = Field(
        default=None,
        description=(
            "Match entities not read in this many days. Uses metadata.last_accessed, which "
            "AccessStampPlugin (or EvolveClient.record_access) stamps; entities that carry no "
            "such stamp fall back to created_at and are reported as such."
        ),
    )
    action: RetentionAction = Field(default="flag", description="What to do with matched entities.")
    cascade_derived: bool = Field(
        default=False,
        description="On delete of a session entity, also delete the entities derived from it (via provenance metadata).",
    )

    @model_validator(mode="after")
    def _require_a_threshold(self) -> RetentionRule:
        if self.max_age_days is None and self.max_unused_days is None:
            raise ValueError(f"retention rule {self.name!r} must set max_age_days and/or max_unused_days")
        return self


class RetentionPolicy(BaseModel):
    """An ordered list of retention rules."""

    rules: list[RetentionRule] = Field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict | None) -> RetentionPolicy:
        return cls.model_validate(data or {})

    @classmethod
    def from_file(cls, path: str | Path) -> RetentionPolicy:
        """Load a policy from a YAML or JSON file (JSON is a subset of YAML)."""
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if data is not None and not isinstance(data, dict):
            raise ValueError(f"retention policy {path} must hold a mapping with a 'rules' list")
        return cls.from_mapping(data)
