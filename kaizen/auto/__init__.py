"""
Kaizen Auto-Instrumentation Module

Auto-patches LLM frameworks (OpenAI, LiteLLM, Smolagents, etc.) to trace calls to Phoenix.

Usage:
    # Set environment variable
    export KAIZEN_AUTO_ENABLED=true

    # Then in code, just import:
    import kaizen.auto # noqa: F401

    # All LLM calls are now traced!

Advanced Usage (Explicit):
    from kaizen.auto import enable_tracing
    enable_tracing(project="my-agent", force=True)
"""

from __future__ import annotations

import atexit
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

logger = logging.getLogger("kaizen.auto")

# Global state to track instrumentation
_tracer_provider: TracerProvider | None = None
_instrumented_frameworks: set[str] = set()
_flush_registered: bool = False


# --- Tracing Logic ---


def is_already_instrumented() -> bool:
    """Check if tracing has already been set up (by Kaizen or another tool)."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        # Default provider is ProxyTracerProvider (no-op)
        default_name = "ProxyTracerProvider"
        return type(provider).__name__ != default_name
    except ImportError:
        return False


def detect_installed_frameworks() -> list[str]:
    """Detect which LLM frameworks are installed."""
    frameworks = []

    framework_modules = {
        "openai": "openai",
        "litellm": "litellm",
        "smolagents": "smolagents",
        "openai_agents": "agents",  # openai-agents package
    }

    for name, module in framework_modules.items():
        try:
            __import__(module)
            frameworks.append(name)
        except ImportError:
            pass

    return frameworks


def _get_instrumentor(framework: str):
    """Get the OpenInference instrumentor for a framework."""
    instrumentor_map = {
        "openai": ("openinference.instrumentation.openai", "OpenAIInstrumentor"),
        "litellm": ("openinference.instrumentation.litellm", "LiteLLMInstrumentor"),
        "smolagents": ("openinference.instrumentation.smolagents", "SmolagentsInstrumentor"),
        "openai_agents": ("openinference.instrumentation.openai_agents", "OpenAIAgentsInstrumentor"),
    }

    if framework not in instrumentor_map:
        return None

    module_name, class_name = instrumentor_map[framework]

    try:
        module = __import__(module_name, fromlist=[class_name])
        instrumentor_class = getattr(module, class_name)
        return instrumentor_class()
    except ImportError as e:
        logger.debug(f"Instrumentor for {framework} not available: {e}")
        return None


def _register_flush_handler() -> None:
    """Register an atexit handler to flush traces."""
    global _flush_registered

    if _flush_registered:
        return

    def _flush():
        try:
            # Use global getter to avoid stale closure
            provider = get_tracer_provider()
            if provider:
                provider.force_flush()
        except Exception:  # noqa: BLE001
            logger.debug("Error flushing traces", exc_info=True)

    atexit.register(_flush)
    _flush_registered = True


def enable_tracing(
    project: str | None = None,
    endpoint: str | None = None,
    frameworks: list[str] | None = None,
    force: bool = False,
    auto_flush: bool = True,
) -> TracerProvider | None:
    """
    Enable Phoenix tracing for detected LLM frameworks.

    Args:
        project: Phoenix project name (default: from env or "kaizen-agent")
        endpoint: Phoenix collector endpoint (default: from env or "http://localhost:6006/v1/traces")
        frameworks: List of frameworks to instrument. If None, auto-detect.
        force: If True, instrument even if tracing is already set up.
        auto_flush: If True, register atexit handler to flush traces.

    Returns:
        TracerProvider instance if tracing was enabled, None otherwise.
    """
    global _tracer_provider, _instrumented_frameworks

    # Check if already instrumented
    if not force and is_already_instrumented():
        logger.info("Tracing already set up, skipping (use force=True to override)")
        return None

    # Get configuration from environment or defaults
    project = project or os.environ.get("KAIZEN_TRACING_PROJECT", os.environ.get("PHOENIX_PROJECT_NAME", "kaizen-agent"))
    endpoint = endpoint or os.environ.get("KAIZEN_TRACING_ENDPOINT", os.environ.get("PHOENIX_ENDPOINT", "http://localhost:6006/v1/traces"))

    # Initialize Phoenix tracer
    try:
        from phoenix.otel import register

        tracer_provider = register(
            project_name=project,
            endpoint=endpoint,
        )
        _tracer_provider = tracer_provider
        logger.info(f"Phoenix tracing enabled: project={project}, endpoint={endpoint}")
    except ImportError:
        logger.exception("Phoenix not available: Install with: pip install arize-phoenix")
        return None
    except Exception:  # noqa: BLE001
        logger.exception("Failed to initialize Phoenix")
        return None

    # Detect frameworks to instrument
    if frameworks is None:
        frameworks = detect_installed_frameworks()

    # Apply instrumentors
    instrumented = []
    for framework in frameworks:
        if framework in _instrumented_frameworks:
            continue

        instrumentor = _get_instrumentor(framework)
        if instrumentor:
            try:
                instrumentor.instrument(tracer_provider=tracer_provider)
                _instrumented_frameworks.add(framework)
                instrumented.append(framework)
            except Exception as e:
                logger.warning(f"Failed to instrument {framework}: {e}")

    if instrumented:
        logger.info(f"Instrumented frameworks: {instrumented}")
    else:
        logger.info("No frameworks instrumented (none found or instrumentors not installed)")

    # Register flush handler
    if auto_flush:
        _register_flush_handler()

    return tracer_provider


def get_tracer_provider() -> TracerProvider | None:
    """Get the current Kaizen tracer provider, if set up."""
    return _tracer_provider


def get_instrumented_frameworks() -> set[str]:
    """Get the set of frameworks that have been instrumented."""
    return _instrumented_frameworks.copy()


def flush_traces() -> None:
    """Manually flush all pending traces to Phoenix."""
    if _tracer_provider:
        _tracer_provider.force_flush()


# --- Auto Setup Logic ---


def _auto_setup() -> None:
    """Automatically set up tracing if KAIZEN_AUTO_ENABLED is true."""
    # Check if auto mode is enabled
    enabled = os.environ.get("KAIZEN_AUTO_ENABLED", "").lower() in ("true", "1", "yes")

    if not enabled:
        logger.debug("KAIZEN_AUTO_ENABLED not set, skipping auto-instrumentation")
        return

    # Check for existing instrumentation
    if is_already_instrumented():
        logger.info("[kaizen.auto] Existing tracer detected, skipping")
        print("[kaizen.auto] Existing tracer detected, skipping")
        return

    # Enable tracing
    tracer = enable_tracing()

    if tracer:
        frameworks = get_instrumented_frameworks()
        if frameworks:
            print(f"[kaizen.auto] Instrumented: {list(frameworks)}")
        else:
            print("[kaizen.auto] Phoenix tracer enabled (no frameworks to instrument)")


# Auto-run on import
_auto_setup()
