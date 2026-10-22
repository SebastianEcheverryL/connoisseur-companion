"""Rating research with Tavily: regex extraction, name matching, aggregation and re-ranking."""
import httpx
import pytest
import respx
from pydantic import SecretStr

from connoisseur.places import ratings
from connoisseur.places.tavily import WebSnippet
from connoisseur.schemas import PlaceCandidate
from connoisseur.settings import get_settings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Sushi World: 4.5 of 5 bubbles", 4.5),
        ("Rated 4,7/5 by diners", 4.7),
        ("4.2 out of 5 stars", 4.2),
        ("Calificación: 4.3", 4.3),
        ("4 estrellas en Google", 4.0),
        ("Score 8.6/10 on Restaurant Guru", 4.3),
        ("Ranked #12 of 1,234 restaurants in Medellín", None),  # a ranking, not a rating
        ("Ranked #5 of 50", None),
        ("Open since 2019, 300 seats", None),
        ("Rating: 7 (impossible on a 5 scale)", None),
    ],
)
def test_extract_rating(text, expected):
    assert ratings.extract_rating(text) == expected


def test_extract_review_count():
    assert ratings.extract_review_count("4.5 of 5 · 1,234 reviews") == 1234
    assert ratings.extract_review_count("87 opiniones") == 87
    assert ratings.extract_review_count("no count here") is None


def test_site_name():
    assert ratings.site_name("https://www.tripadvisor.com.co/Restaurant_Review-x") == "Tripadvisor"
    assert ratings.site_name("https://es.restaurantguru.com/Sushi-World") == "Restaurant Guru"
    assert ratings.site_name("https://example.org/page") == "example.org"


def snip(url, title, content):
    return WebSnippet(url=url, title=title, content=content)


def test_rating_ignores_snippets_about_other_places():
    snippets = [snip("https://tripadvisor.com/a", "Other Place", "4.9 of 5 bubbles")]
    assert ratings.rating_from_snippets("Sushi World", snippets) is None


def test_rating_averages_one_value_per_site_and_matches_accents():
    snippets = [
        snip("https://www.tripadvisor.com/a", "Café Sushi World, Medellín", "4.5 of 5 bubbles, 320 reviews"),
        snip("https://www.tripadvisor.com/b", "Café Sushi World menu", "3.0 of 5"),  # same site: ignored
        snip("https://restaurantguru.com/x", "Cafe Sushi World", "Rating 4.1 · 1,050 reviews"),
    ]
    result = ratings.rating_from_snippets("Café Sushi World", snippets)
    assert result.rating == 4.3
    assert result.sources == ["Tripadvisor", "Restaurant Guru"]
    assert result.review_count == 1050
    assert result.url == "https://www.tripadvisor.com/a"


@pytest.fixture
def tavily_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "tavily_api_key", SecretStr("test-key"))


def place(name, score):
    return PlaceCandidate(id=f"osm:node/{name}", name=name, source="osm", score=score)


@respx.mock
async def test_enrich_with_ratings_updates_and_reranks(tavily_key):
    def reply(request):
        body = request.content.decode()
        if "Low Rated" in body:
            return httpx.Response(200, json={"results": [
                {"url": "https://yelp.com/l", "title": "Low Rated", "content": "Low Rated: 2.0 out of 5 stars"}]})
        if "Great Spot" in body:
            return httpx.Response(200, json={"results": [
                {"url": "https://tripadvisor.com/g", "title": "Great Spot",
                 "content": "Great Spot 4.8 of 5 bubbles, 900 reviews"}]})
        return httpx.Response(200, json={"results": []})

    route = respx.post(get_settings().tavily_url).mock(side_effect=reply)
    places = [place("Low Rated", 2.0), place("Great Spot", 1.5), place("No Data", 1.0), place("Fourth", 0.5)]
    ranked = await ratings.enrich_with_ratings(places, "Medellín")

    assert route.call_count == get_settings().rating_lookups  # only the top places are researched
    assert route.calls[0].request.headers["authorization"] == "Bearer test-key"
    assert [p.name for p in ranked][:2] == ["Great Spot", "Low Rated"]
    great = ranked[0]
    assert (great.rating, great.rating_source, great.review_count) == (4.8, "Tripadvisor", 900)
    assert next(p for p in ranked if p.name == "No Data").rating is None


async def test_enrich_is_a_no_op_without_tavily_key():
    places = [place("A", 1.0)]
    assert await ratings.enrich_with_ratings(places, "x") == places
    assert places[0].rating is None
