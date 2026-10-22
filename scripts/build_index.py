"""Build the ChromaDB index used by the app (replaces notebook 03).

Restaurants come from data/structured_restaurant_data.json (shipped). Recipes and their images
need `python scripts/download_data.py --with-images` first; they are skipped when missing.

Usage: python scripts/build_index.py [--out PATH]
"""
import argparse
import re
import shutil
import time
from pathlib import Path

from connoisseur import data
from connoisseur.retrieval import embeddings
from connoisseur.retrieval.store import IMAGES, RECIPES, RESTAURANTS, VectorStore
from connoisseur.settings import get_settings

IMAGE_DIR_NAME = "synthetic_recipe_images"


def recipe_text(recipe: dict) -> str:
    return (
        f"Recipe: {recipe.get('name', '')}\n"
        f"Cuisine: {recipe.get('cuisine', '')}\n"
        f"Ingredients: {', '.join(recipe.get('ingredients') or [])}"
    )


def image_paths_by_recipe_id(image_dir: Path) -> dict[int, Path]:
    # recipe12.png belongs to the recipe with id 12; match on the number, not on sort order
    paths = {}
    for path in image_dir.rglob("*.png"):
        match = re.search(r"recipe(\d+)", path.name)
        if match:
            paths[int(match.group(1))] = path
    return paths


def upsert(store: VectorStore, name: str, ids, vectors, documents, metadatas) -> None:
    store.collection(name).upsert(
        ids=list(ids), embeddings=vectors.tolist(), documents=list(documents), metadatas=list(metadatas)
    )
    print(f"  {name}: {len(ids)} vectors")


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)  # always rebuild from scratch so reruns are deterministic
    store = VectorStore(out)
    start = time.perf_counter()

    restaurants = data.load_restaurants()
    upsert(
        store,
        RESTAURANTS,
        ids=[f"rest_{r['itemId']}" for r in restaurants],
        vectors=embeddings.embed_texts([data.restaurant_text(r) for r in restaurants]),
        documents=[data.restaurant_text(r) for r in restaurants],
        metadatas=[
            {
                "item_id": r["itemId"],
                "name": r["name"],
                "location": r.get("location") or "",
                "cuisine": r.get("food_style") or "",
                "price_range": r.get("price_range") or 0,
                "source": "restaurant",
            }
            for r in restaurants
        ],
    )

    recipes = data.load_recipes()
    if not recipes:
        print("  recipes: skipped (run scripts/download_data.py to fetch Recipes.json)")
    else:
        upsert(
            store,
            RECIPES,
            ids=[f"recipe_{r['id']}" for r in recipes],
            vectors=embeddings.embed_texts([recipe_text(r) for r in recipes]),
            documents=[recipe_text(r) for r in recipes],
            metadatas=[
                {"recipe_id": r["id"], "name": r["name"], "cuisine": r.get("cuisine") or ""} for r in recipes
            ],
        )

        images = image_paths_by_recipe_id(get_settings().data_dir / IMAGE_DIR_NAME)
        with_image = [r for r in recipes if r["id"] in images]
        if not with_image:
            print("  images: skipped (run scripts/download_data.py --with-images)")
        else:
            upsert(
                store,
                IMAGES,
                ids=[f"img_{r['id']}" for r in with_image],
                vectors=embeddings.embed_images([images[r["id"]] for r in with_image]),
                documents=[r["name"] for r in with_image],
                metadatas=[
                    {
                        "recipe_id": r["id"],
                        "name": r["name"],
                        "cuisine": r.get("cuisine") or "",
                        "image_file": images[r["id"]].name,
                    }
                    for r in with_image
                ],
            )

    print(f"Index built in {time.perf_counter() - start:.1f}s at {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=get_settings().chroma_dir)
    build(parser.parse_args().out)


if __name__ == "__main__":
    main()
