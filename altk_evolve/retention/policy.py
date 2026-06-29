"""Retention policy schema (issue #275).

A policy is a list of rules. Each rule selects entities by type and age, and
either *flags* them (writes a ``retention_flagged_at`` metadata marker — the
issue's "flag for deletion after N days") or *deletes* them. A rule on
``trajectory`` entities may additionally cascade-delete the memories derived
from those sessions, found via provenance metadata.

Policies load from a YAML or JSON file::

    rules:
      - name: stale-guidelines
        entity_type: guideline
        max_age_days: 90
        action: flag
      - name: unused-guidelines
        entity_type: guideline
        max_unused_days: 30
        action: delete
      - name: old-sessions
        entity_type: trajectory
        max_age_days: 365
        action: delete
        cascade_derived: true
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RetentionAction = Literal["flag", "delete"]


class RetentionRule(BaseModel):
    """One retention rule. At least one of the age thresholds must be set."""

    name: str = Field(description="Human-readable rule name, surfaced in reports.")
    entity_type: str | None = Field(
        default=None, description="Only match this entity type (e.g. 'guideline', 'trajectory'). None = all types."
    )
    max_age_days: int | None = Field(default=None, description="Match entities whose created_at is older than this many days.")
    max_unused_days: int | None = Field(
        default=None,
        description="Match entities not accessed in this many days (uses metadata.last_accessed, falling back to created_at).",
    )
    action: RetentionAction = Field(default="flag", description="What to do with matched entities.")
    cascade_derived: bool = Field(
        default=False,
        description="On delete of a trajectory entity, also delete entities derived from it (via provenance metadata).",
    )

    @model_validator(mode="after")
    def _require_a_threshold(self) -> "RetentionRule":
        if self.max_age_days is None and self.max_unused_days is None:
            raise ValueError(f"retention rule {self.name!r} must set max_age_days and/or max_unused_days")
        return self


class RetentionPolicy(BaseModel):
    rules: list[RetentionRule] = Field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict) -> "RetentionPolicy":
        return cls.model_validate(data or {})

    @classmethod
    def from_file(cls, path: str | Path) -> "RetentionPolicy":
        """Load a policy from a YAML or JSON file.

        YAML is used when PyYAML is importable; otherwise the file is parsed as
        JSON (a strict subset of YAML), so simple policies load either way.
        """
        text = Path(path).read_text(encoding="utf-8")
        data: dict
        try:
            import yaml  # type: ignore[import-untyped]  # optional; PyYAML

            data = yaml.safe_load(text) or {}
        except ImportError:
            data = json.loads(text)
        return cls.from_mapping(data)
