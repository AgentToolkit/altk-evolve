"""``plugin_timeout`` for native (sync) hook plugins (#422).

A native plugin's sync method used to run inline in the async dispatch, which
blocked the event loop, so cpex's timeout could not fire until the call
returned and its late result was then applied. These tests pin the bounded
off-loop execution that replaced it: fail-closed timeouts, ``on_error``
parity with async plugins, discarded late results, unchanged concurrency,
fail-fast while a timed-out call is still running, contextvar propagation,
and no hang at loop or interpreter shutdown.

Requires the optional cpex package (``uv sync --extra hooks``).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import gc
import subprocess
import sys
import textwrap
import threading
import time
import weakref
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("cpex")

from cpex.framework import Plugin
from cpex.framework.models import OnError, PluginConfig, PluginMode, PluginResult

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.hooks.manager import (
    MemoryPolicyViolation,
    _build_native_adapter,
    _initialize_manager,
    _run_call,
    dispatch_llm_pre_call,
    get_plugin_manager,
    shutdown_hooks,
)
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import EvolveException

TIMEOUT = 0.3  # seconds; cpex's executor takes a float even though HooksConfig uses whole seconds
MESSAGES = [{"role": "user", "content": "hello"}]

_caller_var: contextvars.ContextVar[str] = contextvars.ContextVar("caller_var", default="unset")


@pytest.fixture(autouse=True)
def clean_hook_state(monkeypatch):
    monkeypatch.setattr("altk_evolve.hooks.manager.discover_hooks_config_path", lambda: None)
    shutdown_hooks()
    yield
    shutdown_hooks()


def _enable(plugin: Any, hooks: list[str], *, mode: str = "sequential", on_error: str = "fail", timeout: float = TIMEOUT) -> Any:
    """Register ``plugin`` (native, or a raw cpex Plugin) with a short timeout."""
    pm = _initialize_manager("", [], 30)
    pm.executor.timeout = timeout
    if isinstance(plugin, Plugin):
        pm._registry.register(plugin)
    else:
        config = PluginConfig(name="slow", kind="tests.slow", hooks=hooks, mode=PluginMode(mode), on_error=OnError(on_error))
        pm._registry.register(_build_native_adapter(plugin, config))
    return pm


class GatedSync:
    """Native llm_pre_call plugin that blocks until ``gate`` is set, then rewrites the messages."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls = 0
        self.finished = threading.Event()

    def llm_pre_call(self, payload: Any, context: Any) -> Any:
        self.calls += 1
        try:
            self.gate.wait(10)
            return payload.replace(messages=[{"role": "user", "content": "LATE"}])
        finally:
            self.finished.set()


class GatedAsync(Plugin):
    """Raw cpex (async) control with the same behavior as :class:`GatedSync`."""

    def __init__(self, mode: str, on_error: str) -> None:
        super().__init__(
            PluginConfig(name="slow", kind="tests.slow", hooks=["llm_pre_call"], mode=PluginMode(mode), on_error=OnError(on_error))
        )
        self.calls = 0

    async def llm_pre_call(self, payload, context):
        self.calls += 1
        await asyncio.sleep(10)
        return PluginResult(continue_processing=True)


def _make(flavor: str, mode: str = "sequential", on_error: str = "fail") -> Any:
    plugin = GatedSync() if flavor == "sync" else GatedAsync(mode, on_error)
    _enable(plugin, ["llm_pre_call"], mode=mode, on_error=on_error)
    return plugin


