"""Read-only access to the shipped datasets, cached in memory and reloaded when the file changes."""
import json
from pathlib import Path
from typing import Any

from connoisseur.schemas import PlaceCandidate
from connoisseur.settings import get_settings

_cache: dict[Path, tuple[float, Any]] = {}


def _load_json(path: Path) -> Any:
    mtime = path.stat().st_mtime
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    _cache[path] = (mtime, data)
    return data


def load_restaurants() -> list[dict]:
    return _load_json(get_settings().restaurant_data_path)


def load_reviews() -> list[dict]:
    return _load_json(get_settings().review_data_path)


def load_recipes() -> list[dict]:
    path = get_settings().recipes_path
    return _load_json(path) if path.exists() else []


def load_culinary_map() -> str:
    """Return the raw culinary map text, or an empty string if it was not downloaded."""
    path = get_settings().culinary_map_path
    return path.read_text(encoding="utf-8") if path.exists() else ""


def find_restaurants(name: str) -> list[dict]:
    """Guide restaurants whose name contains the query or is contained in it ("Sakura" -> Sakura Garden)."""
    query = name.lower().strip()
    if not query:
        return []
    return [r for r in load_restaurants() if query in r["name"].lower() or r["name"].lower() in query]


def restaurants_by_id() -> dict[int, dict]:
    return {r["itemId"]: r for r in load_restaurants()}


def restaurant_text(record: dict) -> str:
    """Text that gets embedded for a restaurant: everything a user might search for."""
    parts = [
        f"Restaurant: {record.get('name', '')}",
        f"Cuisine: {record.get('food_style', '')} ({record.get('type', '')})",
        f"Location: {record.get('location', '')}",
        f"Vibe: {record.get('vibe') or ''}",
        f"Environment: {record.get('environment') or ''}",
        f"Signature dishes: {', '.join(record.get('signatures') or [])}",
    ]
    return "\n".join(parts)


def restaurant_to_candidate(record: dict, score: float = 0.0) -> PlaceCandidate:
    return PlaceCandidate(
        id=f"dataset:{record['itemId']}",
        name=record["name"],
        source="dataset",
        location=record.get("location"),
        cuisine=record.get("food_style"),
        rating=record.get("rating"),
        price_range=record.get("price_range"),
        vibe=record.get("vibe"),
        description=record.get("environment"),
        signatures=record.get("signatures") or [],
        score=score,
    )
