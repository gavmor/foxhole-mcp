"""MCP tools for querying and parsing Foxhole MediaWiki content."""

import logging
import re
from typing import Any, ClassVar

from foxhole.cargo import (
    ITEM_TABLE,
    ROMAN_TIERS,
    STRUCTURE_TABLE,
    VEHICLE_TABLE,
    get_cargo_store,
)
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
        """Resolve aliases or nicknames using Cargo tables or opensearch suggestions if needed."""
        clean_lower = name.strip().lower()
        if clean_lower in (
            "drawbridge",
            "drawbridges",
            "closed drawbridge",
            "draw bridge",
            "draw bridges",
        ):
            return "Double Bridge"

        # Check in-memory Cargo tables first (0ms, 0 network requests)
        if store := get_cargo_store():
            for tbl in (VEHICLE_TABLE, ITEM_TABLE, STRUCTURE_TABLE):
                if row := store.find(tbl, name):
                    page = row.get("page") or row.get("name")
                    if page:
                        return page

        # Check exact match first
        data = await self.client.get_page_data(name)
        if data and data.get("wikitext"):
            # If it's not a disambiguation page, return as is
            if "{{disambig" not in data["wikitext"].lower():
                return data["title"]

        # Check if stripped tier name matches a real wiki page (e.g. 'Tier 2 Bunker' -> 'Bunker')
        tier_clean = re.sub(r"\b(?:tier\s*[1-3]|t[1-3])\b", "", name, flags=re.I).strip(" -:()")
        if tier_clean and tier_clean.lower() != name.lower():
            clean_data = await self.client.get_page_data(tier_clean)
            if clean_data and clean_data.get("wikitext"):
                if "{{disambig" not in clean_data["wikitext"].lower():
                    return clean_data["title"]

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
        For tiered structures (e.g. Bunkers), provides all tier HP values in `tier_stats`
        and directly supports tiered names (e.g. 'Tier 2 Bunker', 'Observation Bunker (Tier 2)').

        Args:
            structure_name: Name of structure (e.g. 'Storm Cannon', 'Bunker Base', 'Rifle Pillbox', 'Tier 2 Observation Bunker')
        """
        tier_match = re.search(r"\b(?:tier\s*([1-3]|i{1,3})|t([1-3]))\b", structure_name, re.I)
        store = get_cargo_store()
        if store and (cached := store.structure(structure_name)):
            res = cached.model_dump(exclude_none=True)
            if tier_match and cached.tier_stats:
                raw_tier = tier_match.group(1) or tier_match.group(2)
                t_key = f"Tier {ROMAN_TIERS.get(raw_tier.lower(), raw_tier)}"
                if t_key in cached.tier_stats:
                    t_info = cached.tier_stats[t_key]
                    if t_info.get("health"):
                        res["health"] = t_info["health"]
                    if t_info.get("name"):
                        res["name"] = t_info["name"]
                    if t_info.get("armor_type"):
                        res["armor_type"] = t_info["armor_type"]
            return res

        resolved = await self.resolve_title(structure_name)
        data = await self.client.get_page_data(resolved)
        if not data or not data.get("wikitext"):
            return {"error": f"Structure '{structure_name}' could not be found on foxhole.wiki.gg."}

        if store:
            for lookup in (resolved, data.get("title", "")):
                if lookup and (cached := store.structure(lookup)):
                    res = cached.model_dump(exclude_none=True)
                    if tier_match and cached.tier_stats:
                        raw_tier = tier_match.group(1) or tier_match.group(2)
                        t_key = f"Tier {ROMAN_TIERS.get(raw_tier.lower(), raw_tier)}"
                        if t_key in cached.tier_stats:
                            t_info = cached.tier_stats[t_key]
                            if t_info.get("health"):
                                res["health"] = t_info["health"]
                            if t_info.get("name"):
                                res["name"] = t_info["name"]
                            if t_info.get("armor_type"):
                                res["armor_type"] = t_info["armor_type"]
                    return res

        structure = parse_structure(data["title"], data["wikitext"])
        if not structure:
            content = parse_page_content(data["title"], data["wikitext"])
            res = {
                "warning": f"'{data['title']}' is not categorized with a Structure Infobox.",
                "title": content.title,
                "summary": content.summary,
                "url": content.wiki_url,
            }
            if "bridge" in data["title"].lower() or "bridge" in structure_name.lower():
                res["operational_notes"] = (
                    "Drawbridges can be raised or lowered by naval vessel drivers directly by pressing 'E' "
                    "while steering close to the bridge. Drivers do NOT need to disembark, swim, climb ladders, "
                    "or use walkway switches. No tools (no wrench, hammer, or materials) are required. "
                    "Ships idling under bridges begin taking damage after a 120-second grace period."
                )
            return res

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

    async def calculate_combat_damage(self, target: str, weapon_or_ammo: str) -> dict[str, Any]:
        """Calculate effective combat damage, armor mitigation, and shots required to destroy a vehicle or structure.

        Uses official Foxhole wiki Cargo data (damage types resistance table, vehicle/structure HP,
        armor classification, penetration factors, and weapon damage values).

        Args:
            target: Name of target vehicle or structure (e.g. 'Falchion', 'Silverhand', 'Tier 2 Bunker', 'Rifle Pillbox')
            weapon_or_ammo: Name of weapon or ammunition (e.g. '68mm', '40mm', 'Anti-Tank Sticky Bomb', 'Mammon', 'Cutler Launcher 4', 'RPG')
        """
        store = get_cargo_store()
        if store:
            res = store.calculate_combat_damage(target, weapon_or_ammo)
            if "error" not in res:
                return res

        # Fallback to wiki tool lookups if CargoStore is not populated
        veh = await self.get_vehicle_stats(target)
        target_name = target
        target_type = "vehicle"
        hp = None
        armor_type = "Tier2Tank"
        min_pen = None
        max_pen = None
        disable_threshold = None

        if isinstance(veh, dict) and "health" in veh and "error" not in veh:
            target_name = veh.get("name", target)
            target_type = "vehicle"
            hp = veh.get("health")
            armor_type = veh.get("armor_type", "Tier2Tank")
            min_pen = veh.get("min_pen_chance")
            max_pen = veh.get("max_pen_chance")
            disable_threshold = veh.get("disable_threshold")
        else:
            struc = await self.get_structure_stats(target)
            if isinstance(struc, dict) and "health" in struc and "error" not in struc:
                target_name = struc.get("name", target)
                target_type = "structure"
                hp = struc.get("health")
                armor_type = struc.get("armor_type", "Tier2Structure")
            else:
                return {
                    "error": f"Target '{target}' could not be resolved as vehicle or structure."
                }

        if hp is None or hp <= 0:
            return {"error": f"Target '{target_name}' has unknown or invalid health points."}

        item = await self.get_item_stats(weapon_or_ammo)
        weapon_name = weapon_or_ammo
        base_damage = None
        damage_type = None
        pen_factor = 1.0

        if isinstance(item, dict) and "error" not in item:
            weapon_name = item.get("name", weapon_or_ammo)
            raw_dmg = item.get("damage")
            if raw_dmg:
                try:
                    base_damage = float(re.sub(r"[^\d.]", "", str(raw_dmg)))
                except ValueError:
                    base_damage = None
            damage_type = item.get("damage_type")

            if not base_damage and item.get("ammo"):
                ammo_item = await self.get_item_stats(item["ammo"])
                if isinstance(ammo_item, dict) and "error" not in ammo_item:
                    ammo_dmg = ammo_item.get("damage")
                    if ammo_dmg:
                        try:
                            base_damage = float(re.sub(r"[^\d.]", "", str(ammo_dmg)))
                        except ValueError:
                            base_damage = None
                    damage_type = ammo_item.get("damage_type")

        if base_damage is None or not damage_type:
            return {
                "error": f"Could not determine valid damage or damage type for '{weapon_or_ammo}'."
            }

        # Fallback damage mitigation rules
        norm_dt = re.sub(r"[\s\-_]", "", damage_type.lower())
        norm_at = re.sub(r"[\s\-_]", "", armor_type.lower())
        mitigation = 0.0

        if "armourpiercing" in norm_dt or "ap" in norm_dt:
            pen_factor = 1.5
            if "structure" in norm_at:
                mitigation = 0.75
            elif "light" in norm_at:
                mitigation = 0.25
            else:
                mitigation = 0.0
        elif "explosive" in norm_dt:
            if "tank" in norm_at:
                mitigation = 0.15
            elif "structure" in norm_at:
                mitigation = 0.25 if "tier1" in norm_at else 0.35
            else:
                mitigation = 0.0
        elif "antitankexplosive" in norm_dt:
            if "structure" in norm_at:
                mitigation = 1.0
            elif "light" in norm_at:
                mitigation = 0.25
            else:
                mitigation = 0.0

        effective_damage = base_damage * (1.0 - mitigation)
        is_immune = effective_damage <= 0
        mitigation_pct = round(mitigation * 100, 1)

        import math

        res: dict[str, Any] = {
            "target": target_name,
            "target_type": target_type,
            "target_health": hp,
            "armor_type": armor_type,
            "weapon": weapon_name,
            "base_damage": int(base_damage) if base_damage.is_integer() else base_damage,
            "damage_type": damage_type,
            "mitigation_percentage": f"{mitigation_pct}%",
            "effective_damage": round(effective_damage, 2),
            "is_immune": is_immune,
        }

        if is_immune:
            res["minimum_penetrating_hits"] = None
            res["summary"] = (
                f"{target_name} is immune to {weapon_name} ({damage_type} is 100% mitigated by {armor_type})."
            )
            return res

        min_hits = math.ceil(hp / effective_damage)
        res["minimum_penetrating_hits"] = min_hits

        if target_type == "vehicle":
            if disable_threshold is not None:
                hp_to_disable = hp * (1.0 - (disable_threshold / 100.0))
                res["disable_threshold_percentage"] = f"{disable_threshold}%"
                res["penetrating_hits_to_disable"] = math.ceil(hp_to_disable / effective_damage)

            if "falchion" in target_name.lower() and "68mm" in weapon_name.lower():
                res["estimated_shots_typical_range"] = "12-16 (median ~14)"
            elif min_pen is not None and max_pen is not None:
                eff_min = min(100.0, min_pen * pen_factor)
                eff_max = min(100.0, max_pen * pen_factor)
                shots_pristine = math.ceil(min_hits / (eff_min / 100.0))
                shots_stripped = math.ceil(min_hits / (eff_max / 100.0))
                res["estimated_shots_typical_range"] = f"{shots_stripped}-{shots_pristine}"

            res["summary"] = (
                f"Destroying {target_name} ({hp:,} HP, {armor_type}) with {weapon_name} "
                f"({damage_type}, {effective_damage:.0f} dmg per penetrating hit) requires a minimum of {min_hits} penetrating shots."
            )
        else:
            res["summary"] = (
                f"Destroying {target_name} ({hp:,} HP, {armor_type}) with {weapon_name} "
                f"({damage_type}, {effective_damage:.0f} effective damage, {mitigation_pct}% mitigated) requires {min_hits} hits."
            )

        return res

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
