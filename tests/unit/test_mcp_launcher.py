import asyncio
import logging
import socket
import threading
import time
import tomllib
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

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


@pytest.mark.parametrize(("dotenv", "expected_started"), [("EVOLVE_DASHBOARD=false\n", False), ("", True)])
def test_empty_dashboard_value_counts_as_unset(monkeypatch, runtime, tmp_path, dotenv, expected_started) -> None:
    """`EVOLVE_DASHBOARD=` in the environment doesn't hide `.env`, and on its own means the default (on)."""
    (tmp_path / ".env").write_text(dotenv)
    monkeypatch.setenv("EVOLVE_DASHBOARD", "")
    assert (_run_stdio(monkeypatch, []) == [launcher.run_api_server]) is expected_started


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


def test_dashboard_logs_the_address_it_bound(caplog) -> None:
    """The listener is announced, with its real address, only once the socket is bound."""

    async def asgi(scope, receive, send):  # minimal app; lifespan is disabled below
        raise RuntimeError("no requests expected")

    server = launcher._DashboardServer(launcher.uvicorn.Config(asgi, host="127.0.0.1", port=0, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    with caplog.at_level(logging.WARNING, logger="evolve-mcp"):
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        server.should_exit = True
        thread.join(10)
    assert server.started
    assert f"listening on http://127.0.0.1:{port}/ without authentication" in caplog.text
    assert "--no-dashboard" in caplog.text


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
