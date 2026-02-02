from fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("LocalTools")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b

if __name__ == "__main__":
    # fastmcp handles stdio/sse execution
    mcp.run()
