# Claude Code Demo

This guide walks through running Kaizen with Claude Code.

## Prerequisites

- Kaizen MCP server running (see [README.md](README.md))
- [Claude Code](https://code.claude.com/docs/en/overview) installed with credentials configured

## Running the Filesystem MCP Server

The demo uses a filesystem MCP server to give Claude Code access to files:

```bash
uv run demo/filesystem/server.py demo/filesystem --transport sse --port 8202
```

## Running with Claude Code

```bash
(cd demo/workdir && claude)
```

Add the MCP servers if needed:
```bash
claude mcp add --scope local guidelines --transport sse http://localhost:8201/sse
claude mcp add --scope local filesystem --transport sse http://localhost:8202/sse
```

Test with:
```
What states do I have teammates in? Read the list from the states.txt file.
```
