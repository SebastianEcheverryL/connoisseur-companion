"""Quality checks for the shipped datasets."""
import json
from collections import Counter

import pytest

from connoisseur.config import RESTAURANT_DATA_PATH, REVIEW_DATA_PATH
from connoisseur.data_management import Restaurant


@pytest.fixture(scope="module")
def restaurants():
    return json.loads(RESTAURANT_DATA_PATH.read_text(encoding="utf-8"))


def test_every_restaurant_matches_schema(restaurants):
    for record in restaurants:
        Restaurant.model_validate({k: v for k, v in record.items() if k != "itemId"})


def test_item_ids_are_unique(restaurants):
    duplicated = [i for i, n in Counter(r["itemId"] for r in restaurants).items() if n > 1]
    assert not duplicated


def test_ratings_and_prices_are_in_range(restaurants):
    for r in restaurants:
        assert r["rating"] is None or 0 <= r["rating"] <= 5, r["name"]
        assert r["price_range"] is None or 1 <= r["price_range"] <= 4, r["name"]


def test_reviews_point_to_existing_restaurants(restaurants):
    ids = {r["itemId"] for r in restaurants}
    reviews = json.loads(REVIEW_DATA_PATH.read_text(encoding="utf-8"))
    assert all(review["itemId"] in ids for review in reviews)
