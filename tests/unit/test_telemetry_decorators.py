"""Unit tests for altk_evolve.telemetry.decorators (Phase 2)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from altk_evolve.telemetry.decorators import with_retrieval_telemetry
from altk_evolve.telemetry.durable_metrics import DurableMetrics
from altk_evolve.telemetry.retrieval_log import RetrievalLog

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_log(tmp_path) -> RetrievalLog:
    return RetrievalLog(log_dir=str(tmp_path / "telemetry"))


def _make_metrics() -> DurableMetrics:
    return DurableMetrics(registry=CollectorRegistry(), namespace="test")


def _patch_singletons(log: RetrievalLog, metrics: DurableMetrics):
    """Context manager that injects test-local singletons into the decorator.

    The decorator does `from altk_evolve.telemetry import get_retrieval_log, get_durable_metrics`
    inside the wrapper at call time, so we patch the names on the telemetry package itself.
    """
    return patch.multiple(
        "altk_evolve.telemetry",
        get_retrieval_log=lambda: log,
        get_durable_metrics=lambda: metrics,
    )


# ---------------------------------------------------------------------------
# Test 1: decorated function returns original result
# ---------------------------------------------------------------------------


def test_decorator_returns_original_result(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="test_event")
        def my_fn(namespace_id: str, query: str | None = None):
            return [object()]  # any non-empty list

        result = my_fn("ns-1", query="hello")

    assert len(result) == 1


# ---------------------------------------------------------------------------
# Test 2: RetrievalLog.log_retrieval is called once per invocation
# ---------------------------------------------------------------------------


def test_log_retrieval_called_once(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()
    log.log_retrieval = MagicMock()

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def search(namespace_id: str, query: str | None = None, filters: dict | None = None):
            return []

        search("ns-abc", query="test-query", filters={"type": "guideline"})

    log.log_retrieval.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: emitted event has correct shape
# ---------------------------------------------------------------------------


def test_event_shape(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()
    captured: list[dict] = []
    log.log_retrieval = lambda e: captured.append(e)

    class FakeEntity:
        id = "entity-1"

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def search(namespace_id: str, query: str | None = None, filters: dict | None = None):
            return [FakeEntity()]

        search("ns-xyz", query="my-query", filters={"type": "guideline"})

    assert len(captured) == 1
    event = captured[0]
    assert event["event"] == "search_entities"
    assert event["namespace_id"] == "ns-xyz"
    assert event["query"] == "my-query"
    assert event["filters"] == {"type": "guideline"}
    assert event["result_ids"] == ["entity-1"]
    assert event["result_count"] == 1
    assert "latency_ms" in event
    assert "timestamp" in event


# ---------------------------------------------------------------------------
# Test 4: DurableMetrics counters are incremented
# ---------------------------------------------------------------------------


def test_durable_metrics_incremented(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="get_entity_by_id")
        def get_entity(namespace_id: str, entity_id: str):
            return []

        get_entity("ns-1", "eid-1")
        get_entity("ns-1", "eid-2")

    assert metrics.retrieval_total() == 2


# ---------------------------------------------------------------------------
# Test 5: observe_retrieval_latency is called (latency > 0)
# ---------------------------------------------------------------------------


def test_latency_observed(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()
    metrics.observe_retrieval_latency = MagicMock()

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="get_all_entities")
        def get_all(namespace_id: str):
            return []

        get_all("ns-1")

    metrics.observe_retrieval_latency.assert_called_once()
    latency_arg = metrics.observe_retrieval_latency.call_args[0][0]
    assert latency_arg >= 0.0


# ---------------------------------------------------------------------------
# Test 6: telemetry exception inside decorator is swallowed, function returns
# ---------------------------------------------------------------------------


def test_telemetry_exception_swallowed(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()

    def boom(_event):
        raise RuntimeError("log is broken")

    log.log_retrieval = boom

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def search(namespace_id: str):
            return ["result"]

        # Must not raise even though log_retrieval raises.
        result = search("ns-1")

    assert result == ["result"]


# ---------------------------------------------------------------------------
# Test 7: wrapped function exception still propagates
# ---------------------------------------------------------------------------


def test_wrapped_exception_propagates(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def broken(namespace_id: str):
            raise ValueError("backend down")

        with pytest.raises(ValueError, match="backend down"):
            broken("ns-1")


# ---------------------------------------------------------------------------
# Test 8: error event emitted on wrapped-function exception
# ---------------------------------------------------------------------------


def test_error_event_emitted_on_exception(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()
    captured: list[dict] = []
    log.log_retrieval = lambda e: captured.append(e)

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def broken(namespace_id: str):
            raise RuntimeError("oops")

        with pytest.raises(RuntimeError):
            broken("ns-error")

    assert len(captured) == 1
    event = captured[0]
    assert event["event"] == "search_entities:error"
    assert event["error_type"] == "RuntimeError"
    assert event["namespace_id"] == "ns-error"
    assert "latency_ms" in event


# ---------------------------------------------------------------------------
# Test 9: metrics incremented even when wrapped function raises
# ---------------------------------------------------------------------------


def test_metrics_incremented_on_exception(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def broken(namespace_id: str):
            raise KeyError("missing")

        with pytest.raises(KeyError):
            broken("ns-1")

    assert metrics.retrieval_total() == 1


# ---------------------------------------------------------------------------
# Test 10: positional call style works (inspect.signature.bind)
# ---------------------------------------------------------------------------


def test_positional_call_style(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()
    captured: list[dict] = []
    log.log_retrieval = lambda e: captured.append(e)

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def search(namespace_id: str, query: str | None = None):
            return []

        # All positional — no kwargs
        search("ns-positional", "my-query")

    assert captured[0]["namespace_id"] == "ns-positional"
    assert captured[0]["query"] == "my-query"


# ---------------------------------------------------------------------------
# Test 11: result_ids populated from entities with .id attribute
# ---------------------------------------------------------------------------


def test_result_ids_populated(tmp_path):
    log = _make_log(tmp_path)
    metrics = _make_metrics()
    captured: list[dict] = []
    log.log_retrieval = lambda e: captured.append(e)

    class Entity:
        def __init__(self, eid):
            self.id = eid

    with _patch_singletons(log, metrics):

        @with_retrieval_telemetry(event_name="search_entities")
        def search(namespace_id: str):
            return [Entity("a"), Entity("b"), Entity("c")]

        search("ns-1")

    event = captured[0]
    assert event["result_ids"] == ["a", "b", "c"]
    assert event["result_count"] == 3
