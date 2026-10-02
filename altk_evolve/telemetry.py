"""Content-free instrumentation; exporting is opt-in at the service boundary.

Libraries only use the API and inherit their host's provider. No prompts,
memories, SQL parameters, exception messages, or user IDs are exported here.
"""

from contextlib import contextmanager
from functools import wraps
from inspect import signature
from time import perf_counter
import asyncio
from enum import Enum
from typing import Callable, ParamSpec, TypeVar
import os
import json
from contextvars import ContextVar
import threading

from opentelemetry import metrics, trace
from opentelemetry.trace import Status, StatusCode

P = ParamSpec("P")
R = TypeVar("R")

_TRACER = trace.get_tracer("altk_evolve")
_METER = metrics.get_meter("altk_evolve")
_DURATION = _METER.create_histogram("evolve.operation.duration", unit="s")
_RESULTS = _METER.create_counter("evolve.operation.results", unit="{item}")
_LOCK = threading.Lock()
_OWNED = None
_OUTCOME = ContextVar("evolve_telemetry_outcome", default="completed")


@contextmanager
def operation(name, *, namespace_id=None, kind=trace.SpanKind.INTERNAL):
    """Measure a fixed operation name without collecting arbitrary arguments."""
    started = perf_counter()
    token = _OUTCOME.set("completed")
    with _TRACER.start_as_current_span(name, kind=kind, record_exception=False, set_status_on_exception=False) as span:
        if namespace_id:
            span.set_attribute("evolve.namespace.id", str(namespace_id))
        try:
            yield span
        except BaseException as exc:
            _OUTCOME.set("cancelled" if isinstance(exc, asyncio.CancelledError) else "failed")
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            # Namespace/user/entity IDs must never become metric dimensions.
            outcome = _OUTCOME.get()
            _OUTCOME.reset(token)
            span.set_attribute("evolve.outcome", outcome)
            _DURATION.record(perf_counter() - started, {"evolve.operation": name, "evolve.outcome": outcome})


def traced(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Instrument synchronous domain boundaries, retaining their public signature."""

    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        sig = signature(function)

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            bound = sig.bind(*args, **kwargs).arguments
            namespace = bound.get("namespace_id", bound.get("namespace"))
            if namespace is None and "self" in bound:
                namespace = getattr(bound["self"], "namespace_id", getattr(bound["self"], "namespace", None))
            with operation(name, namespace_id=namespace) as span:
                hook_type = bound.get("hook_type")
                if isinstance(hook_type, Enum):
                    span.set_attribute("evolve.hook.type", hook_type.value)
                result = function(*args, **kwargs)
                if isinstance(result, (list, tuple)):
                    span.set_attribute("evolve.result.count", len(result))
                report_result(result, span, operation_name=name)
                return result

        return wrapped

    return decorate


def report_result(result, span, *, operation_name="evolve.operation"):
    """Allowlisted counts/outcomes only; never serialize a returned payload."""
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            return
    if not isinstance(result, dict):
        return
    if result.get("error") or result.get("errors"):
        _OUTCOME.set("failed")
        span.set_status(Status(StatusCode.ERROR))
    elif result.get("cancelled"):
        _OUTCOME.set("cancelled")
    for key in ("matched_count", "stored_count", "marked", "deleted", "held", "skipped", "flagged"):
        value = result.get(key)
        if isinstance(value, list):
            value = len(value)
        if type(value) is int:
            span.set_attribute("evolve.result." + key, value)
            if value >= 0:
                _RESULTS.add(value, {"evolve.operation": operation_name, "evolve.result": key})
    items = result.get("items")
    if isinstance(items, list):
        for outcome in ("deleted", "held", "skipped", "flagged", "marked"):
            count = sum(isinstance(item, dict) and item.get("outcome") == outcome for item in items)
            if count:
                span.set_attribute("evolve.result." + outcome, count)
                _RESULTS.add(count, {"evolve.operation": operation_name, "evolve.result": outcome})


def configure_service_telemetry():
    """Opt-in HTTP/protobuf OTLP exporter. Never replace a host-owned provider.

    Uses standard OTEL exporter, resource, sampling, and batching environment
    variables. Call at service startup, not when constructing a library client.
    Returns the owned provider, or None when disabled/host-configured.
    """
    global _OWNED
    if os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return None
    if os.getenv("EVOLVE_OTEL_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return None
    with _LOCK:
        if _OWNED is not None:
            return _OWNED
        if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
            return None
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider = TracerProvider(resource=Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", "evolve")}))
        if os.getenv("OTEL_TRACES_EXPORTER", "otlp") != "none":
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)
        # Respect a host-configured meter provider independently of tracing.
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.metrics._internal import _ProxyMeterProvider

        meter = None
        if os.getenv("OTEL_METRICS_EXPORTER", "otlp") != "none" and isinstance(metrics.get_meter_provider(), _ProxyMeterProvider):
            meter = MeterProvider(
                resource=provider.resource,
                metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
            )
            metrics.set_meter_provider(meter)
        _OWNED = provider
        return provider


def model_completion(*args, **kwargs):
    """Time a model request without capturing messages or response content."""
    from litellm import completion

    with operation("evolve.llm.completion") as span:
        response = completion(*args, **kwargs)
        usage = getattr(response, "usage", None)
        for field in ("prompt_tokens", "completion_tokens"):
            value = getattr(usage, field, None)
            if type(value) is int:
                span.set_attribute("evolve.llm." + field, value)
        return response
