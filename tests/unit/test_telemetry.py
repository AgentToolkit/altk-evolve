"""Telemetry must preserve behavior and avoid exporting memory contents."""

import asyncio
import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from altk_evolve import telemetry

pytestmark = pytest.mark.unit


@pytest.fixture
def telemetry_capture(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(telemetry, "_TRACER", provider.get_tracer("test"))
    monkeypatch.setattr(telemetry, "_DURATION", meter.get_meter("test").create_histogram("duration"))
    yield exporter, reader
    provider.shutdown()
    meter.shutdown()


def test_nested_spans_metrics_and_no_payloads(telemetry_capture):
    exporter, reader = telemetry_capture

    @telemetry.traced("evolve.memory.search")
    def search(namespace_id, query):
        with telemetry.operation("evolve.db.search"):
            return [{"content": "secret-memory"}]

    with telemetry.operation("request"):
        result = search("service-one", "secret-query")
    spans = exporter.get_finished_spans()
    by_name = {s.name: s for s in spans}
    assert result == [{"content": "secret-memory"}]
    assert by_name["evolve.memory.search"].parent.span_id == by_name["request"].context.span_id
    assert by_name["evolve.db.search"].parent.span_id == by_name["evolve.memory.search"].context.span_id
    assert by_name["evolve.memory.search"].attributes["evolve.namespace.id"] == "service-one"
    assert "secret" not in str([dict(s.attributes) for s in spans])
    data = reader.get_metrics_data()
    labels = [
        dict(point.attributes)
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for m in sm.metrics
        for point in m.data.data_points
    ]
    assert all(set(label) == {"evolve.operation", "evolve.outcome"} for label in labels)


@pytest.mark.parametrize("exception", [ValueError("secret-payload"), asyncio.CancelledError("secret-payload")])
def test_errors_preserved_without_messages(telemetry_capture, exception):
    exporter, _ = telemetry_capture
    with pytest.raises(type(exception)) as caught:
        with telemetry.operation("evolve.test"):
            raise exception
    assert caught.value is exception
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code == trace.StatusCode.ERROR
    assert span.status.description is None
    assert not span.events
    assert "secret" not in str(span.attributes)


def test_returned_error_is_not_success(telemetry_capture):
    exporter, _ = telemetry_capture

    @telemetry.traced("evolve.test")
    def run():
        return json.dumps({"error": "secret", "deleted": 3})

    run()
    span = exporter.get_finished_spans()[0]
    assert span.attributes["evolve.outcome"] == "failed"
    assert span.attributes["evolve.result.deleted"] == 3
    assert span.status.status_code == trace.StatusCode.ERROR
    assert "secret" not in str(span.attributes)


def test_disabled_service_does_not_install_provider(monkeypatch):
    monkeypatch.setenv("EVOLVE_OTEL_ENABLED", "false")
    before = trace.get_tracer_provider()
    assert telemetry.configure_service_telemetry() is None
    assert trace.get_tracer_provider() is before


def test_host_provider_is_not_replaced(monkeypatch):
    provider = TracerProvider()
    monkeypatch.setenv("EVOLVE_OTEL_ENABLED", "true")
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: provider)
    monkeypatch.setattr(telemetry, "_OWNED", None)
    assert telemetry.configure_service_telemetry() is None
    assert trace.get_tracer_provider() is provider
    provider.shutdown()


@pytest.mark.asyncio
async def test_mcp_propagates_host_trace_context(telemetry_capture, monkeypatch):
    from types import SimpleNamespace
    from fastmcp import Client
    import fastmcp.telemetry as fastmcp_telemetry
    from altk_evolve.frontend.mcp import mcp_server

    exporter, _ = telemetry_capture
    monkeypatch.setattr(fastmcp_telemetry, "otel_get_tracer", lambda *args, **kwargs: telemetry._TRACER)
    monkeypatch.setattr(mcp_server, "get_client", lambda: SimpleNamespace(namespace_exists=lambda ns: False))
    monkeypatch.setenv("EVOLVE_OTEL_ENABLED", "false")
    with telemetry.operation("application.request") as parent:
        async with Client(mcp_server.mcp) as client:
            await client.call_tool("retrieve_user_facts", {"namespace_id": "service", "user_id": "secret-user"})
    spans = exporter.get_finished_spans()
    memory = next(s for s in spans if s.name == "evolve.mcp.retrieve_user_facts")
    assert memory.context.trace_id == parent.get_span_context().trace_id
    assert memory.parent is not None
    assert "secret-user" not in str(memory.attributes)


