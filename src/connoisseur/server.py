"""MCP server exposing the restaurant guide, semantic search and nearby-place search as tools."""
from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

from connoisseur import data
from connoisseur.places import osm
from connoisseur.retrieval import service
from connoisseur.schemas import UserContext

mcp = FastMCP("Connoisseur-Server")

ShortText = Annotated[str, Field(min_length=1, max_length=200)]
# Tools the chat agent may call. Every tool here is read-only.
READ_ONLY_TOOLS = (
    "get_restaurant_info",
    "recommend_by_vibe",
    "get_review",
    "semantic_search",
    "search_nearby_restaurants",
)


# Kept for backwards compatibility with the original module API
def load_restaurant_data() -> list[dict]:
    return data.load_restaurants()


def load_review_data() -> list[dict]:
    return data.load_reviews()


def load_culinary_map() -> str:
    return data.load_culinary_map()


# MCP Resource - raw culinary map
@mcp.resource("culinary-map://california")
def get_culinary_map() -> str:
    """The full raw California Culinary Map text.
    Contains detailed descriptions of 100+ restaurants across California
    including their vibes, cuisines, ratings, and price ranges."""
    text = data.load_culinary_map()
    if not text:
        return "Culinary map not found. Run `python scripts/download_data.py` first."
    return text


@mcp.tool(annotations={"readOnlyHint": True})
def get_restaurant_info(restaurant_name: ShortText) -> dict:
    """Search for a restaurant by name and return its structured details
    including cuisine, rating, price range, and signature dish."""
    query = restaurant_name.lower().strip()
    matches = [
        r for r in data.load_restaurants() if query in r["name"].lower() or r["name"].lower() in query
    ]
    if not matches:
        return {
            "status": "not_found",
            "message": f"No restaurant found matching '{restaurant_name}'.",
            "suggestion": "Try a partial name like 'Iron' or 'Sakura'.",
        }
    return {"status": "found", "count": len(matches), "results": matches}


@mcp.tool(annotations={"readOnlyHint": True})
def recommend_by_vibe(vibe: ShortText) -> dict:
    """Find restaurants that match a given vibe or atmosphere keyword.
    Searches both structured vibe tags and raw text descriptions.
    Examples of vibe keywords: "moody", "sun-drenched", "romantic"."""
    vibe_lower = vibe.lower().strip()

    # Pass 1: structured vibe tags and environment descriptions
    structured_matches = [
        {
            "name": r["name"],
            "location": r["location"],
            "food_style": r["food_style"],
            "rating": r["rating"],
            "vibe": r["vibe"],
            "price_range": r["price_range"],
        }
        for r in data.load_restaurants()
        if vibe_lower in (r.get("vibe") or "").lower() or vibe_lower in (r.get("environment") or "").lower()
    ]

    # Pass 2: raw text paragraphs (skipped when the map was not downloaded)
    text_excerpts = [
        para.strip()[:300]
        for para in data.load_culinary_map().split("\n\n")
        if para.strip() and vibe_lower in para.lower()
    ]
    return {
        "vibe_searched": vibe,
        "structured_matches": structured_matches,
        "raw_text_excerpts": text_excerpts[:5],
    }


@mcp.tool(annotations={"readOnlyHint": True})
def get_review(restaurant_name: ShortText) -> dict:
    """Retrieve the full review for a restaurant."""
    query = restaurant_name.lower().strip()
    # Reviews don't store the restaurant name, they link to it through itemId
    names_by_item_id = {r["itemId"]: r["name"] for r in data.load_restaurants()}

    matching_review = next(
        (rv for rv in data.load_reviews() if query in names_by_item_id.get(rv["itemId"], "").lower()), None
    )
    if not matching_review:
        return {"status": "not_found", "message": f"No review found for restaurant: {restaurant_name}"}

    captions = matching_review.get("image_captions") or []
    return {
        "status": "found",
        "restaurant": names_by_item_id[matching_review["itemId"]],
        "reviewer": matching_review["userId"],
        "rating": matching_review["rating"],
        "title": matching_review["title"],
        "review_text": matching_review["text"],
        "image_description": captions[0] if captions else "N/A",
        "visit_date": matching_review.get("date", "N/A"),
    }


@mcp.tool(annotations={"readOnlyHint": True})
def semantic_search(
    query: ShortText,
    kind: Annotated[str, Field(pattern="^(restaurants|recipes)$")] = "restaurants",
    limit: Annotated[int, Field(ge=1, le=10)] = 5,
) -> dict:
    """Semantic search over the California restaurant guide or the recipe collection.
    Recipes are ranked by fusing text similarity with CLIP image similarity.
    Example: query="cozy noodles with warm atmosphere", kind="recipes"."""
    ctx = UserContext(search_query=query)
    if kind == "recipes":
        results = [r.model_dump() for r in service.search_recipes(ctx, k=limit)]
    else:
        results = [r.model_dump(exclude_none=True) for r in service.search_restaurants(ctx, k=limit)]
    if not results:
        return {"status": "not_found", "message": "No results. Has the index been built (scripts/build_index.py)?"}
    return {"status": "found", "count": len(results), "results": results}


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_nearby_restaurants(
    location: ShortText,
    cuisine: Annotated[str | None, Field(max_length=60)] = None,
    dietary: Annotated[str | None, Field(max_length=60)] = None,
) -> dict:
    """Find real restaurants near any place in the world using OpenStreetMap.
    Example: location="El Poblado, Medellín", cuisine="vegan", dietary="vegan"."""
    resolved = await osm.geocode(location)
    if resolved is None:
        return {"status": "not_found", "message": f"Could not find the location '{location}'."}
    places = await osm.find_places(
        resolved.coordinates,
        cuisines=[cuisine] if cuisine else [],
        dietary=[dietary] if dietary else [],
        area_name=resolved.city,
    )
    return {
        "status": "found" if places else "not_found",
        "location": resolved.display_name,
        "results": [p.model_dump(exclude={"coordinates", "score"}, exclude_none=True) for p in places],
        "attribution": "Data © OpenStreetMap contributors (ODbL)",
    }


if __name__ == "__main__":
    mcp.run()
