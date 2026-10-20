"""Persistent ChromaDB collections for restaurants, recipes and recipe images."""
from functools import lru_cache
from pathlib import Path

from connoisseur.retrieval import embeddings
from connoisseur.retrieval.fusion import Hit, to_similarity
from connoisseur.settings import get_settings

RESTAURANTS = "restaurant_articles"
RECIPES = "recipe_texts"
IMAGES = "food_images"
COLLECTION_METADATA = {"hnsw:space": "cosine"}


class VectorStore:
    def __init__(self, path: Path):
        import chromadb

        self.path = path
        self.client = chromadb.PersistentClient(path=str(path))

    def collection(self, name: str):
        return self.client.get_or_create_collection(name, metadata=COLLECTION_METADATA)

    def count(self, name: str) -> int:
        return self.collection(name).count()

    def _query(self, name: str, vector, k: int, where: dict | None, key_field: str) -> list[Hit]:
        col = self.collection(name)
        if col.count() == 0:
            return []
        res = col.query(
            query_embeddings=[vector.tolist()],
            n_results=min(k, col.count()),
            where=where or None,
            include=["documents", "metadatas", "distances"],
        )
        ids, docs = res["ids"][0], res["documents"][0]
        metas, sims = res["metadatas"][0], to_similarity(res["distances"][0])
        return [
            Hit(key=str((meta or {}).get(key_field, id_)), similarity=float(sim), document=doc or "",
                metadata=dict(meta or {}))
            for id_, doc, meta, sim in zip(ids, docs, metas, sims, strict=True)
        ]

    def search_restaurants(self, query: str, k: int = 10, where: dict | None = None) -> list[Hit]:
        return self._query(RESTAURANTS, embeddings.embed_texts([query])[0], k, where, "item_id")

    def search_recipes(self, query: str, k: int = 10, where: dict | None = None) -> list[Hit]:
        return self._query(RECIPES, embeddings.embed_texts([query])[0], k, where, "recipe_id")

    def search_images(self, query: str, k: int = 10, where: dict | None = None) -> list[Hit]:
        return self._query(IMAGES, embeddings.embed_query_clip_text(query), k, where, "recipe_id")


@lru_cache
def get_store() -> VectorStore | None:
    """Return the store, or None when the index has not been built (run scripts/build_index.py)."""
    path = get_settings().chroma_dir
    if not path.exists():
        return None
    return VectorStore(path)
