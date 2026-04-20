from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
import fastmcp
from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.lowlevel.server import LifespanResultT
from mcp.server.sse import SseServerTransport
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Mount, Route

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.http import StarletteWithLifespan, create_base_app

if TYPE_CHECKING:
    from fastmcp.server.server import FastMCP

logger = logging.getLogger("evolve-mcp")


def _is_benign_disconnect_exception(exc: BaseException) -> bool:
    """Return True when an exception only represents a dropped SSE client."""
    if isinstance(exc, (anyio.ClosedResourceError, anyio.BrokenResourceError)):
        return True

    if isinstance(exc, BaseExceptionGroup):
        return len(exc.exceptions) > 0 and all(_is_benign_disconnect_exception(child) for child in exc.exceptions)

    return False


async def _run_sse_session(
    server: FastMCP[LifespanResultT],
    sse: SseServerTransport,
    scope,
    receive,
    send,
) -> bool:
    """Run a FastMCP SSE session and swallow client-disconnect teardown noise."""
    try:
        async with sse.connect_sse(scope, receive, send) as streams:
            await server._mcp_server.run(
                streams[0],
                streams[1],
                server._mcp_server.create_initialization_options(),
            )
    except Exception as exc:
        if _is_benign_disconnect_exception(exc):
            logger.debug("Suppressing benign SSE disconnect during response flush")
            return False
        raise

    return True


def create_resilient_sse_app(
    server: FastMCP[LifespanResultT],
    message_path: str | None = None,
    sse_path: str | None = None,
    auth: AuthProvider | None = None,
    debug: bool = False,
    routes: list[BaseRoute] | None = None,
    middleware=None,
) -> StarletteWithLifespan:
    """Create an SSE app that tolerates disconnect races during teardown."""
    message_path = message_path or fastmcp.settings.message_path
    sse_path = sse_path or fastmcp.settings.sse_path
    auth = auth if auth is not None else server.auth

    server_routes: list[BaseRoute] = []
    server_middleware = list(middleware or [])

    sse = SseServerTransport(message_path)

    async def handle_sse(scope, receive, send) -> Response:
        await _run_sse_session(server, sse, scope, receive, send)
        return Response(status_code=204)

    if auth:
        auth_middleware = auth.get_middleware()
        auth_routes = auth.get_routes(mcp_path=sse_path)
        server_routes.extend(auth_routes)
        server_middleware.extend(auth_middleware)

        resource_url = auth._get_resource_url(sse_path)
        resource_metadata_url = build_resource_metadata_url(resource_url) if resource_url else None

        server_routes.append(
            Route(
                sse_path,
                endpoint=RequireAuthMiddleware(
                    handle_sse,
                    auth.required_scopes,
                    resource_metadata_url,
                ),
                methods=["GET"],
            )
        )
        server_routes.append(
            Mount(
                message_path,
                app=RequireAuthMiddleware(
                    sse.handle_post_message,
                    auth.required_scopes,
                    resource_metadata_url,
                ),
            )
        )
    else:

        async def sse_endpoint(request: Request) -> Response:
            return await handle_sse(request.scope, request.receive, request._send)

        server_routes.append(Route(sse_path, endpoint=sse_endpoint, methods=["GET"]))
        server_routes.append(Mount(message_path, app=sse.handle_post_message))

    if routes:
        server_routes.extend(routes)
    server_routes.extend(server._get_additional_http_routes())

    @asynccontextmanager
    async def lifespan(_app) -> AsyncGenerator[None, None]:
        async with server._lifespan_manager():
            yield

    app = create_base_app(
        routes=server_routes,
        middleware=server_middleware,
        debug=debug,
        lifespan=lifespan,
    )
    app.state.fastmcp_server = server
    app.state.path = sse_path
    app.state.transport_type = "sse"
    return app
