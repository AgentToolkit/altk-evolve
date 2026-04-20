import tomllib
from pathlib import Path

import pytest

from altk_evolve.frontend.mcp import __main__ as launcher

pytestmark = pytest.mark.unit


def test_pyproject_exports_mcp_launcher_script() -> None:
    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    parsed = tomllib.loads(pyproject_path.read_text())

    assert parsed["project"]["scripts"]["evolve-mcp"] == "altk_evolve.frontend.mcp.__main__:main"


def test_stdio_launcher_starts_ui_thread(monkeypatch) -> None:
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

    assert thread_calls[0] == (launcher.run_api_server, True)
    assert thread_calls[1] == ("started", True)
    assert run_calls == [((), {})]


def test_sse_launcher_skips_ui_thread(monkeypatch) -> None:
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

    assert thread_called is False
    assert sse_calls == [("0.0.0.0", 9300)]


def test_run_sse_server_uses_resilient_http_transport(monkeypatch) -> None:
    resilient_app = object()
    captured: list[tuple[object, str, int, str]] = []

    monkeypatch.setattr(launcher, "create_resilient_sse_app", lambda server: resilient_app)
    monkeypatch.setattr(
        launcher.uvicorn,
        "run",
        lambda app, host, port, log_level: captured.append((app, host, port, log_level)),
    )

    launcher.run_sse_server(host="127.0.0.1", port=8201)

    assert captured == [(resilient_app, "127.0.0.1", 8201, "warning")]
