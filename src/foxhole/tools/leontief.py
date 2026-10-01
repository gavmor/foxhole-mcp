"""MCP tool wrapper for Leontief input-output linear solver (backward compatibility module)."""

from mcp.server.mcpserver import MCPServer

from foxhole.tools.production import (
    calculate_required_resources,
    plan_production,
    solve_leontief,
)


def register_leontief_tools(server: MCPServer) -> None:
    """Register Leontief factory optimization tools with the given MCP server."""
    server.add_tool(solve_leontief)


__all__ = [
    "calculate_required_resources",
    "plan_production",
    "register_leontief_tools",
    "solve_leontief",
]
