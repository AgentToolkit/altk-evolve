import asyncio
import http.client
import logging
import socket
import threading
import time
import tomllib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

from altk_evolve.frontend.mcp import __main__ as launcher

pytestmark = pytest.mark.unit


def test_pyproject_exports_mcp_launcher_script() -> None:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    parsed = tomllib.loads(pyproject_path.read_text())

    assert parsed["project"]["scripts"]["evolve-mcp"] == "altk_evolve.frontend.mcp.__main__:main"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    # main() reads EVOLVE_DASHBOARD from ./.env. Run from an empty directory so a developer's
    # repo-root .env (e.g. EVOLVE_DASHBOARD=false) can't change what these tests see.
    monkeypatch.chdir(tmp_path)
    events = []
    client = object()
    monkeypatch.setattr(launcher, "get_client", lambda: client)

    @contextmanager
    def lifecycle(injected):
        assert injected is client
        events.append("started")
        try:
            yield
        finally:
            events.append("stopped")

    monkeypatch.setattr(launcher, "retention_runtime", lifecycle)
    return events


def test_stdio_launcher_starts_ui_thread(monkeypatch, runtime) -> None:
    monkeypatch.delenv("EVOLVE_DASHBOARD", raising=False)
    thread_calls: list[tuple[object, bool]] = []
    run_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeThread:
        def __init__(self, target, daemon):
            thread_calls.append((target, daemon))

        def start(self) -> None:
            thread_calls.append(("started", True))

    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher.mcp, "run", lambda *args, **kwargs: run_calls.append((args, kwargs)))
    monkeypatch.setattr(launcher.sys, "argv", ["evolve-mcp"])

    launcher.main()
    assert runtime == ["started", "stopped"]

    assert thread_calls[0] == (launcher.run_api_server, True)
    assert thread_calls[1] == ("started", True)
    assert run_calls == [((), {})]


def _run_stdio(monkeypatch, argv: list[str]) -> list[object]:
    """Run main() over stdio with threads and the MCP loop stubbed; return the thread targets started."""
    started: list[object] = []

    class FakeThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self) -> None:
            started.append(self.target)

    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher.mcp, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher.sys, "argv", ["evolve-mcp", *argv])
    launcher.main()
    return started


@pytest.mark.parametrize(
    ("argv", "env"),
    [
        (["--no-dashboard"], None),
        ([], "false"),
        ([], "0"),
        ([], "off"),
        ([], "no"),
        # The spellings pydantic reads as False for every other EVOLVE_ boolean.
        ([], "f"),
        ([], "n"),
        ([], " OFF "),
        ([], "False"),
        (["--no-dashboard"], "true"),
    ],
)
def test_stdio_launcher_can_turn_the_dashboard_off(monkeypatch, runtime, caplog, argv, env) -> None:
    if env is None:
        monkeypatch.delenv("EVOLVE_DASHBOARD", raising=False)
    else:
        monkeypatch.setenv("EVOLVE_DASHBOARD", env)
    with caplog.at_level(logging.INFO, logger="evolve-mcp"):
        assert _run_stdio(monkeypatch, argv) == []
    assert "dashboard disabled" in caplog.text
    assert runtime == ["started", "stopped"]


@pytest.mark.parametrize("env", ["true", "1", "yes", "on", "t", "y", " True "])
def test_stdio_launcher_keeps_the_dashboard_on_for_true_values(monkeypatch, runtime, env) -> None:
    monkeypatch.setenv("EVOLVE_DASHBOARD", env)
    assert _run_stdio(monkeypatch, []) == [launcher.run_api_server]


@pytest.mark.parametrize("env", ["fasle", "disable", "none", "2"])
def test_unrecognised_dashboard_value_fails_closed(monkeypatch, runtime, caplog, env) -> None:
    """A mistyped off switch must not leave the unauthenticated listener up."""
    monkeypatch.setenv("EVOLVE_DASHBOARD", env)
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        assert _run_stdio(monkeypatch, []) == []
    assert f"EVOLVE_DASHBOARD={env!r} is not a boolean" in caplog.text
    assert "leaving the dashboard off" in caplog.text


def test_dashboard_flag_overrides_the_environment(monkeypatch, runtime) -> None:
    monkeypatch.setenv("EVOLVE_DASHBOARD", "false")
    assert _run_stdio(monkeypatch, ["--dashboard"]) == [launcher.run_api_server]


def test_dashboard_flag_overrides_an_unrecognised_value(monkeypatch, runtime, caplog) -> None:
    monkeypatch.setenv("EVOLVE_DASHBOARD", "fasle")
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        assert _run_stdio(monkeypatch, ["--dashboard"]) == [launcher.run_api_server]
    assert "is not a boolean" not in caplog.text


