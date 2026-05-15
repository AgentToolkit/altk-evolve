"""Gate-grade durable metrics (Phase 2).

Counterpart to the lossy `retrieval_log`. Where the retrieval log is
best-effort and drops events under stress, this module maintains
*durable* counters and histograms that the §11 graduation/rollback
gates can rely on.

Codex review round-2 §4: telemetry-derived gates can't rest on a lossy
pipeline because drops correlate with the exact stress conditions the
gates care about. Durable metrics fixes that.

Backend: prometheus_client. Counters/histograms register with a
caller-supplied `CollectorRegistry` (defaults to the process-global
registry). For tests, pass a custom registry to avoid cross-test
contamination.

Internal mirror counters (`int`) live alongside the Prometheus exports
so callers can read values directly without scraping the registry.

§11 gate-health rule: if `dropped_total / retrieval_total > 0.05` over
a gate evaluation window, the gate **fails open** — no graduation or
rollback decision is made until telemetry health is restored.
"""

from __future__ import annotations

import threading
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


# Default histogram buckets for retrieval latency (seconds).
_RETRIEVAL_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


class DurableMetrics:
    """Gate-grade counters + histograms.

    Counters are inc-only; histograms record latency observations.
    Read methods return current snapshots from the internal `int` mirror,
    not from prometheus's wire format (which is exposed via the registry
    for scraping).
    """

    def __init__(self, *, registry: CollectorRegistry | None = None, namespace: str = "evolve") -> None:
        self.registry = registry
        # RLock so snapshot() can call drop_ratio()/gate_health_ok() while
        # holding the lock without deadlocking.
        self._lock = threading.RLock()
        self._namespace = namespace

        # ── Counters (never dropped, fail-closed) ──────────────────────────
        self._retrieval_total_counter = Counter(
            f"{namespace}_retrieval_total",
            "Total retrieval calls served by EvolveClient.search_entities and friends.",
            registry=self.registry,
        )
        self._telemetry_dropped_counter = Counter(
            f"{namespace}_telemetry_dropped_total",
            "Total retrieval-log records dropped due to queue overflow or sampling.",
            registry=self.registry,
        )
        self._index_stale_counter = Counter(
            f"{namespace}_index_stale_total",
            "Times a namespace was detected as stale at retrieval time (Phase 3).",
            registry=self.registry,
        )
        self._recognition_mode_decisions_counter = Counter(
            f"{namespace}_recognition_mode_decisions_total",
            "Recognition-mode decisions taken by EvolveClient (Phase 3+).",
            ["mode"],
            registry=self.registry,
        )
        self._write_failures_counter = Counter(
            f"{namespace}_write_failures_total",
            "Failed write attempts in the retrieval-log writer thread.",
            registry=self.registry,
        )

        # ── Gauges ─────────────────────────────────────────────────────────
        self._queue_depth_max_gauge = Gauge(
            f"{namespace}_telemetry_queue_depth_max",
            "Highest observed depth of the retrieval-log queue.",
            registry=self.registry,
        )

        # ── Histograms (sampled at low rate but durable) ───────────────────
        self._retrieval_latency_hist = Histogram(
            f"{namespace}_retrieval_latency_seconds",
            "End-to-end retrieval latency observed at EvolveClient.search_entities.",
            buckets=_RETRIEVAL_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self._telemetry_enqueue_latency_hist = Histogram(
            f"{namespace}_telemetry_enqueue_latency_seconds",
            "Latency observed inside log_retrieval (enqueue path only).",
            buckets=_RETRIEVAL_LATENCY_BUCKETS,
            registry=self.registry,
        )

        # Mirror of counter values for direct read access; updated under
        # `_lock` so concurrent inc + read is consistent.
        self._counts: dict[str, int] = {
            "retrieval_total": 0,
            "telemetry_dropped_total": 0,
            "index_stale_total": 0,
            "write_failures_total": 0,
        }
        self._labeled_counts: dict[tuple[str, str], int] = {}  # (counter_name, label_value) → count
        self._queue_depth_max: int = 0

    # ── inc methods (counters) ─────────────────────────────────────────────

    def inc_retrieval_total(self, n: int = 1) -> None:
        with self._lock:
            self._counts["retrieval_total"] += n
        self._retrieval_total_counter.inc(n)

    def inc_telemetry_dropped(self, n: int = 1) -> None:
        with self._lock:
            self._counts["telemetry_dropped_total"] += n
        self._telemetry_dropped_counter.inc(n)

    def inc_index_stale(self, n: int = 1) -> None:
        with self._lock:
            self._counts["index_stale_total"] += n
        self._index_stale_counter.inc(n)

    def inc_write_failures(self, n: int = 1) -> None:
        with self._lock:
            self._counts["write_failures_total"] += n
        self._write_failures_counter.inc(n)

    def inc_recognition_mode_decision(self, mode: str, n: int = 1) -> None:
        key = ("recognition_mode_decisions_total", mode)
        with self._lock:
            self._labeled_counts[key] = self._labeled_counts.get(key, 0) + n
        self._recognition_mode_decisions_counter.labels(mode=mode).inc(n)

    # ── set methods (gauges) ──────────────────────────────────────────────

    def observe_queue_depth(self, depth: int) -> None:
        """Record current queue depth; gauge tracks the max."""
        with self._lock:
            if depth > self._queue_depth_max:
                self._queue_depth_max = depth
                self._queue_depth_max_gauge.set(depth)

    # ── observe methods (histograms) ──────────────────────────────────────

    def observe_retrieval_latency(self, seconds: float) -> None:
        self._retrieval_latency_hist.observe(seconds)

    def observe_enqueue_latency(self, seconds: float) -> None:
        self._telemetry_enqueue_latency_hist.observe(seconds)

    # ── read methods ──────────────────────────────────────────────────────

    def retrieval_total(self) -> int:
        with self._lock:
            return self._counts["retrieval_total"]

    def telemetry_dropped_total(self) -> int:
        with self._lock:
            return self._counts["telemetry_dropped_total"]

    def index_stale_total(self) -> int:
        with self._lock:
            return self._counts["index_stale_total"]

    def write_failures_total(self) -> int:
        with self._lock:
            return self._counts["write_failures_total"]

    def recognition_mode_decisions(self, mode: str) -> int:
        with self._lock:
            return self._labeled_counts.get(("recognition_mode_decisions_total", mode), 0)

    def queue_depth_max(self) -> int:
        with self._lock:
            return self._queue_depth_max

    # ── §11 gate-health helper ─────────────────────────────────────────────

    def drop_ratio(self) -> float:
        """Telemetry drop ratio over the metrics' lifetime.

        Returns 0.0 when retrieval_total is 0 (no events yet → not unhealthy
        by definition; gates should not trigger on zero-traffic windows).
        """
        with self._lock:
            total = self._counts["retrieval_total"]
            dropped = self._counts["telemetry_dropped_total"]
        if total == 0:
            return 0.0
        return dropped / total

    def gate_health_ok(self, *, max_drop_ratio: float = 0.05) -> bool:
        """Return True if the drop ratio is within the gate-health threshold.

        Per design_doc/implementation_plan.md §11: gates fail open when this
        returns False (drop_ratio > threshold). The default 0.05 (5%) matches
        the documented threshold; override per-gate as needed.
        """
        return self.drop_ratio() <= max_drop_ratio

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of all counters + gauges."""
        with self._lock:
            return {
                "counters": dict(self._counts),
                "labeled_counts": {f"{name}.{label}": v for (name, label), v in self._labeled_counts.items()},
                "queue_depth_max": self._queue_depth_max,
                "drop_ratio": self.drop_ratio(),
                "gate_health_ok": self.gate_health_ok(),
            }
