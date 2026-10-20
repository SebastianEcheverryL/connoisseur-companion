"""High-level retrieval over the local dataset: user context in, ranked candidates out."""
from connoisseur import data
from connoisseur.observability import traced
from connoisseur.retrieval.fusion import fuse
from connoisseur.retrieval.store import get_store
from connoisseur.schemas import PlaceCandidate, RecipeCandidate, UserContext
from connoisseur.settings import get_settings

BUDGET_TO_PRICE = {"$": 1, "$$": 2, "$$$": 3, "$$$$": 4}


def build_query(ctx: UserContext) -> str:
    """Prefer the agent's own search query; otherwise compose one from the extracted preferences."""
    if ctx.search_query.strip():
        return ctx.search_query
    parts = [*ctx.cuisines, *ctx.dietary, ctx.vibe or "", ctx.occasion or ""]
    return " ".join(p for p in parts if p).strip() or "great restaurant"


def _location_filter(location: str | None) -> dict | None:
    """Filter by location only when it names a neighbourhood that exists in the dataset."""
    if not location:
        return None
    known = {r.get("location", "").lower(): r.get("location") for r in data.load_restaurants()}
    for part in [location, *location.split(",")]:
        match = known.get(part.strip().lower())
        if match:
            return {"location": match}
    return None


@traced("retrieve_restaurants", as_type="retriever")
def search_restaurants(ctx: UserContext, k: int = 8) -> list[PlaceCandidate]:
    store = get_store()
    if store is None:
        return []
    query = build_query(ctx)
    hits = store.search_restaurants(query, k=k * 2, where=_location_filter(ctx.location))
    by_id = data.restaurants_by_id()
    max_price = BUDGET_TO_PRICE.get(ctx.budget or "")

    candidates, seen_names = [], set()
    for hit in fuse(hits, [], w_text=1.0, w_img=0.0, top_n=None):
        record = by_id.get(int(hit.key))
        # The source text repeats a few restaurants: keep the best-scored entry per name
        if record is None or record["name"].lower() in seen_names:
            continue
        seen_names.add(record["name"].lower())
        if max_price and record.get("price_range") and record["price_range"] > max_price:
            continue
        candidates.append(data.restaurant_to_candidate(record, score=round(hit.fused, 4)))
    return candidates[:k]


@traced("retrieve_recipes", as_type="retriever")
def search_recipes(ctx: UserContext, k: int = 5) -> list[RecipeCandidate]:
    """Multimodal recipe search: MiniLM over recipe text fused with CLIP over recipe images."""
    store = get_store()
    if store is None:
        return []
    s = get_settings()
    query = build_query(ctx)
    fused = fuse(
        store.search_recipes(query, k=k * 2),
        store.search_images(query, k=k * 2),
        w_text=s.fusion_w_text,
        w_img=s.fusion_w_img,
        top_n=k,
    )
    return [
        RecipeCandidate(
            id=f"recipe:{hit.key}",
            name=hit.metadata.get("name", hit.document),
            cuisine=hit.metadata.get("cuisine") or None,
            score=round(hit.fused, 4),
        )
        for hit in fused
    ]