@pytest.mark.parametrize("env", ["", " ", "\t", " \n "])
@pytest.mark.parametrize(
    ("dotenv", "expected_started"), [("EVOLVE_DASHBOARD=false\n", False), ("EVOLVE_DASHBOARD=true\n", True), ("", True)]
)
def test_empty_dashboard_value_counts_as_unset(monkeypatch, runtime, tmp_path, caplog, env, dotenv, expected_started) -> None:
    """An empty or whitespace-only `EVOLVE_DASHBOARD` in the environment doesn't hide `.env`,
    and on its own means the default (on). It is not reported as a bad value."""
    (tmp_path / ".env").write_text(dotenv)
    monkeypatch.setenv("EVOLVE_DASHBOARD", env)
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        assert (_run_stdio(monkeypatch, []) == [launcher.run_api_server]) is expected_started
    assert "is not a boolean" not in caplog.text


def test_blank_dashboard_value_in_dotenv_counts_as_unset(monkeypatch, runtime, tmp_path, caplog) -> None:
    (tmp_path / ".env").write_text('EVOLVE_DASHBOARD="  "\n')
    monkeypatch.delenv("EVOLVE_DASHBOARD", raising=False)
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        assert _run_stdio(monkeypatch, []) == [launcher.run_api_server]
    assert "is not a boolean" not in caplog.text


@pytest.mark.parametrize(("env", "expected_started"), [(None, False), ("true", True)])
def test_dashboard_setting_is_read_from_dotenv(monkeypatch, runtime, tmp_path, env, expected_started) -> None:
    """EVOLVE_DASHBOARD in `.env` turns the dashboard off, like every other EVOLVE_ setting;
    a value in the process environment still takes precedence over the file."""
    (tmp_path / ".env").write_text("EVOLVE_DASHBOARD=false\n")
    monkeypatch.chdir(tmp_path)
    if env is None:
        monkeypatch.delenv("EVOLVE_DASHBOARD", raising=False)
    else:
        monkeypatch.setenv("EVOLVE_DASHBOARD", env)
    assert (_run_stdio(monkeypatch, []) == [launcher.run_api_server]) is expected_started


@contextmanager
def _serving(target: Callable[[], object], get_server: Callable[[], Any], timeout: float = 10.0) -> Iterator[Any]:
    """Run a dashboard server in a thread, yield it once it is serving, and always stop it.

    A startup that fails or hangs is reported as such, and the server thread is told to exit either way.
    """
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + timeout
        while thread.is_alive() and not getattr(get_server(), "started", False) and time.monotonic() < deadline:
            time.sleep(0.02)
        server = get_server()
        assert getattr(server, "started", False), f"dashboard server did not start within {timeout:g} s"
        yield server
    finally:
        if (server := get_server()) is not None:
            server.should_exit = True
        thread.join(timeout)
    assert not thread.is_alive(), f"dashboard server did not stop within {timeout:g} s"


def _real_api_server(monkeypatch) -> AbstractContextManager[Any]:
    """The real `run_api_server`, with its default host, on a port the OS picks."""
    servers: list[launcher._DashboardServer] = []

    class RecordingServer(launcher._DashboardServer):
        def __init__(self, config) -> None:
            super().__init__(config)
            servers.append(self)

    monkeypatch.setattr(launcher, "_DashboardServer", RecordingServer)
    return _serving(lambda: launcher.run_api_server(port=0), lambda: servers[0] if servers else None)


def _bound(server) -> list[tuple[str, int]]:
    return [sock.getsockname()[:2] for listener in server.servers for sock in listener.sockets]


