"""Base class for MCP tool providers that auto-registers public methods."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


class BaseToolProvider:
    """Base class for MCP tool providers that auto-registers public methods."""

    EXCLUDED_METHODS: ClassVar[set[str]] = {"register", "close"}

    def register(self, server: MCPServer) -> None:
        """Auto-register all public bound methods with the given MCP server.

        Uses inspect.getmembers to discover all public bound methods (excluding
        private methods starting with '_' and items in EXCLUDED_METHODS),
        then registers each as an MCP tool via server.add_tool().
        """
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if name.startswith("_") or name in self.EXCLUDED_METHODS:
                continue
            server.add_tool(method)
