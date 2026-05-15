"""MarkdownEntityBackend (Phase 0 spike — Phase 1 implements).

This module is a typed skeleton confirming the abstract-method contract of
`BaseEntityBackend`. All methods raise `NotImplementedError` with a pointer
back to the implementation phase. Phase 1 will fill in the bodies, mirroring
the patterns used by `FilesystemEntityBackend` (atomic temp+replace, threading
lock + fcntl.flock, `_active_data` for in-update search consistency).

Design contract (locked in Phase 0):
- Filename = stable_id (ULID). Filename never embeds the trigger slug.
- YAML frontmatter carries all structured fields per design_doc/markdown_schema.md.
- Concurrency: per-namespace threading.Lock + fcntl.flock on a .lockfile.
- Atomic writes: temp-file + os.replace.
- `_active_data` pattern: pre-loaded namespace state visible to search during update_entities.
- Index recovery: per-entity `index_generation` plus per-namespace high-watermark manifest.
- Conflict resolution: invokes existing llm/conflict_resolution/conflict_resolution.py
  unchanged in Phase 1; per-track split (guideline_resolver) lands in Phase 2.

See:
- design_doc/transform_evolve.md (architecture)
- design_doc/implementation_plan.md §5 (Phase 1 deliverables)
- design_doc/markdown_schema.md (frontmatter contract)
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

from altk_evolve.backend.base import BaseEntityBackend
from altk_evolve.config.markdown import MarkdownSettings, markdown_settings
from altk_evolve.schema.core import Namespace, RecordedEntity


_NOT_IMPLEMENTED_MSG = (
    "MarkdownEntityBackend is a Phase 0 spike — implementation lands in Phase 1. See design_doc/implementation_plan.md §5."
)


class MarkdownEntityBackend(BaseEntityBackend):
    """Markdown-tree backend stub. Phase 1 fills in the bodies.

    Construction:
        MarkdownEntityBackend()                              # uses module-level markdown_settings
        MarkdownEntityBackend(MarkdownSettings(data_dir=...))  # custom config
    """

    settings: MarkdownSettings

    def __init__(self, config: BaseSettings | None = None) -> None:
        super().__init__(config)
        if config is None:
            self.settings = markdown_settings
        elif isinstance(config, MarkdownSettings):
            self.settings = config
        else:
            raise TypeError(f"MarkdownEntityBackend requires MarkdownSettings, got {type(config).__name__}.")

    # ── lifecycle ────────────────────────────────────────────────────────

    def ready(self) -> bool:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def details(self) -> dict:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # ── namespace ops ────────────────────────────────────────────────────

    def create_namespace(self, namespace_id: str | None = None) -> Namespace:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def get_namespace_details(self, namespace_id: str) -> Namespace:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def search_namespaces(self, limit: int = 10) -> list[Namespace]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def delete_namespace(self, namespace_id: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # ── entity ops ───────────────────────────────────────────────────────

    def search_entities(
        self,
        namespace_id: str,
        query: str | None = None,
        filters: dict | None = None,
        limit: int = 10,
    ) -> list[RecordedEntity]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def delete_entity_by_id(self, namespace_id: str, entity_id: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # ── update_entities template-method hooks ────────────────────────────

    def _validate_namespace(self, namespace_id: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def _add_entity(
        self,
        namespace_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> str:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def _update_entity(
        self,
        namespace_id: str,
        entity_id: str,
        entity_type: str,
        content_str: str,
        timestamp: int,
        metadata: dict,
    ) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def _delete_entity(self, namespace_id: str, entity_id: str) -> None:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    # `_post_update` and `patch_entity` use default implementations from
    # `BaseEntityBackend`; Phase 1 will override them for the `_active_data`
    # pattern + manifest write path.
