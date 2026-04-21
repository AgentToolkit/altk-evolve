import argparse
import logging
import sys
import threading
import uvicorn

from altk_evolve.frontend.mcp.mcp_server import mcp, app
from altk_evolve.frontend.mcp.http_transport import create_resilient_sse_app

logger = logging.getLogger("evolve-mcp")


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
    return parser


def run_api_server():
    """Run the FastAPI server for UI and API in a background thread."""
    try:
        # We run with log_level="warning" to avoid cluttering stdio for MCP
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    except Exception as e:
        logging.error(f"Failed to start UI server: {e}")


def run_sse_server(host: str, port: int) -> None:
    """Run the MCP server over SSE with disconnect-safe transport handling."""
    sse_app = create_resilient_sse_app(mcp)
    uvicorn.run(sse_app, host=host, port=port, log_level="warning")


def main():
    """
    Main entry point for the server.
    """
    args = _build_parser().parse_args()

    try:
        if args.transport == "stdio":
            # Start the HTTP API/UI server in a daemon thread so it dies when the parent dies
            api_thread = threading.Thread(target=run_api_server, daemon=True)
            api_thread.start()
            # Start FastMCP using stdio (which blocks)
            mcp.run()
        else:
            run_sse_server(host=args.host, port=args.port)
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user (KeyboardInterrupt)")
        sys.exit(0)


if __name__ == "__main__":
    main()
