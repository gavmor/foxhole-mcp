"""MCP tools for querying and parsing Foxhole MediaWiki content."""

import logging
from typing import Any, ClassVar

from foxhole.cargo import get_cargo_store
from foxhole.client import (
    FoxholeWikiClient,
    WikiAuthenticationError,
    WikiEditError,
)
from foxhole.models import ItemStats, WikiEditResult
from foxhole.parser import (
    parse_item,
    parse_page_content,
    parse_structure,
    parse_vehicle,
)
from foxhole.tools.base import BaseToolProvider

logger = logging.getLogger(__name__)


class WikiTools(BaseToolProvider):
    """Encapsulates Foxhole MediaWiki tools and their associated client."""

    EXCLUDED_METHODS: ClassVar[set[str]] = {"register", "close", "resolve_title"}

    def __init__(self, client: FoxholeWikiClient | None = None) -> None:
        self.client = client or FoxholeWikiClient()

    async def close(self) -> None:
        """Close the underlying FoxholeWikiClient."""
        await self.client.close()

    async def resolve_title(self, name: str) -> str:
        """Resolve aliases or nicknames using opensearch suggestions if needed."""
        # Check exact match first
        data = await self.client.get_page_data(name)
        if data and data.get("wikitext"):
            # If it's not a disambiguation page, return as is
            if "{{disambig" not in data["wikitext"].lower():
                return data["title"]

        # Try opensearch for close matches
        suggestions = await self.client.opensearch(name, limit=5)
        if suggestions:
            # Prefer non-disambiguation matches
            for sug in suggestions:
                sug_data = await self.client.get_page_data(sug)
                if (
                    sug_data
                    and sug_data.get("wikitext")
                    and "{{disambig" not in sug_data["wikitext"].lower()
                ):
                    return sug_data["title"]
            return suggestions[0]

        return name

    async def search_foxhole_wiki(self, query: str, limit: int = 5) -> dict[str, Any]:
        """Search the Foxhole wiki (foxhole.wiki.gg) for articles matching the query.

        Args:
            query: Search term (e.g. 'Storm Cannon', 'Devitt', 'Heavy Explosive')
            limit: Maximum number of search results to return (default: 5)
        """
        results = await self.client.search(query, limit=limit)
        if not results:
            return {"query": query, "total_results": 0, "results": []}

        out = [
            {
                "title": r.title,
                "snippet": r.snippet,
                "wordcount": r.wordcount,
                "url": r.url,
            }
            for r in results
        ]
        return {"query": query, "total_results": len(out), "results": out}

    async def get_vehicle_stats(self, vehicle_name: str) -> dict[str, Any]:
        """Get structured specifications for any Foxhole vehicle.

        Extracts hit points, armor rating, min/max penetration chances, subsystem
        vulnerabilities (tracks, turret, fuel tank), crew seats, fuel specs, speeds,
        mounted armaments, and production recipes.

        Args:
            vehicle_name: Name or alias of the vehicle (e.g. 'Silverhand - Mk. IV', 'Dunne Transport', 'Falchion')
        """
        if (store := get_cargo_store()) and (cached := store.vehicle(vehicle_name)):
            return cached.model_dump(exclude_none=True)
        resolved = await self.resolve_title(vehicle_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return {"error": f"Vehicle '{vehicle_name}' could not be found on foxhole.wiki.gg."}

        vehicle = parse_vehicle(data["title"], data["wikitext"])
        if not vehicle:
            content = parse_page_content(data["title"], data["wikitext"])
            return {
                "warning": f"'{data['title']}' is not categorized with a Vehicle Infobox.",
                "title": content.title,
                "summary": content.summary,
                "url": content.wiki_url,
            }

        return vehicle.model_dump(exclude_none=True)

    async def get_item_stats(self, item_name: str) -> dict[str, Any]:
        """Get structured statistics for weapons, equipment, ammunition, and items.

        Extracts damage, ammo compatibility, magazine size, fire rate, ranges,
        encumbrance, crate packaging sizes, and manufacturing costs.

        Args:
            item_name: Name of weapon, tool, or shell (e.g. 'No.2 Loughcaster', '40mm', 'Gas Mask', 'Bishamon')
        """
        if (store := get_cargo_store()) and (cached := store.item(item_name)):
            return cached.model_dump(exclude_none=True)
        resolved = await self.resolve_title(item_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return {"error": f"Item '{item_name}' could not be found on foxhole.wiki.gg."}

        item = parse_item(data["title"], data["wikitext"])
        if not item:
            content = parse_page_content(data["title"], data["wikitext"])
            return {
                "warning": f"'{data['title']}' is not categorized with an Item Infobox.",
                "title": content.title,
                "summary": content.summary,
                "url": content.wiki_url,
            }

        return item.model_dump(exclude_none=True)

    async def get_structure_stats(self, structure_name: str) -> dict[str, Any]:
        """Get structured statistics for fortifications, bases, and world buildings.

        Extracts structure HP, armor tier, decay resistance, repair costs,
        mounted guns/artillery, and construction/upgrade recipes.

        Args:
            structure_name: Name of structure (e.g. 'Storm Cannon', 'Bunker Base', 'Rifle Pillbox')
        """
        if (store := get_cargo_store()) and (cached := store.structure(structure_name)):
            return cached.model_dump(exclude_none=True)
        resolved = await self.resolve_title(structure_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return {"error": f"Structure '{structure_name}' could not be found on foxhole.wiki.gg."}

        structure = parse_structure(data["title"], data["wikitext"])
        if not structure:
            content = parse_page_content(data["title"], data["wikitext"])
            return {
                "warning": f"'{data['title']}' is not categorized with a Structure Infobox.",
                "title": content.title,
                "summary": content.summary,
                "url": content.wiki_url,
            }

        return structure.model_dump(exclude_none=True)

    async def get_production_cost(self, name: str) -> dict[str, Any]:
        """Get manufacturing and logistics recipes for any vehicle, weapon, ammo, or structure.

        Returns the production facility (Garage, Factory, MPF, Small Assembly Station),
        material requirements (Basic Materials, Refined Materials, Assembly Materials),
        cycle times, and crate output.

        Args:
            name: Name of entity to query (e.g. 'Dunne Transport', '40mm', 'Silverhand Chieftain - Mk. VI')
        """
        if store := get_cargo_store():
            for entity_type, find in (
                ("vehicle", store.vehicle),
                ("item", store.item),
                ("structure", store.structure),
            ):
                stats = find(name)
                if stats and stats.production:
                    result: dict[str, Any] = {"name": stats.name, "entity_type": entity_type}
                    if isinstance(stats, ItemStats):
                        result["crate_amount"] = stats.crate_amount or store.crate_capacity(
                            stats.name
                        )
                    result["production_recipes"] = [
                        p.model_dump(exclude_none=True) for p in stats.production
                    ]
                    result["wiki_url"] = stats.wiki_url
                    return {k: v for k, v in result.items() if v is not None}
        resolved = await self.resolve_title(name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return {"error": f"Entity '{name}' could not be found on foxhole.wiki.gg."}

        wikitext = data["wikitext"]
        title = data["title"]

        v = parse_vehicle(title, wikitext)
        if v and v.production:
            return {
                "name": v.name,
                "entity_type": "vehicle",
                "production_recipes": [p.model_dump(exclude_none=True) for p in v.production],
                "wiki_url": v.wiki_url,
            }

        item = parse_item(title, wikitext)
        if item and item.production:
            return {
                "name": item.name,
                "entity_type": "item",
                "crate_amount": item.crate_amount,
                "production_recipes": [p.model_dump(exclude_none=True) for p in item.production],
                "wiki_url": item.wiki_url,
            }

        s = parse_structure(title, wikitext)
        if s and s.production:
            return {
                "name": s.name,
                "entity_type": "structure",
                "production_recipes": [p.model_dump(exclude_none=True) for p in s.production],
                "wiki_url": s.wiki_url,
            }

        return {
            "name": title,
            "message": "No standard production recipes were parsed from this article's infobox.",
            "wiki_url": f"https://foxhole.wiki.gg/wiki/{title.replace(' ', '_')}",
        }

    async def get_page_overview(self, title: str) -> dict[str, Any]:
        """Get clean text overview and section contents for any article on foxhole.wiki.gg.

        Removes wikitext syntax, template noise, and navigation boxes.

        Args:
            title: Title of the wiki page (e.g. 'Logistics', 'Artillery', 'Armor Mechanics')
        """
        data = await self.client.get_page_data(title)
        if not data or not data.get("wikitext"):
            return {"error": f"Article '{title}' could not be found."}

        content = parse_page_content(data["title"], data["wikitext"])
        return content.model_dump(exclude_none=True)

    async def edit_wiki_page(
        self,
        title: str,
        content: str,
        summary: str = "Edited via Foxhole MCP",
        section: str | None = None,
        minor: bool = False,
        bot: bool = False,
        createonly: bool = False,
        nocreate: bool = False,
    ) -> dict[str, Any]:
        """Create or edit a page on foxhole.wiki.gg using the MediaWiki action=edit API.

        Requires wiki authentication credentials (set FOXHOLE_WIKI_USERNAME and
        FOXHOLE_WIKI_PASSWORD environment variables or configure bot credentials).
        Automatically handles CSRF token negotiation, refresh on expiry, and cache
        invalidation.

        Args:
            title: Title of the wiki page to create or edit (e.g. 'Logistics', 'User:MyBot/Sandbox')
            content: Wikitext content to write to the page or specified section
            summary: Edit summary explaining the change (default: 'Edited via Foxhole MCP')
            section: Optional section identifier ('new' to append a new section, or integer section index)
            minor: Whether to mark the edit as minor (default: False)
            bot: Whether to mark the edit as a bot edit (default: False)
            createonly: Only create the page; fails if page already exists (default: False)
            nocreate: Only edit existing page; fails if page does not exist (default: False)
        """
        try:
            sec = section if section != "" else None
            edit_data = await self.client.edit(
                title=title,
                text=content,
                summary=summary,
                section=sec,
                minor=minor,
                bot=bot,
                createonly=createonly,
                nocreate=nocreate,
            )

            is_nochange = "nochange" in edit_data or bool(edit_data.get("nochange"))
            page_title = edit_data.get("title", title)
            slug = page_title.replace(" ", "_")
            wiki_url = f"https://foxhole.wiki.gg/wiki/{slug}"

            res = WikiEditResult(
                result=edit_data.get("result", "Success"),
                title=page_title,
                pageid=edit_data.get("pageid"),
                nochange=is_nochange,
                oldrevid=edit_data.get("oldrevid"),
                newrevid=edit_data.get("newrevid"),
                newtimestamp=edit_data.get("newtimestamp"),
                contentmodel=edit_data.get("contentmodel"),
                url=wiki_url,
            )
            return res.model_dump(exclude_none=True)
        except WikiAuthenticationError as e:
            logger.error("Authentication error editing '%s': %s", title, e)
            return {
                "error": str(e),
                "title": title,
                "type": "authentication_error",
            }
        except WikiEditError as e:
            logger.error("MediaWiki edit error on '%s': [%s] %s", title, e.code, e.info)
            return {
                "error": str(e),
                "code": e.code,
                "info": e.info,
                "title": title,
                "type": "edit_error",
            }
        except Exception as e:
            logger.error("Unexpected error editing '%s': %s", title, e)
            return {
                "error": f"Failed to edit wiki page: {e}",
                "title": title,
                "type": "unexpected_error",
            }


# Default singleton instance for convenience
default_wiki_tools = WikiTools()


def __getattr__(name: str):
    """PEP 562: delegate attribute access to the default singleton instance."""
    return getattr(default_wiki_tools, name)


def __dir__():
    """PEP 562: return dir of the default singleton instance merged with module attributes."""
    attrs = set(globals().keys())
    attrs.update(dir(default_wiki_tools))
    return sorted(attrs)
