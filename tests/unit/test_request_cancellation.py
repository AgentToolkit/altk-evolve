"""Cooperative cancellation prevents writes after cancelled thread-backed requests."""

from threading import Event
from types import SimpleNamespace

import anyio
import pytest

from altk_evolve.telemetry import model_completion

pytestmark = pytest.mark.unit


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_cancelled_model_call_does_not_continue_to_save(monkeypatch):
    started, release, finished = Event(), Event(), Event()
    saved = []

    def completion(**kwargs):
        started.set()
        assert release.wait(5)
        return SimpleNamespace(usage=None)

    monkeypatch.setattr("litellm.completion", completion)

    def request():
        try:
            model_completion(model="test", messages=[])
            saved.append("memory")
        finally:
            finished.set()

    with anyio.fail_after(10):
        async with anyio.create_task_group() as group:
            group.start_soon(anyio.to_thread.run_sync, request)
            while not started.is_set():
                await anyio.sleep(0.01)
            group.cancel_scope.cancel()
            release.set()
    assert finished.is_set()
    assert saved == []


def test_model_completion_outside_worker_still_returns(monkeypatch):
    response = SimpleNamespace(usage=None)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: response)
    assert model_completion(model="test", messages=[]) is response


@pytest.mark.anyio
async def test_cancelled_prepared_write_is_not_committed(tmp_path, monkeypatch):
    from altk_evolve.backend.filesystem import FilesystemEntityBackend
    from altk_evolve.config.filesystem import FilesystemSettings
    from altk_evolve.schema.core import Entity

    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    backend = FilesystemEntityBackend(FilesystemSettings(data_dir=str(tmp_path)))
    backend.create_namespace("cancel-test")
    prepared = backend.prepare_updates("cancel-test", [Entity(type="fact", content="pending")], enable_conflict_resolution=False)
    started, release, finished = Event(), Event(), Event()

    def request():
        try:
            started.set()
            assert release.wait(5)
            backend.commit_prepared("cancel-test", [prepared])
        finally:
            finished.set()

    with anyio.fail_after(10):
        async with anyio.create_task_group() as group:
            group.start_soon(anyio.to_thread.run_sync, request)
            while not started.is_set():
                await anyio.sleep(0.01)
            group.cancel_scope.cancel()
            release.set()
    assert finished.is_set()
    assert backend.scan_entities("cancel-test") == []


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_mcp_notification_cancels_thread_before_save(monkeypatch, mode):
    import asyncio
    from contextlib import suppress

    from fastmcp import Client, FastMCP
    from fastmcp.server.dependencies import get_context
    from fastmcp.server.middleware import Middleware
    from mcp.types import CancelledNotification, CancelledNotificationParams

    started, release, finished = Event(), Event(), Event()
    request_ids, saved, failures = [], [], []
    cancellation_received = anyio.Event()

    class ObserveCancellation(Middleware):
        async def on_message(self, context, call_next):
            if context.method == "notifications/cancelled":
                cancellation_received.set()
            return await call_next(context)

    server = FastMCP("cancellation-test", middleware=[ObserveCancellation()])

    def completion(**kwargs):
        started.set()
        assert release.wait(5)
        return SimpleNamespace(usage=None)

    monkeypatch.setattr("litellm.completion", completion)

    @server.tool
    def save_memory() -> str:
        context = get_context().request_context
        assert context is not None
        request_ids.append(context.request_id)
        try:
            model_completion(model="test", messages=[])
            saved.append("memory")
            return "saved"
        except BaseException as exc:
            failures.append(type(exc))
            raise
        finally:
            finished.set()

    with anyio.fail_after(10):
        async with Client(server, mode=mode) as client:
            task = asyncio.create_task(client.call_tool("save_memory"))
            try:
                while not started.is_set():
                    await anyio.sleep(0.01)
                await client.session.send_notification(CancelledNotification(params=CancelledNotificationParams(requestId=request_ids[0])))
                # MCP 2 deliberately leaves a cancelled request unanswered. Observe
                # delivery (the SDK cancels the scope before middleware runs), then
                # let the blocking model return and hit our cooperative checkpoint.
                await cancellation_received.wait()
                release.set()
                while not finished.is_set():
                    await anyio.sleep(0.01)
                assert saved == []
                assert failures == [asyncio.CancelledError]
            finally:
                release.set()
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            result = await client.call_tool("save_memory")
            assert result.data == "saved"
    assert saved == ["memory"]
