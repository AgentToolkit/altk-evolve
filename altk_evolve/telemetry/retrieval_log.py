"""Best-effort, async, fail-isolated JSONL retrieval log (Phase 2).

Contract (per design_doc/implementation_plan.md §6 — Phase 2 telemetry):

- **Async**: writes happen on a background daemon thread. `log_retrieval()`
  enqueues and returns; the caller is never blocked on disk I/O.
- **Bounded**: the in-memory queue has a fixed capacity (default 1000).
  On overflow, the OLDEST queued event is dropped and the
  `dropped_records_total` counter is incremented.
- **Fail-isolated**: any exception during enqueue or writer-loop write is
  caught + logged at WARN. Telemetry NEVER raises into the caller.
- **Sample-able**: `EVOLVE_TELEMETRY_SAMPLE_RATE` env var (default 1.0)
  thins the log; events are dropped probabilistically before enqueue.
- **Date-partitioned**: events are written to `{log_dir}/{YYYY-MM-DD}.jsonl`,
  one event per line. The aggregator job reads these files daily.

This module is the source of the IMPLICIT_USAGE signal source: the
aggregator (Phase 2 next) folds retrieval frequency, query diversity,
and follow-up patterns into each guideline's outcome_evidence.

Codex review round-2 §4: this pipeline is intentionally lossy. The
graduation gates evaluate on `durable_metrics` (next), not on this log.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import queue
import random
import threading
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_QUEUE_SIZE = 1000
_DEFAULT_WRITE_TIMEOUT_SECONDS = 0.05  # 50ms; matches design doc.
_DEFAULT_SAMPLE_RATE = 1.0
_SHUTDOWN_SENTINEL = object()


class RetrievalLog:
    """Append-only JSONL writer for retrieval events.

    Construct one per process. Call `log_retrieval(event_dict)` from the
    retrieval hot path. The writer thread persists in the background.

    On process shutdown, call `close()` to flush remaining events
    (best-effort, with timeout). After `close()`, further `log_retrieval()`
    calls become no-ops (they return without raising).
    """

    def __init__(
        self,
        *,
        log_dir: str,
        sample_rate: float | None = None,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        write_timeout_seconds: float = _DEFAULT_WRITE_TIMEOUT_SECONDS,
    ) -> None:
        self.log_dir = log_dir
        self.sample_rate = sample_rate if sample_rate is not None else _read_sample_rate_from_env()
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._write_timeout_seconds = write_timeout_seconds
        self._stopped = threading.Event()
        self._closed = False
        self.dropped_records_total = 0
        self.write_failures_total = 0
        self.events_written_total = 0

        os.makedirs(self.log_dir, exist_ok=True)

        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            name="evolve-retrieval-log",
            daemon=True,
        )
        self._writer_thread.start()

    # ── public API ─────────────────────────────────────────────────────────

    def log_retrieval(self, event: dict[str, Any]) -> None:
        """Best-effort enqueue; never raises, never blocks long.

        On any failure (queue full, sample miss, post-close, malformed
        event) the call returns silently. Counters track drops separately.
        """
        if self._closed:
            return
        try:
            # Sampling — drop probabilistically before enqueue.
            if self.sample_rate < 1.0 and random.random() > self.sample_rate:
                return

            try:
                self._queue.put_nowait(event)
            except queue.Full:
                # Drop OLDEST then enqueue NEW. Best-effort: tolerate races
                # in which another thread drained between get_nowait and put_nowait.
                try:
                    self._queue.get_nowait()
                    self.dropped_records_total += 1
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(event)
                except queue.Full:
                    # Pathological: still full. Count and drop the new event.
                    self.dropped_records_total += 1
        except Exception as exc:  # noqa: BLE001 — telemetry never raises.
            logger.warning("log_retrieval suppressed exception: %s", exc)

    def close(self, *, timeout: float = 2.0) -> None:
        """Flush + stop the writer thread; safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(_SHUTDOWN_SENTINEL, timeout=0.5)
        except queue.Full:
            self._stopped.set()
        self._writer_thread.join(timeout=timeout)

    # ── internal ───────────────────────────────────────────────────────────

    def _writer_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is _SHUTDOWN_SENTINEL:
                return
            try:
                self._write_event(item)
                self.events_written_total += 1
            except Exception as exc:  # noqa: BLE001
                self.write_failures_total += 1
                logger.warning("retrieval-log write failed: %s", exc)

    def _write_event(self, event: dict[str, Any]) -> None:
        path = self._path_for(event)
        line = json.dumps(event, default=_json_default, separators=(",", ":"))
        # Append + flush; we let the OS buffer for performance. Daily rotation
        # is implicit via `_path_for`.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")

    def _path_for(self, event: dict[str, Any]) -> str:
        # Use event['timestamp'] if it's a parseable ISO date; else now-UTC.
        ts = event.get("timestamp")
        date_str: str
        if isinstance(ts, str) and len(ts) >= 10:
            date_str = ts[:10]
        elif isinstance(ts, _dt.datetime):
            date_str = ts.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d")
        else:
            date_str = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{date_str}.jsonl")


# ── helpers ────────────────────────────────────────────────────────────────


def _json_default(obj: Any) -> Any:
    """Coerce datetimes / Pydantic models / Enums to JSON-friendly forms."""
    from enum import Enum

    if isinstance(obj, _dt.datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=_dt.timezone.utc)
        return obj.astimezone(_dt.timezone.utc).isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _read_sample_rate_from_env() -> float:
    raw = os.environ.get("EVOLVE_TELEMETRY_SAMPLE_RATE")
    if raw is None:
        return _DEFAULT_SAMPLE_RATE
    try:
        rate = float(raw)
    except ValueError:
        logger.warning("ignoring invalid EVOLVE_TELEMETRY_SAMPLE_RATE=%r", raw)
        return _DEFAULT_SAMPLE_RATE
    return max(0.0, min(1.0, rate))
