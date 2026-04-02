# Docker MCP Server Testing Guide

This guide explains how to manually build and test the Evolve MCP server Docker images.

## Prerequisites

- Docker or Podman installed and running
- OpenAI API key (or compatible LLM API key)

## Building the Image

```bash
docker build -f Dockerfile.core -t evolve-mcp:test .
```
## Testing the MCP Server

```bash
cp .env.example .env
# Populate the .env file with your API keys and any other configs
docker run -i --rm \
  -e EVOLVE_BACKEND=filesystem \
  -v $(pwd)/evolve-data:/app/.evolve \
  --env-file .env evolve-mcp:test
```

You should see output like:
```
╭──────────────────────────────────────────────────────────────────────────────╮
│                         ▄▀▀ ▄▀█ █▀▀ ▀█▀ █▀▄▀█ █▀▀ █▀█                        │
│                         █▀  █▀█ ▄▄█  █  █ ▀ █ █▄▄ █▀▀                        │
│                                FastMCP 3.1.0                                 │
│                    🖥  Server:      entities, 3.1.0                           │
╰──────────────────────────────────────────────────────────────────────────────╯

INFO     Starting MCP server 'entities' with transport 'stdio'
```
### 3. Test with MCP Client

To test the server with an actual MCP client, add it to your MCP configuration:

```json
{
  "mcpServers": {
    "evolve": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "OPENAI_API_KEY",
        "evolve-mcp:test"
      ]
    }
  }
}
```

Then you can send MCP protocol messages via stdin.