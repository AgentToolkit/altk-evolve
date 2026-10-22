"""Integrations, observability filters and pipelines, and the worker that runs pipelines in the background.

Traces are not stored here: they are read from the source when needed. A pipeline stores only what it produced
(see state, trajectories, converters.store and log); the worker polls, converts and, in production, sends (worker,
sending); the API changes pipelines through manage, integrations and filters."""

from .filters import delete_filter, get_filter, list_filters, preview, save_filter
from .integrations import browse_folder, delete_integration, list_integrations, save_integration, seed_from_env, test_integration
from .kinds import FILTER_KINDS, KINDS, MEMORY_CHOICES, MEMORY_FIELDS, MODES
from .log import read_log
from .runtime import RUNTIME
from .manage import delete_pipeline, list_pipelines, pipeline_view, promote, purge, save_pipeline, set_enabled
from .migrations import migrate_experiments, migrate_memory_settings
from .sending import memory_guidelines, retry_send, send_all, send_all_progress, send_progress, send_trace, stop_send_all
from .state import get_pipeline, reset_watermarks, state_of
from .trajectories import slices_of, trajectory
from .worker import WORKER, retry_failed


def startup():
    """What the server and the worker do before they run: the first Langfuse integration from .env, and the data of
    earlier Forge versions moved to the current layout. Returns the ids of experiments migrated to pipelines."""
    seed_from_env()
    migrate_memory_settings()
    return migrate_experiments()


__all__ = [
    "RUNTIME",
    "FILTER_KINDS",
    "KINDS",
    "MEMORY_CHOICES",
    "MEMORY_FIELDS",
    "MODES",
    "WORKER",
    "browse_folder",
    "delete_filter",
    "delete_integration",
    "delete_pipeline",
    "get_filter",
    "get_pipeline",
    "list_filters",
    "list_integrations",
    "list_pipelines",
    "memory_guidelines",
    "migrate_experiments",
    "migrate_memory_settings",
    "pipeline_view",
    "preview",
    "promote",
    "purge",
    "read_log",
    "reset_watermarks",
    "retry_failed",
    "retry_send",
    "save_filter",
    "save_integration",
    "save_pipeline",
    "seed_from_env",
    "send_all",
    "send_all_progress",
    "send_progress",
    "send_trace",
    "set_enabled",
    "slices_of",
    "startup",
    "state_of",
    "stop_send_all",
    "test_integration",
    "trajectory",
]
