import logging
import sys

from kaizen.frontend.mcp.mcp_server import mcp

logger = logging.getLogger("kaizen-mcp")


def main():
    """
    Main entry point for the server.
    """
    try:
        mcp.run()
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user (KeyboardInterrupt)")
        sys.exit(0)


if __name__ == "__main__":
    main()
