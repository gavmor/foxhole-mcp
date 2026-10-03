"""Unit tests for BaseToolProvider reflection-based tool registration."""

from typing import ClassVar
from unittest.mock import MagicMock

from mcp.server.mcpserver import MCPServer

from foxhole.tools.base import BaseToolProvider
from foxhole.tools.dispatches import DispatchesTools
from foxhole.tools.production import ProductionTools
from foxhole.tools.warapi import WarApiTools
from foxhole.tools.wiki import WikiTools


class DummyProvider(BaseToolProvider):
    """Test provider with various method types."""

    EXCLUDED_METHODS: ClassVar[set[str]] = {"register", "close", "custom_skip"}

    def tool_alpha(self) -> str:
        return "alpha"

    def tool_beta(self) -> str:
        return "beta"

    def custom_skip(self) -> str:
        return "skip"

    def _private_method(self) -> str:
        return "private"

    def close(self) -> None:
        pass


def test_base_tool_provider_reflection():
    """Verify BaseToolProvider registers public methods and respects EXCLUDED_METHODS and private prefix."""
    mock_server = MagicMock(spec=MCPServer)
    provider = DummyProvider()

    provider.register(mock_server)

    registered_methods = [call.args[0] for call in mock_server.add_tool.call_args_list]
    registered_names = {m.__name__ for m in registered_methods}

    assert registered_names == {"tool_alpha", "tool_beta"}
    assert "custom_skip" not in registered_names
    assert "_private_method" not in registered_names
    assert "close" not in registered_names
    assert "register" not in registered_names


def test_concrete_providers_inherit_base():
    """Verify production tool provider classes inherit from BaseToolProvider."""
    assert issubclass(WikiTools, BaseToolProvider)
    assert issubclass(WarApiTools, BaseToolProvider)
    assert issubclass(DispatchesTools, BaseToolProvider)
    assert issubclass(ProductionTools, BaseToolProvider)


def test_wiki_tools_registration():
    """Verify WikiTools auto-registers expected wiki tool methods and excludes close/resolve_title."""
    mock_server = MagicMock(spec=MCPServer)
    wiki = WikiTools()

    wiki.register(mock_server)

    registered_names = {call.args[0].__name__ for call in mock_server.add_tool.call_args_list}
    expected = {
        "search_foxhole_wiki",
        "get_vehicle_stats",
        "get_item_stats",
        "get_structure_stats",
        "get_production_cost",
        "get_page_overview",
        "calculate_combat_damage",
        "edit_wiki_page",
    }
    assert registered_names == expected
    assert "resolve_title" not in registered_names
    assert "close" not in registered_names


def test_warapi_tools_registration():
    """Verify WarApiTools auto-registers expected War API tool methods and excludes close."""
    mock_server = MagicMock(spec=MCPServer)
    war = WarApiTools()

    war.register(mock_server)

    registered_names = {call.args[0].__name__ for call in mock_server.add_tool.call_args_list}
    expected = {
        "get_war_status",
        "get_war_casualties",
        "get_active_maps",
        "get_map_intel",
        "get_victory_town_status",
        "get_ingame_time",
        "calibrate_ingame_clock",
    }
    assert registered_names == expected
    assert "close" not in registered_names


def test_dispatches_tools_registration():
    """Verify DispatchesTools auto-registers expected dispatch tool methods."""
    mock_server = MagicMock(spec=MCPServer)
    dispatches = DispatchesTools()

    dispatches.register(mock_server)

    registered_names = {call.args[0].__name__ for call in mock_server.add_tool.call_args_list}
    expected = {
        "get_flash_dispatch",
        "get_propaganda_wire",
    }
    assert registered_names == expected
