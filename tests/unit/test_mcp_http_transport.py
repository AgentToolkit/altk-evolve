from unittest.mock import MagicMock

import anyio
import pytest
from starlette.routing import Mount, Route

from altk_evolve.frontend.mcp.http_transport import (
    _is_benign_disconnect_exception,
    create_resilient_sse_app,
)
from fastmcp.server.auth.middleware import RequireAuthMiddleware

pytestmark = pytest.mark.unit


def test_closed_resource_disconnect_is_benign() -> None:
    assert _is_benign_disconnect_exception(anyio.ClosedResourceError()) is True


def test_nested_disconnect_group_is_benign() -> None:
    exc = ExceptionGroup(
        "disconnect",
        [
            anyio.ClosedResourceError(),
            ExceptionGroup("nested", [anyio.BrokenResourceError()]),
        ],
    )

    assert _is_benign_disconnect_exception(exc) is True


def test_mixed_exception_group_is_not_benign() -> None:
    exc = ExceptionGroup(
        "mixed",
        [anyio.ClosedResourceError(), RuntimeError("real failure")],
    )

    assert _is_benign_disconnect_exception(exc) is False


def _make_mock_server():
    server = MagicMock()
    server.auth = None
    server._get_additional_http_routes.return_value = []
    server._lifespan_manager = MagicMock()
    return server


def _make_mock_auth(sse_path: str = "/sse"):
    auth = MagicMock()
    auth.required_scopes = ["read"]
    auth.get_middleware.return_value = []
    auth.get_routes.return_value = []
    auth._get_resource_url.return_value = f"http://localhost:8000{sse_path}"
    return auth


def test_create_resilient_sse_app_with_auth_wraps_sse_in_require_auth() -> None:
    server = _make_mock_server()
    auth = _make_mock_auth()

    app = create_resilient_sse_app(server, auth=auth)

    assert app is not None

    sse_routes = [r for r in app.routes if isinstance(r, Route) and r.path == "/sse"]
    assert len(sse_routes) == 1, "Expected exactly one SSE Route at /sse"
    assert isinstance(sse_routes[0].endpoint, RequireAuthMiddleware), "SSE endpoint should be wrapped in RequireAuthMiddleware"


def test_create_resilient_sse_app_with_auth_wraps_message_in_require_auth() -> None:
    server = _make_mock_server()
    auth = _make_mock_auth()

    app = create_resilient_sse_app(server, auth=auth)

    message_mounts = [r for r in app.routes if isinstance(r, Mount) and r.path == "/messages"]
    assert len(message_mounts) == 1, "Expected exactly one Mount at /messages"
    assert isinstance(message_mounts[0].app, RequireAuthMiddleware), "Message mount should be wrapped in RequireAuthMiddleware"


def test_create_resilient_sse_app_with_auth_calls_auth_provider_methods() -> None:
    server = _make_mock_server()
    auth = _make_mock_auth()

    create_resilient_sse_app(server, auth=auth)

    auth.get_middleware.assert_called_once()
    auth.get_routes.assert_called_once_with(mcp_path="/sse")
    auth._get_resource_url.assert_called_once_with("/sse")


def test_create_resilient_sse_app_without_auth_does_not_wrap_endpoints() -> None:
    server = _make_mock_server()

    app = create_resilient_sse_app(server, auth=None)

    sse_routes = [r for r in app.routes if isinstance(r, Route) and r.path == "/sse"]
    assert len(sse_routes) == 1
    assert not isinstance(sse_routes[0].endpoint, RequireAuthMiddleware), "Without auth, SSE endpoint should not be wrapped"

    message_mounts = [r for r in app.routes if isinstance(r, Mount) and r.path == "/messages"]
    assert len(message_mounts) == 1
    assert not isinstance(message_mounts[0].app, RequireAuthMiddleware), "Without auth, message mount should not be wrapped"
