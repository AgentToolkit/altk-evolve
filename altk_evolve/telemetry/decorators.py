"""Telemetry decorator for retrieval hot-path methods (Phase 2).

Usage::

    from altk_evolve.telemetry.decorators import with_retrieval_telemetry

    @with_retrieval_telemetry(event_name="search_entities")
    def search_entities(self, namespace_id: str, query: str | None = None, ...):
        ...

The decorator:
- Times the wrapped call with `time.perf_counter()`.
- Emits a JSONL event via the process-wide `RetrievalLog` singleton.
- Increments `DurableMetrics.inc_retrieval_total()` and observes latency.
- Never raises into the retrieval hot path (telemetry failures are swallowed).
- On wrapped-function exception: logs an error event and re-raises.
- Extracts `namespace_id`, `query`, `filters`, and result IDs from call args
  using `inspect.signature` so positional and keyword styles both work.
"""

from __future__ import annotations

import datetime
import functools
import inspect
import logging
import time
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def with_retrieval_telemetry(*, event_name: str) -> Callable[[F], F]:
    """Decorator factory that wraps a retrieval method with telemetry.

    Args:
        event_name: Base name for the emitted JSONL event (e.g. "search_entities").
    """

    def decorator(fn: F) -> F:
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            from altk_evolve.telemetry import get_durable_metrics, get_retrieval_log

            start = time.perf_counter()
            elapsed: float = 0.0
            error_exc: BaseException | None = None
            result: Any = None

            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001
                error_exc = exc
            finally:
                elapsed = time.perf_counter() - start

            # Always increment metrics — even on exception.
            try:
                metrics = get_durable_metrics()
                metrics.inc_retrieval_total()
                metrics.observe_retrieval_latency(elapsed)
            except Exception as telemetry_exc:  # noqa: BLE001
                logger.warning("DurableMetrics update suppressed: %s", telemetry_exc)

            # Build and emit the retrieval log event.
            try:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                call_args = bound.arguments

                if error_exc is not None:
                    event: dict[str, Any] = {
                        "event": f"{event_name}:error",
                        "namespace_id": call_args.get("namespace_id"),
                        "error_type": type(error_exc).__name__,
                        "latency_ms": round(elapsed * 1000, 3),
                        "timestamp": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                    }
                else:
                    result_ids: list[str] = []
                    if result is not None:
                        try:
                            result_ids = [str(r.id) for r in result if hasattr(r, "id")]
                        except Exception:  # noqa: BLE001
                            result_ids = []

                    event = {
                        "event": event_name,
                        "namespace_id": call_args.get("namespace_id"),
                        "result_ids": result_ids,
                        "result_count": len(result_ids),
                        "latency_ms": round(elapsed * 1000, 3),
                        "timestamp": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                    }
                    # Include optional query / filters when present in the call.
                    if "query" in call_args:
                        event["query"] = call_args["query"]
                    if "filters" in call_args:
                        event["filters"] = call_args["filters"]

                get_retrieval_log().log_retrieval(event)
            except Exception as telemetry_exc:  # noqa: BLE001
                logger.warning("RetrievalLog emit suppressed: %s", telemetry_exc)

            if error_exc is not None:
                raise error_exc

            return result

        return wrapper  # type: ignore[return-value]

    return decorator
