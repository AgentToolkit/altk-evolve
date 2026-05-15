"""Tests for altk_evolve.telemetry.durable_metrics (Phase 2).

Each test uses its own CollectorRegistry to keep counter names isolated
across tests and avoid metric-name collisions in the global registry.
"""

from __future__ import annotations

import threading

import pytest
from prometheus_client import CollectorRegistry

from altk_evolve.telemetry.durable_metrics import DurableMetrics


pytestmark = pytest.mark.unit


@pytest.fixture
def metrics() -> DurableMetrics:
    return DurableMetrics(registry=CollectorRegistry())


# ── counters ──────────────────────────────────────────────────────────────


class TestCounters:
    def test_retrieval_total_increments(self, metrics) -> None:
        assert metrics.retrieval_total() == 0
        metrics.inc_retrieval_total()
        metrics.inc_retrieval_total(n=3)
        assert metrics.retrieval_total() == 4

    def test_dropped_total_increments(self, metrics) -> None:
        metrics.inc_telemetry_dropped(n=10)
        assert metrics.telemetry_dropped_total() == 10

    def test_index_stale_increments(self, metrics) -> None:
        metrics.inc_index_stale()
        metrics.inc_index_stale()
        assert metrics.index_stale_total() == 2

    def test_write_failures_increments(self, metrics) -> None:
        metrics.inc_write_failures(n=5)
        assert metrics.write_failures_total() == 5


# ── labeled counters ──────────────────────────────────────────────────────


class TestLabeledCounters:
    def test_recognition_mode_per_label(self, metrics) -> None:
        metrics.inc_recognition_mode_decision("hybrid")
        metrics.inc_recognition_mode_decision("hybrid")
        metrics.inc_recognition_mode_decision("trigger_only")
        assert metrics.recognition_mode_decisions("hybrid") == 2
        assert metrics.recognition_mode_decisions("trigger_only") == 1
        assert metrics.recognition_mode_decisions("legacy") == 0

    def test_unknown_label_returns_zero(self, metrics) -> None:
        assert metrics.recognition_mode_decisions("never_seen") == 0


# ── gauges ────────────────────────────────────────────────────────────────


class TestQueueDepth:
    def test_tracks_max_seen(self, metrics) -> None:
        metrics.observe_queue_depth(10)
        metrics.observe_queue_depth(50)
        metrics.observe_queue_depth(30)  # below 50 — no update
        assert metrics.queue_depth_max() == 50

    def test_starts_at_zero(self, metrics) -> None:
        assert metrics.queue_depth_max() == 0


# ── histograms ────────────────────────────────────────────────────────────


class TestHistograms:
    def test_observe_retrieval_latency_does_not_raise(self, metrics) -> None:
        for s in [0.001, 0.05, 0.5, 5.0]:
            metrics.observe_retrieval_latency(s)

    def test_observe_enqueue_latency_does_not_raise(self, metrics) -> None:
        for s in [0.0001, 0.01, 0.1]:
            metrics.observe_enqueue_latency(s)


# ── §11 gate-health helper ────────────────────────────────────────────────


class TestGateHealth:
    def test_zero_traffic_is_healthy(self, metrics) -> None:
        # No retrieval calls yet → drop_ratio = 0.0, gate_health_ok = True.
        assert metrics.drop_ratio() == 0.0
        assert metrics.gate_health_ok() is True

    def test_drop_ratio_calculation(self, metrics) -> None:
        for _ in range(100):
            metrics.inc_retrieval_total()
        for _ in range(2):
            metrics.inc_telemetry_dropped()
        # 2/100 = 0.02 < 0.05 default threshold.
        assert metrics.drop_ratio() == pytest.approx(0.02)
        assert metrics.gate_health_ok() is True

    def test_drop_ratio_above_threshold_fails_health(self, metrics) -> None:
        for _ in range(100):
            metrics.inc_retrieval_total()
        for _ in range(10):
            metrics.inc_telemetry_dropped()
        # 10/100 = 0.10 > 0.05.
        assert metrics.drop_ratio() == pytest.approx(0.10)
        assert metrics.gate_health_ok() is False

    def test_custom_threshold_per_gate(self, metrics) -> None:
        for _ in range(100):
            metrics.inc_retrieval_total()
        for _ in range(2):
            metrics.inc_telemetry_dropped()
        # 2% drop — strict gate (1% threshold) fails; lax gate (5%) passes.
        assert metrics.gate_health_ok(max_drop_ratio=0.01) is False
        assert metrics.gate_health_ok(max_drop_ratio=0.05) is True


# ── snapshot ──────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_snapshot_includes_all_fields(self, metrics) -> None:
        metrics.inc_retrieval_total(n=5)
        metrics.inc_telemetry_dropped(n=1)
        metrics.inc_recognition_mode_decision("hybrid", n=2)
        metrics.observe_queue_depth(7)

        snap = metrics.snapshot()
        assert snap["counters"]["retrieval_total"] == 5
        assert snap["counters"]["telemetry_dropped_total"] == 1
        assert snap["labeled_counts"]["recognition_mode_decisions_total.hybrid"] == 2
        assert snap["queue_depth_max"] == 7
        assert snap["drop_ratio"] == pytest.approx(0.2)
        assert snap["gate_health_ok"] is False  # 0.2 > 0.05


# ── prometheus integration ────────────────────────────────────────────────


class TestPrometheusBackend:
    def test_metrics_register_under_custom_registry(self) -> None:
        registry = CollectorRegistry()
        metrics = DurableMetrics(registry=registry, namespace="test_ns")
        metrics.inc_retrieval_total(n=3)
        # prometheus_client does not double-suffix _total — a Counter named
        # "test_ns_retrieval_total" exposes sample "test_ns_retrieval_total".
        samples = [s for fam in registry.collect() for s in fam.samples]
        retrieval_total = next((s for s in samples if s.name == "test_ns_retrieval_total"), None)
        assert retrieval_total is not None, f"sample names were: {[s.name for s in samples]}"
        assert retrieval_total.value == 3.0

    def test_two_instances_with_separate_registries_dont_collide(self) -> None:
        m1 = DurableMetrics(registry=CollectorRegistry(), namespace="ns_a")
        m2 = DurableMetrics(registry=CollectorRegistry(), namespace="ns_b")
        m1.inc_retrieval_total(n=10)
        m2.inc_retrieval_total(n=20)
        assert m1.retrieval_total() == 10
        assert m2.retrieval_total() == 20


# ── thread safety ─────────────────────────────────────────────────────────


class TestThreadSafety:
    def test_concurrent_inc_does_not_lose_counts(self, metrics) -> None:
        N_THREADS = 10
        N_PER_THREAD = 100

        def worker() -> None:
            for _ in range(N_PER_THREAD):
                metrics.inc_retrieval_total()

        threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert metrics.retrieval_total() == N_THREADS * N_PER_THREAD
