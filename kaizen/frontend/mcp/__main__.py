import logging
import sys
import threading
import uvicorn

from kaizen.frontend.mcp.mcp_server import mcp, app

logger = logging.getLogger("kaizen-mcp")


def run_api_server():
    """Run the FastAPI server for UI and API in a background thread."""
    try:
        # We run with log_level="warning" to avoid cluttering stdio for MCP
        uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
    except Exception as e:
        logging.error(f"Failed to start UI server: {e}")


def main():
    """
    Main entry point for the server.
    """
    # Start the HTTP API/UI server in a daemon thread so it dies when the parent dies
    api_thread = threading.Thread(target=run_api_server, daemon=True)
    api_thread.start()

    try:
        # Start FastMCP using stdio (which blocks)
        mcp.run()
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user (KeyboardInterrupt)")
        sys.exit(0)


if __name__ == "__main__":
    main()
