"""Retrieval evaluation (no LLM): known-item queries generated from the dataset itself.

Each query is built from one record (a signature dish, a vibe + cuisine, a recipe's ingredients or its
name) and the record it came from is the relevant answer. This measures whether the index finds the
right item, and lets us compare text-only search with the multimodal fusion.
"""
import json
import random
from pathlib import Path

from connoisseur import data
from connoisseur.retrieval.fusion import fuse
from connoisseur.retrieval.store import VectorStore
from connoisseur.settings import get_settings
from evals.metrics import first_relevant_rank, hit_at_k, mean, reciprocal_rank

SEED = 42
SAMPLE = 40
K = 10


def _restaurant_ids_by_name() -> dict[str, set[str]]:
    by_name: dict[str, set[str]] = {}
    for r in data.load_restaurants():
        by_name.setdefault(r["name"].lower(), set()).add(str(r["itemId"]))
    return by_name


def restaurant_cases() -> dict[str, list[dict]]:
    rng = random.Random(SEED)
    restaurants = data.load_restaurants()
    same_name = _restaurant_ids_by_name()  # duplicated descriptions: any copy counts as relevant
    with_signature = [r for r in restaurants if r.get("signatures")]
    with_vibe = [r for r in restaurants if r.get("vibe") and r.get("food_style")]
    return {
        "restaurants_by_signature_dish": [
            {"query": rng.choice(r["signatures"]), "relevant": same_name[r["name"].lower()], "label": r["name"]}
            for r in rng.sample(with_signature, min(SAMPLE, len(with_signature)))
        ],
        "restaurants_by_vibe_and_cuisine": [
            {"query": f"{r['vibe']} {r['food_style']}", "relevant": same_name[r["name"].lower()], "label": r["name"]}
            for r in rng.sample(with_vibe, min(SAMPLE, len(with_vibe)))
        ],
    }


def recipe_cases() -> dict[str, list[dict]]:
    rng = random.Random(SEED)
    recipes = [r for r in data.load_recipes() if r.get("ingredients")]
    sample = rng.sample(recipes, min(SAMPLE, len(recipes)))

    def key_ingredients(recipe: dict) -> str:
        # Drop quantities ("2 cups of"), keep 3 ingredient phrases
        words = [i.split(",")[0] for i in recipe["ingredients"][:6]]
        cleaned = [" ".join(w for w in phrase.split() if not any(ch.isdigit() for ch in w))[-40:] for phrase in words]
        return ", ".join(c for c in cleaned if c)[:120]

    return {
        "recipes_by_ingredients": [
            {"query": key_ingredients(r), "relevant": {str(r["id"])}, "label": r["name"]} for r in sample
        ],
        "recipe_images_by_dish_name": [
            {"query": r["name"], "relevant": {str(r["id"])}, "label": r["name"]} for r in sample
        ],
    }


VISUAL_QUERIES = Path(__file__).parent / "datasets" / "visual_queries.json"


def visual_cases() -> list[dict]:
    """Hand-written descriptions of how 10 dishes look (written while looking at the images, without
    using the dish name). This is the case where image embeddings should help."""
    items = json.loads(VISUAL_QUERIES.read_text(encoding="utf-8"))
    return [{"query": i["query"], "relevant": {str(i["recipe_id"])}, "label": i["label"]} for i in items]


def _score(cases: list[dict], ranker) -> dict:
    ranks = [first_relevant_rank(ranker(c["query"]), c["relevant"]) for c in cases]
    misses = [c["label"] for c, r in zip(cases, ranks, strict=True) if not r or r > 5][:5]
    return {
        "n": len(cases),
        "hit@1": mean([hit_at_k(r, 1) for r in ranks]),
        "hit@5": mean([hit_at_k(r, 5) for r in ranks]),
        "mrr@10": mean([reciprocal_rank(r) if r and r <= K else 0.0 for r in ranks]),
        "examples_missed": misses,
    }


def run() -> dict:
    s = get_settings()
    if not s.chroma_dir.exists():
        raise SystemExit("Vector index not found: run `python scripts/build_index.py` first.")
    store = VectorStore(s.chroma_dir)

    def restaurants(query: str) -> list[str]:
        return [h.key for h in store.search_restaurants(query, k=K)]

    def recipes_text_only(query: str) -> list[str]:
        return [h.key for h in store.search_recipes(query, k=K)]

    def recipes_fused(query: str) -> list[str]:
        hits = fuse(store.search_recipes(query, k=K), store.search_images(query, k=K),
                    w_text=s.fusion_w_text, w_img=s.fusion_w_img, top_n=K)
        return [h.key for h in hits]

    def images_only(query: str) -> list[str]:
        return [h.key for h in store.search_images(query, k=K)]

    rc, pc = restaurant_cases(), recipe_cases()
    results = {
        "restaurants_by_signature_dish": _score(rc["restaurants_by_signature_dish"], restaurants),
        "restaurants_by_vibe_and_cuisine": _score(rc["restaurants_by_vibe_and_cuisine"], restaurants),
        "recipes_by_ingredients (text only)": _score(pc["recipes_by_ingredients"], recipes_text_only),
        f"recipes_by_ingredients (fusion {s.fusion_w_text}/{s.fusion_w_img})":
            _score(pc["recipes_by_ingredients"], recipes_fused),
        "recipe_by_dish_name (text only)": _score(pc["recipe_images_by_dish_name"], recipes_text_only),
        "recipe_by_dish_name (CLIP images only)": _score(pc["recipe_images_by_dish_name"], images_only),
        f"recipe_by_dish_name (fusion {s.fusion_w_text}/{s.fusion_w_img})":
            _score(pc["recipe_images_by_dish_name"], recipes_fused),
        "recipe_by_visual_description (text only)": _score(visual_cases(), recipes_text_only),
        "recipe_by_visual_description (CLIP images only)": _score(visual_cases(), images_only),
        f"recipe_by_visual_description (fusion {s.fusion_w_text}/{s.fusion_w_img})":
            _score(visual_cases(), recipes_fused),
    }
    pc["visual"] = visual_cases()
    return {"metrics": results, "fusion_weight_sweep": weight_sweep(store, pc)}


def weight_sweep(store: VectorStore, pc: dict[str, list[dict]]) -> list[dict]:
    """MRR for each image weight on both query types: picks the fusion weight from data, not intuition."""
    rows = []
    for w_img in (0.0, 0.2, 0.4, 0.6, 0.8):
        def ranker(query: str, w=w_img) -> list[str]:
            hits = fuse(store.search_recipes(query, k=K), store.search_images(query, k=K),
                        w_text=1 - w, w_img=w, top_n=K)
            return [h.key for h in hits]

        rows.append({
            "w_img": w_img,
            "mrr_ingredients": _score(pc["recipes_by_ingredients"], ranker)["mrr@10"],
            "mrr_dish_name": _score(pc["recipe_images_by_dish_name"], ranker)["mrr@10"],
            "mrr_visual_description": _score(pc["visual"], ranker)["mrr@10"],
        })
    return rows
