"""Foxhole MCP Server orchestrator combining MediaWiki tools, War API telemetry, and factory optimization."""

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from mcp.server.mcpserver import MCPServer

from foxhole.prompts import register_prompts
from foxhole.telemetry import apply_telemetry_mode, setup_telemetry
from foxhole.tools import (
    DEFAULT_TOOL_PROVIDERS,
    default_dispatches_tools,
    default_production_tools,
    default_war_tools,
    default_wiki_tools,
)

logger = logging.getLogger(__name__)


class ServerExtension(Protocol):
    """Protocol for components registering tools, prompts, or capabilities with an MCPServer."""

    def register(self, server: MCPServer) -> None: ...


# Default registered extensions (all tool providers + prompt registry)
DEFAULT_EXTENSIONS: tuple[Any, ...] = (
    *DEFAULT_TOOL_PROVIDERS,
    register_prompts,
)


def mount_mcp_server(target: MCPServer, source: MCPServer) -> None:
    """Mount tools, prompts, and resources from a sub-server into the target MCPServer."""
    target._tool_manager._tools.update(source._tool_manager._tools)
    target._prompt_manager._prompts.update(source._prompt_manager._prompts)
    if hasattr(source, "_resource_manager") and hasattr(target, "_resource_manager"):
        target._resource_manager._resources.update(source._resource_manager._resources)
        target._resource_manager._templates.update(source._resource_manager._templates)


def create_server(
    name: str = "foxhole",
    description: str = "Foxhole MCP server combining MediaWiki data with live War API telemetry",
    version: str = "0.2.0",
    extensions: Sequence[Any] | None = None,
    telemetry_mode: str | None = None,
    telemetry: bool | None = None,
    **legacy_kwargs: Any,
) -> MCPServer:
    """Create and configure a Foxhole MCPServer instance with modular extensions.

    Extensions can be `MCPServer` sub-instances, objects implementing `.register(server: MCPServer)`,
    or callables taking `(server: MCPServer)`.
    """
    setup_telemetry(service_name=name, telemetry_mode=telemetry_mode, enabled=telemetry)

    mcp_server = MCPServer(
        name=name,
        description=description,
        version=version,
    )

    if extensions is not None:
        active_extensions = list(extensions)
    else:
        # Build active extensions, allowing legacy kwargs overrides if specified
        wiki = legacy_kwargs.get("wiki_tools", default_wiki_tools)
        war = legacy_kwargs.get("war_tools", default_war_tools)
        dispatches = legacy_kwargs.get("dispatches_tools", default_dispatches_tools)
        production = legacy_kwargs.get("production_tools", default_production_tools)
        prompts = legacy_kwargs.get("prompt_registry", register_prompts)
        active_extensions = [wiki, war, dispatches, production, prompts]

    for ext in active_extensions:
        if isinstance(ext, MCPServer):
            mount_mcp_server(mcp_server, ext)
        elif hasattr(ext, "register") and callable(ext.register):
            ext.register(mcp_server)
        elif callable(ext):
            ext(mcp_server)
        else:
            raise TypeError(
                f"Extension {ext!r} must be an MCPServer, have a .register() method, or be callable"
            )

    apply_telemetry_mode(mcp_server, mode=telemetry_mode, enabled=telemetry)

    return mcp_server


# Default server instance
server = create_server()

# Backward-compatible references
client = default_wiki_tools.client
war_client = default_war_tools.war_client
_resolve_title = default_wiki_tools.resolve_title
_fetch_recipes = default_production_tools.fetch_fn

# ---------------------------------------------------------------------------
# Dynamic Backward Compatibility (PEP 562)
# Avoids maintaining a static re-export list of 30+ functions from child modules.
# ---------------------------------------------------------------------------
_DELEGATE_MODULES = ("foxhole.tools", "foxhole.prompts")


def __getattr__(name: str) -> Any:
    # Direct alias fast-paths
    if name == "_fetch_recipes":
        return default_production_tools.fetch_fn
    if name == "client":
        return default_wiki_tools.client
    if name == "war_client":
        return default_war_tools.war_client
    if name == "_resolve_title":
        return default_wiki_tools.resolve_title

    import importlib

    for mod_name in _DELEGATE_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, name):
                return getattr(mod, name)
        except (ImportError, AttributeError):
            continue

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    attrs = set(globals().keys())
    attrs.update(["_fetch_recipes", "client", "war_client", "_resolve_title"])
    import importlib

    for mod_name in _DELEGATE_MODULES:
        try:
            mod = importlib.import_module(mod_name)
            attrs.update(dir(mod))
        except ImportError:
            pass
    return sorted(attrs)
