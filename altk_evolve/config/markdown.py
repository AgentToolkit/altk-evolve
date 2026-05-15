"""Settings for MarkdownEntityBackend (Phase 0 spike — Phase 1 implements)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MarkdownSettings(BaseSettings):
    """Configuration for the Markdown-tree entity backend.

    On-disk layout and frontmatter contract are documented in
    `design_doc/markdown_schema.md`. The backend implementation lives in
    `altk_evolve/backend/markdown.py`.
    """

    model_config = SettingsConfigDict(env_prefix="EVOLVE_", env_file=".env", extra="ignore")

    data_dir: str = Field(
        default="evolve_memory",
        description="Root directory for the Markdown tree (guidelines/, facts/, .indexes/, etc.).",
    )
    lock_timeout_seconds: float = Field(
        default=10.0,
        description="Timeout for cross-process file lock acquisition per namespace.",
    )
    enable_git_commit: bool = Field(
        default=False,
        description="When True, the backend invokes git to record each write. Deferred to a later phase.",
    )
    evolve_bot_author: str = Field(
        default="evolve-bot <bot@evolve.local>",
        description="Author string used for git commits when enable_git_commit is True.",
    )
    drift_check_max_age_seconds: int = Field(
        default=3600,
        description=(
            "Maximum staleness allowed for the legacy drift-check before legacy_rollback_safe "
            "becomes False during the bake-in window. Round-3 review §3."
        ),
    )
    legacy_staleness_max_seconds: int = Field(
        default=3600,
        description=("Maximum age of legacy_rebuild_completed_at marker for post-bake-in legacy rollback to be safe. Round-3 review §3."),
    )


markdown_settings = MarkdownSettings()
