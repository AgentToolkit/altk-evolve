import argparse
import logging
import os
import socket
import sys
import threading
import uvicorn

from altk_evolve.frontend.mcp.mcp_server import app, get_client, mcp
from altk_evolve.frontend.mcp.http_transport import create_resilient_sse_app
from altk_evolve.retention.scheduler import retention_runtime

logger = logging.getLogger("evolve-mcp")

# Where the stdio launcher serves the web UI and REST API.
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8000


def _is_truthy_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def warmup_mcp_runtime() -> None:
    """Pre-initialize MCP backend state to reduce first-tool-call latency."""
    logger.info("Warming up MCP runtime...")
    get_client()
    logger.info("MCP runtime warmup complete")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Evolve MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse"),
        default="stdio",
        help="MCP transport to use (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8201,
        help="Port for SSE transport (default: 8201)",
    )
    parser.add_argument(
        "--dashboard",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            f"With stdio transport, also serve the web UI and REST API on http://{DASHBOARD_HOST}:{DASHBOARD_PORT} "
            "(default: on; EVOLVE_DASHBOARD=false turns it off). The API has no authentication."
        ),
    )
    return parser


def _dashboard_enabled(flag: bool | None) -> bool:
    """The command-line flag wins; otherwise EVOLVE_DASHBOARD, which defaults to on."""
    return flag if flag is not None else _is_truthy_env("EVOLVE_DASHBOARD", True)


class _DashboardServer(uvicorn.Server):
    """Announces the dashboard once its socket is bound, so the log never claims a port it lost."""

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.started:
            bound = [sock.getsockname() for server in self.servers for sock in server.sockets or ()]
            host, port = bound[0][:2] if bound else (self.config.host, self.config.port)
            logger.warning(
                "Evolve dashboard and REST API listening on http://%s:%d/ without authentication: any local process can "
                "read and change memories. Disable with --no-dashboard or EVOLVE_DASHBOARD=false.",
                host,
                port,
            )


def run_api_server(host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT) -> None:
    """Run the FastAPI server for UI and API in a background thread."""
    # log_level="warning" keeps uvicorn's own logging (access logs go to stdout) off the
    # MCP stdio channel.
    server = _DashboardServer(uvicorn.Config(app, host=host, port=port, log_level="warning"))
    try:
        server.run()
    except SystemExit:
        # uvicorn logs the bind error (e.g. the port is in use) and exits; only this thread stops.
        logger.warning("Evolve dashboard did not start on http://%s:%d/. The MCP server keeps running over stdio.", host, port)
    except Exception as e:
        logger.error(f"Failed to start UI server: {e}")


def run_sse_server(host: str, port: int) -> None:
    """Run the MCP server over SSE with disconnect-tolerant teardown."""
    if _is_truthy_env("EVOLVE_MCP_WARMUP", True):
        try:
            warmup_mcp_runtime()
        except Exception as exc:
            # Keep startup resilient: failed warmup should not block server boot.
            logger.warning("MCP warmup failed; continuing without warmup: %s", exc)

    uvicorn.run(
        create_resilient_sse_app(mcp),
        host=host,
        port=port,
        lifespan="on",
        timeout_graceful_shutdown=3,
        ws="websockets-sansio",
    )


def main():
    """
    Main entry point for the server.
    """
    args = _build_parser().parse_args()

    try:
        with retention_runtime(get_client()):
            if args.transport == "stdio":
                if _dashboard_enabled(args.dashboard):
                    # Start the HTTP API/UI server in a daemon thread so it dies when the parent dies
                    api_thread = threading.Thread(target=run_api_server, daemon=True)
                    api_thread.start()
                else:
                    logger.info("Evolve dashboard disabled; serving MCP over stdio only.")
                # Start FastMCP using stdio (which blocks)
                mcp.run()
            else:
                if args.dashboard:
                    logger.warning("--dashboard applies to the stdio transport only; the SSE server does not serve the dashboard.")
                run_sse_server(args.host, args.port)
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user (KeyboardInterrupt)")
        sys.exit(0)


if __name__ == "__main__":
    main()