def test_service_exporter_defaults_on_idempotent_and_outage_safe(tmp_path):
    """Separate process keeps the global provider lifecycle realistic."""
    import os
    import subprocess
    import sys

    code = """
import time
from altk_evolve.telemetry import configure_service_telemetry, operation
from opentelemetry import metrics
provider = configure_service_telemetry()
assert provider is not None
assert configure_service_telemetry() is provider
started = time.perf_counter()
with operation("evolve.outage.test"):
    pass
assert time.perf_counter() - started < 1
provider.force_flush(timeout_millis=500)
provider.shutdown()
metrics.get_meter_provider().shutdown(timeout_millis=500)
"""
    env = {
        **os.environ,
        "OTEL_SDK_DISABLED": "false",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:1",
        "OTEL_EXPORTER_OTLP_TIMEOUT": "0.1",
    }
    env.pop("EVOLVE_OTEL_ENABLED", None)
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_retention_outcome_counts_do_not_expose_entities(telemetry_capture):
    exporter, _ = telemetry_capture
    with telemetry.operation("evolve.retention.sweep") as span:
        telemetry.report_result({"items": [{"outcome": "held", "content": "secret"}, {"outcome": "deleted"}]}, span)
    attributes = exporter.get_finished_spans()[0].attributes
    assert attributes["evolve.result.held"] == 1
    assert attributes["evolve.result.deleted"] == 1
    assert "secret" not in str(attributes)


def test_concurrent_service_namespaces_do_not_mix(telemetry_capture):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    exporter, _ = telemetry_capture
    barrier = Barrier(2)

    def request(namespace):
        with telemetry.operation("application.request") as parent:
            barrier.wait(timeout=5)
            with telemetry.operation("evolve.memory.search", namespace_id=namespace):
                pass
            return namespace, parent.get_span_context().trace_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        owners = dict(pool.map(request, ["service-one", "service-two"]))
    spans = [s for s in exporter.get_finished_spans() if s.name == "evolve.memory.search"]
    assert len(spans) == 2
    for span in spans:
        assert span.context.trace_id == owners[span.attributes["evolve.namespace.id"]]
    assert len(set(owners.values())) == 2


def test_missing_optional_exporter_does_not_prevent_startup(monkeypatch, caplog):
    import builtins

    original_import = builtins.__import__

    def without_sdk(name, *args, **kwargs):
        if name.startswith("opentelemetry.sdk"):
            raise ImportError("optional exporter not installed")
        return original_import(name, *args, **kwargs)

    monkeypatch.delenv("EVOLVE_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.setattr(telemetry, "_OWNED", None)
    monkeypatch.setattr(trace, "get_tracer_provider", trace.ProxyTracerProvider)
    monkeypatch.setattr(builtins, "__import__", without_sdk)
    assert telemetry.configure_service_telemetry() is None
    assert "install altk-evolve[observability]" in caplog.text


@pytest.mark.asyncio
async def test_service_lifespan_does_not_wait_for_export(monkeypatch):
    from unittest.mock import Mock
    from altk_evolve.frontend.mcp import mcp_server

    provider = Mock()
    monkeypatch.setattr(mcp_server, "configure_service_telemetry", lambda: provider)
    async with mcp_server.telemetry_lifespan(None):
        pass
    provider.force_flush.assert_not_called()
    provider.shutdown.assert_not_called()


def test_slow_collector_does_not_block_memory_operations():
    import os
    import subprocess
    import sys

    code = """
import os, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from altk_evolve.telemetry import configure_service_telemetry, operation
entered, release = threading.Event(), threading.Event()
class Collector(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        entered.set()
        release.wait(5)
server = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] = f'http://127.0.0.1:{server.server_port}'
provider = configure_service_telemetry()
with operation('evolve.memory.search'): pass
flusher = threading.Thread(target=provider.force_flush)
flusher.start()
assert entered.wait(5), 'no export reached the collector'
started = time.perf_counter()
for _ in range(100):
    with operation('evolve.memory.search'): pass
assert time.perf_counter() - started < 1, 'memory work waited on collector'
flusher.join(3)
assert not flusher.is_alive(), 'default exporter timeout was not bounded'
release.set()
provider.shutdown()
server.shutdown()
"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "EVOLVE_OTEL_"))}
    env["OTEL_METRICS_EXPORTER"] = "none"
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