def _timed(fn, *args, **kwargs) -> tuple[float, Any]:
    start = time.monotonic()
    try:
        result = fn(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - returned to the test for assertions
        result = exc
    return time.monotonic() - start, result


@pytest.fixture
def release():
    """Release every gated sync plugin at teardown so no worker outlives its test for long."""
    plugins: list[GatedSync] = []
    yield plugins.append
    for plugin in plugins:
        plugin.gate.set()


# ── the timeout fires ────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize("in_loop", [False, True], ids=["no-loop", "inside-running-loop"])
@pytest.mark.parametrize("flavor", ["sync", "async"])
def test_hook_past_timeout_fails_closed_within_the_timeout(flavor: str, in_loop: bool, release):
    plugin = _make(flavor)
    if flavor == "sync":
        release(plugin)

    async def from_loop():
        return _timed(dispatch_llm_pre_call, MESSAGES, purpose="t")

    elapsed, result = asyncio.run(from_loop()) if in_loop else _timed(dispatch_llm_pre_call, MESSAGES, purpose="t")

    assert isinstance(result, MemoryPolicyViolation), result
    assert result.code == "PLUGIN_ERROR" and "timeout" in result.reason
    assert elapsed < TIMEOUT + 0.5, f"returned after {elapsed:.2f}s; the {TIMEOUT}s timeout did not fire"


@pytest.mark.unit
@pytest.mark.parametrize("on_error", ["ignore", "disable"])
@pytest.mark.parametrize("flavor", ["sync", "async"])
def test_on_error_ignore_and_disable_match_async_plugins(flavor: str, on_error: str, release):
    plugin = _make(flavor, on_error=on_error)
    if flavor == "sync":
        release(plugin)

    elapsed, result = _timed(dispatch_llm_pre_call, MESSAGES, purpose="t")
    assert result == MESSAGES, result
    assert elapsed < TIMEOUT + 0.5

    # "disable" takes the plugin out of rotation: the next call skips it (it is
    # not even refused as busy). "ignore" keeps calling it.
    if flavor == "sync":
        _wait_until_idle(plugin)
    elapsed, result = _timed(dispatch_llm_pre_call, MESSAGES, purpose="t")
    if on_error == "disable":
        assert plugin.calls == 1
        assert result == MESSAGES and elapsed < 0.2
    else:
        assert plugin.calls == 2


def _wait_until_idle(plugin: GatedSync) -> None:
    """Let a released, timed-out call finish so the plugin accepts calls again."""
    plugin.gate.set()
    assert plugin.finished.wait(5)
    # The abandoned mark clears on the call's thread just after the method returns.
    deadline = time.monotonic() + 5
    while any(r._abandoned for r in _runners()) and time.monotonic() < deadline:
        time.sleep(0.01)


def _runners() -> list[Any]:
    pm = get_plugin_manager()
    assert pm is not None
    return [ref.plugin._runner for ref in pm._registry.get_all_plugins() if hasattr(ref.plugin, "_runner")]


# ── late results ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_late_result_is_never_applied(release):
    plugin = _make("sync", mode="transform", on_error="ignore")
    release(plugin)
    original = [dict(m) for m in MESSAGES]

    result = dispatch_llm_pre_call(original, purpose="t")
    _wait_until_idle(plugin)

    assert result == MESSAGES
    assert original == MESSAGES  # the caller's list is untouched too
    # The worker is free again, and an on-time result still applies.
    assert dispatch_llm_pre_call(MESSAGES, purpose="t") == [{"role": "user", "content": "LATE"}]


class LateBackendPatcher:
    """memory_post_read plugin that, after its caller gave up, tries to stamp the store."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.finished = threading.Event()
        self.error: BaseException | None = None

    def memory_post_read(self, payload: Any, context: Any) -> Any:
        try:
            self.gate.wait(10)
            for entity in payload.entities:
                context.backend.update_entity_metadata(payload.namespace_id, entity["id"], {"late": True})
        except BaseException as exc:  # noqa: BLE001 - inspected by the test
            self.error = exc
        finally:
            self.finished.set()
        return None


@pytest.mark.unit
def test_timed_out_call_cannot_patch_the_store_late(tmp_path: Path, release):
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="first", type="note")], enable_conflict_resolution=False)

    plugin = LateBackendPatcher()
    release(plugin)
    _enable(plugin, ["memory_post_read"], mode="fire_and_forget", on_error="ignore")

    target = client.search_entities("ns", limit=1)[0]
    plugin.gate.set()
    assert plugin.finished.wait(5)

    assert isinstance(plugin.error, EvolveException) and "hook call" in str(plugin.error)
    stored = client.get_entity_by_id("ns", target.id)
    assert stored is not None and "late" not in stored.metadata


# ── bounds and serialization ─────────────────────────────────────────


@pytest.mark.unit
def test_new_calls_fail_fast_while_a_timed_out_call_is_still_running(release):
    plugin = _make("sync")
    release(plugin)

    with pytest.raises(MemoryPolicyViolation, match="timeout"):
        dispatch_llm_pre_call(MESSAGES, purpose="t")

    elapsed, result = _timed(dispatch_llm_pre_call, MESSAGES, purpose="t")
    assert isinstance(result, MemoryPolicyViolation), result
    assert "still running" in result.reason
    assert elapsed < TIMEOUT / 2, "a call queued behind the stuck one instead of failing fast"
    assert plugin.calls == 1

    _wait_until_idle(plugin)
    assert dispatch_llm_pre_call(MESSAGES, purpose="t") == [{"role": "user", "content": "LATE"}]


class SlowDetector:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def llm_pre_call(self, payload: Any, context: Any) -> Any:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.2)
        with self.lock:
            self.active -= 1
        return None


@pytest.mark.unit
def test_concurrent_callers_are_not_serialized():
    """As when they ran inline: 8 callers of a 0.2s detector all finish well inside a 1s timeout."""
    plugin = SlowDetector()
    _enable(plugin, ["llm_pre_call"], timeout=1)
    errors: list[BaseException] = []

    def call() -> None:
        try:
            dispatch_llm_pre_call(MESSAGES, purpose="t")
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, errors
    assert time.monotonic() - start < 0.8
    assert plugin.peak > 1


@pytest.mark.unit
def test_a_call_cancelled_before_it_starts_never_runs():
    calls: list[int] = []
    future: concurrent.futures.Future = concurrent.futures.Future()
    assert future.cancel()
    _run_call(future, contextvars.copy_context(), calls.append, (1,))
    assert calls == []


@pytest.mark.unit
def test_nothing_from_a_call_outlives_it():
    """No thread, plugin, payload or backend is kept alive after the call and shutdown_hooks()."""

    class Redactor:
        def llm_pre_call(self, payload: Any, context: Any) -> Any:
            return payload.replace(messages=[{"role": "user", "content": "[REDACTED]"}])

    for _ in range(5):
        plugin = Redactor()
        ref = weakref.ref(plugin)
        _enable(plugin, ["llm_pre_call"], timeout=5)
        assert dispatch_llm_pre_call([{"role": "user", "content": "ssn 123-45-6789"}], purpose="t")[0]["content"] == "[REDACTED]"
        del plugin
        shutdown_hooks()
    gc.collect()
    assert ref() is None
    deadline = time.monotonic() + 2
    while any(t.name.startswith("evolve-hook") for t in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not [t.name for t in threading.enumerate() if t.name.startswith("evolve-hook")]


# ── context and re-entrancy ──────────────────────────────────────────


class ContextProbe:
    def __init__(self) -> None:
        self.seen: tuple[str, int] | None = None

    def llm_pre_call(self, payload: Any, context: Any) -> Any:
        self.seen = (_caller_var.get(), threading.get_ident())
        return None


@pytest.mark.unit
def test_contextvars_reach_the_worker():
    plugin = ContextProbe()
    _enable(plugin, ["llm_pre_call"], timeout=5)
    token = _caller_var.set("from-caller")
    try:
        dispatch_llm_pre_call(MESSAGES, purpose="t")
    finally:
        _caller_var.reset(token)
    assert plugin.seen is not None
    value, ident = plugin.seen
    assert value == "from-caller"
    assert ident != threading.get_ident()  # it ran off the caller's thread


class NestedPatcher:
    """One native plugin on two hooks: its post_read stamps metadata, which dispatches its own pre_metadata_patch."""

    def __init__(self) -> None:
        self.patches = 0

    def memory_post_read(self, payload: Any, context: Any) -> Any:
        for entity in payload.entities:
            context.backend.update_entity_metadata(payload.namespace_id, entity["id"], {"seen": True})
        return None

    def memory_pre_metadata_patch(self, payload: Any, context: Any) -> Any:
        self.patches += 1
        return None


@pytest.mark.unit
def test_hook_dispatched_from_a_worker_does_not_queue_behind_it(tmp_path: Path):
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="first", type="note")], enable_conflict_resolution=False)

    plugin = NestedPatcher()
    _enable(plugin, ["memory_post_read", "memory_pre_metadata_patch"], timeout=2)

    elapsed, result = _timed(client.search_entities, "ns", limit=5)
    assert not isinstance(result, BaseException), result
    assert elapsed < 1.5, "the nested dispatch waited on its own plugin's worker until the timeout"
    assert plugin.patches == 1
    stored = client.get_entity_by_id("ns", result[0].id)
    assert stored is not None and stored.metadata.get("seen") is True


class HelperThreadPatcher:
    """Makes its ``context.backend`` callback from its own pool thread, where contextvars are not copied."""

    def __init__(self, target_id: str) -> None:
        self.target_id = target_id

    def memory_pre_write(self, payload: Any, context: Any) -> Any:
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            pool.submit(context.backend.update_entity_metadata, payload.namespace_id, self.target_id, {"tag": 1}).result()
        return None

    def memory_pre_metadata_patch(self, payload: Any, context: Any) -> Any:
        return None


@pytest.mark.unit
def test_callback_from_a_plugin_helper_thread_does_not_wait_on_the_plugin(tmp_path: Path):
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="first", type="note")], enable_conflict_resolution=False)
    target = client.search_entities("ns", limit=1)[0]
    _enable(HelperThreadPatcher(target.id), ["memory_pre_write", "memory_pre_metadata_patch"], timeout=2)

    elapsed, result = _timed(client.update_entities, "ns", [Entity(content="second", type="note")], enable_conflict_resolution=False)
    assert not isinstance(result, BaseException), result
    assert elapsed < 1.5


class NestedReader:
    def __init__(self) -> None:
        self.calls = 0

    def memory_post_read(self, payload: Any, context: Any) -> Any:
        self.calls += 1
        context.backend.search_entities(payload.namespace_id, limit=5)
        return None


@pytest.mark.unit
def test_post_read_reentrancy_guard_holds_on_the_worker(tmp_path: Path):
    client = EvolveClient(config=EvolveConfig(backend="filesystem", settings=FilesystemSettings(data_dir=str(tmp_path))))
    client.create_namespace("ns")
    client.update_entities("ns", [Entity(content="first", type="note")], enable_conflict_resolution=False)
    plugin = NestedReader()
    _enable(plugin, ["memory_post_read"], timeout=5)

    assert len(client.search_entities("ns", limit=5)) == 1
    assert plugin.calls == 1


# ── fast hooks and fire-and-forget ───────────────────────────────────


class Tagger:
    def llm_pre_call(self, payload: Any, context: Any) -> Any:
        return payload.replace(messages=[{**m, "content": f"[tag] {m['content']}"} for m in payload.messages])


@pytest.mark.unit
def test_fast_sync_hook_still_transforms():
    _enable(Tagger(), ["llm_pre_call"], mode="transform", timeout=5)
    for _ in range(20):
        assert dispatch_llm_pre_call(MESSAGES, purpose="t") == [{"role": "user", "content": "[tag] hello"}]


@pytest.mark.unit
def test_fire_and_forget_sync_hook_is_bounded_by_the_timeout(release):
    plugin = GatedSync()
    release(plugin)
    _enable(plugin, ["llm_pre_call"], mode="fire_and_forget")

    elapsed, result = _timed(dispatch_llm_pre_call, MESSAGES, purpose="t")
    assert result == MESSAGES
    assert elapsed < TIMEOUT + 0.5


# ── shutdown ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_no_hang_at_loop_or_interpreter_shutdown():
    """A plugin stuck for 60s must not hold up loop close or interpreter exit."""
    code = textwrap.dedent(
        """
        import asyncio, time
        from cpex.framework.models import OnError, PluginConfig, PluginMode
        from altk_evolve.hooks import manager as m

        class Stuck:
            def llm_pre_call(self, payload, context):
                time.sleep(60)

        def enable():
            m.shutdown_hooks()
            pm = m._initialize_manager("", [], 1)
            config = PluginConfig(name="stuck", kind="x.stuck", hooks=["llm_pre_call"], mode=PluginMode.SEQUENTIAL, on_error=OnError.FAIL)
            pm._registry.register(m._build_native_adapter(Stuck(), config))

        def call():
            start = time.monotonic()
            try:
                m.dispatch_llm_pre_call([{"role": "user", "content": "x"}], purpose="t")
            except m.MemoryPolicyViolation:
                pass
            return time.monotonic() - start

        enable()
        print("no-loop", round(call(), 2))
        enable()

        async def inside():
            return call()

        print("in-loop", round(asyncio.run(inside()), 2))
        """
    )
    start = time.monotonic()
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    total = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    timings = dict(line.split() for line in proc.stdout.splitlines())
    assert float(timings["no-loop"]) < 1.5 and float(timings["in-loop"]) < 1.5, timings
    assert total < 15, f"interpreter exit waited for the stuck plugin ({total:.1f}s)"