def test_dashboard_logs_the_address_it_bound(caplog) -> None:
    """The listener is announced, with its real address, only once the socket is bound."""

    async def asgi(scope, receive, send):  # minimal app; lifespan is disabled below
        raise RuntimeError("no requests expected")

    server = launcher._DashboardServer(launcher.uvicorn.Config(asgi, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        with _serving(lambda: asyncio.run(server.serve()), lambda: server):
            (_, port), *_ = _bound(server)
    assert f"listening on http://127.0.0.1:{port}/ without authentication" in caplog.text
    assert "--no-dashboard" in caplog.text


def test_dashboard_keeps_uvicorn_logs_off_stdout(monkeypatch, capfd) -> None:
    """Over stdio, stdout is the MCP JSON-RPC channel. uvicorn writes its access log to stdout at INFO,
    so a request to the dashboard must leave stdout untouched (fd 1 as well as sys.stdout)."""
    capfd.readouterr()
    with _real_api_server(monkeypatch) as server:
        (_, port), *_ = _bound(server)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            conn.request("GET", "/api/no-such-route")
            status = conn.getresponse().status
        finally:
            conn.close()
    assert status == 404
    assert capfd.readouterr().out == ""


def test_dashboard_binds_loopback_only(monkeypatch) -> None:
    """The REST API has no authentication, so by default it must listen on 127.0.0.1 and nowhere else."""
    with _real_api_server(monkeypatch) as server:
        hosts = {host for host, _ in _bound(server)}
    assert hosts == {"127.0.0.1"}


def test_dashboard_bind_failure_is_reported_and_does_not_raise(caplog) -> None:
    """uvicorn exits when the port is taken; only the dashboard thread may stop, with a clear log line."""
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
            launcher.run_api_server(host="127.0.0.1", port=port)
    assert f"Evolve dashboard did not start on http://127.0.0.1:{port}/" in caplog.text
    assert "listening on" not in caplog.text


def test_dashboard_lifespan_failure_is_reported(monkeypatch, caplog) -> None:
    """A failed app lifespan startup makes uvicorn return without SystemExit and without binding.
    That must still be reported as "did not start", and never as "listening"."""

    @asynccontextmanager
    async def failing_lifespan(_app):
        raise RuntimeError("lifespan startup failed")
        yield

    monkeypatch.setattr(launcher, "app", FastAPI(lifespan=failing_lifespan))
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        launcher.run_api_server(host="127.0.0.1", port=0)
    assert "Evolve dashboard did not start on http://127.0.0.1:0/" in caplog.text
    assert "listening on" not in caplog.text


def test_sse_launcher_skips_ui_thread(monkeypatch, runtime) -> None:
    thread_called = False
    sse_calls: list[tuple[str, int]] = []

    class FakeThread:
        def __init__(self, target, daemon):
            nonlocal thread_called
            thread_called = True

        def start(self) -> None:
            nonlocal thread_called
            thread_called = True

    monkeypatch.setattr(launcher.threading, "Thread", FakeThread)
    monkeypatch.setattr(launcher, "run_sse_server", lambda host, port: sse_calls.append((host, port)))
    monkeypatch.setattr(launcher.sys, "argv", ["evolve-mcp", "--transport", "sse", "--host", "0.0.0.0", "--port", "9300"])

    launcher.main()
    assert runtime == ["started", "stopped"]

    assert thread_called is False
    assert sse_calls == [("0.0.0.0", 9300)]


@pytest.mark.parametrize(("flag", "expect_notice"), [(["--dashboard"], True), (["--no-dashboard"], False), ([], False)])
def test_sse_launcher_says_the_dashboard_flag_is_stdio_only(monkeypatch, runtime, caplog, flag, expect_notice) -> None:
    sse_calls: list[tuple[str, int]] = []
    monkeypatch.setattr(launcher.threading, "Thread", lambda *a, **k: pytest.fail("SSE must not start the dashboard thread"))
    monkeypatch.setattr(launcher, "run_sse_server", lambda host, port: sse_calls.append((host, port)))
    monkeypatch.setattr(launcher.sys, "argv", ["evolve-mcp", "--transport", "sse", *flag])
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        launcher.main()
    assert sse_calls == [("127.0.0.1", 8201)]
    assert ("--dashboard applies to the stdio transport only" in caplog.text) is expect_notice


def test_run_sse_server_uses_resilient_http_transport(monkeypatch) -> None:
    resilient_app = object()
    captured: list[dict] = []
    warmup_calls: list[bool] = []

    monkeypatch.setattr(launcher, "create_resilient_sse_app", lambda server: resilient_app)
    monkeypatch.setattr(launcher, "warmup_mcp_runtime", lambda: warmup_calls.append(True))
    monkeypatch.setenv("EVOLVE_MCP_WARMUP", "true")
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda app, **kwargs: captured.append({"app": app, **kwargs}),
    )

    launcher.run_sse_server(host="127.0.0.1", port=8201)

    assert len(captured) == 1
    assert captured[0]["app"] is resilient_app
    assert captured[0]["host"] == "127.0.0.1"
    assert captured[0]["port"] == 8201
    assert captured[0]["lifespan"] == "on"
    assert captured[0]["timeout_graceful_shutdown"] == 3
    assert captured[0]["ws"] == "websockets-sansio"
    assert warmup_calls == [True]


def test_run_sse_server_skips_warmup_when_disabled(monkeypatch) -> None:
    warmup_calls: list[bool] = []

    monkeypatch.setattr(launcher, "create_resilient_sse_app", lambda server: object())
    monkeypatch.setattr(launcher, "warmup_mcp_runtime", lambda: warmup_calls.append(True))
    monkeypatch.setenv("EVOLVE_MCP_WARMUP", "false")
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kwargs: None)

    launcher.run_sse_server(host="127.0.0.1", port=8201)

    assert warmup_calls == []


def test_run_sse_server_boots_despite_warmup_failure(monkeypatch) -> None:
    uvicorn_calls: list[bool] = []

    def failing_warmup() -> None:
        raise RuntimeError("warmup exploded")

    monkeypatch.setattr(launcher, "create_resilient_sse_app", lambda server: object())
    monkeypatch.setattr(launcher, "warmup_mcp_runtime", failing_warmup)
    monkeypatch.setenv("EVOLVE_MCP_WARMUP", "true")
    monkeypatch.setattr(launcher.uvicorn, "run", lambda app, **kwargs: uvicorn_calls.append(True))

    launcher.run_sse_server(host="127.0.0.1", port=8201)

    assert uvicorn_calls == [True]
