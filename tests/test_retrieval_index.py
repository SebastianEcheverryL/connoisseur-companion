"""Integration test over the real vector index (run `python scripts/build_index.py` first)."""
import pytest

from connoisseur.retrieval import service, store
from connoisseur.retrieval.service import search_recipes, search_restaurants
from connoisseur.schemas import UserContext
from connoisseur.settings import PROJECT_DIR

INDEX = PROJECT_DIR / ".chroma"
pytestmark = [
    pytest.mark.index,
    pytest.mark.skipif(not INDEX.exists(), reason="vector index not built"),
]


@pytest.fixture(autouse=True)
def real_index(monkeypatch):
    monkeypatch.setattr(service, "get_store", lambda: store.VectorStore(INDEX))


def test_restaurant_search_honours_location_and_budget():
    ctx = UserContext(intent="restaurant", search_query="italian", location="Pasadena, CA", budget="$$")
    results = search_restaurants(ctx, k=5)
    assert results
    assert all(r.location == "Pasadena" for r in results)
    assert all((r.price_range or 0) <= 2 for r in results)


def test_restaurant_search_deduplicates_names():
    names = [r.name for r in search_restaurants(UserContext(search_query="sushi"), k=8)]
    assert len(names) == len(set(names))


def test_multimodal_recipe_search_finds_noodles():
    results = search_recipes(UserContext(search_query="cozy noodles"), k=3)
    assert any("noodle" in r.name.lower() or "ramen" in r.name.lower() for r in results)
