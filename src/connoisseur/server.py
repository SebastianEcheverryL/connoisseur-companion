"""MCP server exposing the restaurant dataset as tools and a resource."""
import json

from fastmcp import FastMCP

from connoisseur.config import CULINARY_MAP_PATH, RESTAURANT_DATA_PATH, REVIEW_DATA_PATH

mcp = FastMCP("Connoisseur-Server")


def load_restaurant_data() -> list[dict]:
    """Load the structured restaurant data."""
    with open(RESTAURANT_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_review_data() -> list[dict]:
    """Load the user reviews augmented with image captions."""
    with open(REVIEW_DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_culinary_map() -> str:
    """Return the raw culinary map text, or an empty string if it was not downloaded."""
    if not CULINARY_MAP_PATH.exists():
        return ""
    return CULINARY_MAP_PATH.read_text(encoding="utf-8")


# MCP Resource - raw culinary map
@mcp.resource("culinary-map://california")
def get_culinary_map() -> str:
    """The full raw California Culinary Map text.
    Contains detailed descriptions of 100+ restaurants across California
    including their vibes, cuisines, ratings, and price ranges."""
    text = load_culinary_map()
    if not text:
        return "Culinary map not found. Run `python scripts/download_data.py` first."
    return text


# TOOL 1 - structured search by name
@mcp.tool()
def get_restaurant_info(restaurant_name: str) -> str:
    """Search for a restaurant by name and return its structured details
    including cuisine, rating, price range, and signature dish."""
    restaurants = load_restaurant_data()
    query = restaurant_name.lower().strip()

    matches = []
    for restaurant in restaurants:
        name = restaurant["name"].lower()
        if query in name or name in query:
            matches.append(restaurant)

    if not matches:
        return json.dumps(
            {
                "status": "not_found",
                "message": f"No restaurant found matching '{restaurant_name}'.",
                "suggestion": "Try a partial name like 'Iron' or 'Sakura'.",
            },
            indent=2,
        )

    return json.dumps(
        {"status": "found", "count": len(matches), "results": matches},
        indent=2,
    )


# TOOL 2 - search by vibe
@mcp.tool()
def recommend_by_vibe(vibe: str) -> str:
    """Find restaurants that match a given vibe or atmosphere keyword.
    Searches both structured vibe tags and raw text descriptions.
    Examples of vibe keywords: "moody", "sun-drenched", "romantic"."""
    restaurants = load_restaurant_data()
    vibe_lower = vibe.lower().strip()

    # Pass 1: structured vibe tags and environment descriptions
    structured_matches = []
    for restaurant in restaurants:
        vibe_tag = (restaurant.get("vibe") or "").lower()
        environment = (restaurant.get("environment") or "").lower()

        if vibe_lower in vibe_tag or vibe_lower in environment:
            structured_matches.append(
                {
                    "name": restaurant["name"],
                    "location": restaurant["location"],
                    "food_style": restaurant["food_style"],
                    "rating": restaurant["rating"],
                    "vibe": restaurant["vibe"],
                    "price_range": restaurant["price_range"],
                }
            )

    # Pass 2: raw text paragraphs (skipped when the map was not downloaded)
    text_excerpts = [
        para.strip()[:300]
        for para in load_culinary_map().split("\n\n")
        if para.strip() and vibe_lower in para.lower()
    ]

    return json.dumps(
        {
            "vibe_searched": vibe,
            "structured_matches": structured_matches,
            "raw_text_excerpts": text_excerpts[:5],
        },
        indent=2,
    )


# TOOL 3 - review lookup
@mcp.tool()
def get_review(restaurant_name: str) -> str:
    """Retrieve the full review for a restaurant."""
    reviews = load_review_data()
    query = restaurant_name.lower().strip()

    # Reviews don't store the restaurant name, they link to it through itemId
    names_by_item_id = {r["itemId"]: r["name"] for r in load_restaurant_data()}

    matching_review = None
    for review in reviews:
        name = names_by_item_id.get(review["itemId"], "")
        if query in name.lower():
            matching_review = review
            break

    if not matching_review:
        return json.dumps(
            {
                "status": "not_found",
                "message": f"No review found for restaurant: {restaurant_name}",
            },
            indent=2,
        )

    captions = matching_review.get("image_captions") or []

    return json.dumps(
        {
            "status": "found",
            "restaurant": names_by_item_id[matching_review["itemId"]],
            "reviewer": matching_review["userId"],
            "rating": matching_review["rating"],
            "title": matching_review["title"],
            "review_text": matching_review["text"],
            "image_description": captions[0] if captions else "N/A",
            "visit_date": matching_review.get("date", "N/A"),
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
