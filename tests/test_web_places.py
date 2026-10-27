"""Place discovery with Tavily: grounding of the names, injection screening and the source order."""
import pytest

from connoisseur.agents import nodes
from connoisseur.places import osm, tavily, web_places
from connoisseur.places.tavily import WebSnippet
from connoisseur.schemas import (
    Coordinates,
    PlaceCandidate,
    ResolvedLocation,
    ResultPlaces,
    UserContext,
    WebPlace,
    WebPlaceExtraction,
)
from connoisseur.security import spotlight
from connoisseur.security.guardrails import GuardrailResult

MEDELLIN = ResolvedLocation(query="Laureles, Medellín", display_name="Laureles, Medellín, Colombia",
                            coordinates=Coordinates(lat=6.24, lon=-75.58), city="Medellín", country_code="co")
CTX = UserContext(intent="restaurant", language="Spanish", location="Laureles, Medellín", cuisines=["sushi"],
                  search_query="casual sushi")
RESULTS = [
    WebSnippet(title="THE 10 BEST Sushi Restaurants in Medellín - Tripadvisor",
               url="https://www.tripadvisor.com/Restaurants-g297478-c38-Medellin.html",
               content="1. Sushi Gozen. 4.8 of 5 bubbles. 2. Kai Sushi Bar, Japanese. 3. Osaka Nikkei 4.9 of 5"),
    WebSnippet(title="Kai Sushi Bar, Medellín - Restaurant reviews", url="https://www.restaurantguru.com/kai",
               content="Kai Sushi Bar: rating 4.6 of 5, 312 reviews. Fresh rolls in Laureles."),
]


def extraction(*items):
    """(name, cuisine, result number) triples, one group per item as the model may return them."""
    return WebPlaceExtraction(results=[ResultPlaces(result=r, places=[WebPlace(name=n, cuisine=c)])
                                       for n, c, r in items])


@pytest.mark.parametrize(("query", "expected"), [
    ("casual sushi", "best casual sushi restaurants in Medellín"),
    ("casual Colombian restaurants in Medellín", "best casual Colombian restaurants in Medellín"),
    ("restaurantes en Medellín", "best restaurants in Medellín"),
])
def test_discovery_query_does_not_repeat_the_city(query, expected):
    assert web_places.discovery_query(CTX.model_copy(update={"search_query": query}), "Medellín") == expected


def test_names_must_appear_in_the_result_they_cite():
    places = web_places.ground_places(
        extraction(("Sushi Gozen", None, 0), ("Invented Sushi Palace", None, 0), ("Kai Sushi Bar", "Japanese", 1)),
        RESULTS, "Medellín", limit=8)
    assert [p.name for p in places] == ["Sushi Gozen", "Kai Sushi Bar"]
    assert all(p.source == "web" and p.location == "Medellín" for p in places)
    assert places[1].cuisine == "Japanese"


def test_site_names_cities_bad_indexes_and_duplicates_are_skipped():
    places = web_places.ground_places(
        extraction(("Tripadvisor", None, 0), ("Medellín", None, 0), ("Sushi Gozen", None, 7),
                   ("Kai Sushi Bar", None, 0), ("KAI sushi bar", None, 1)),
        RESULTS, "Medellín", limit=8)
    assert [p.name for p in places] == ["Kai Sushi Bar"]


def test_rating_comes_only_from_results_about_that_place():
    places = web_places.ground_places(
        extraction(("Osaka Nikkei", None, 0), ("Kai Sushi Bar", None, 0)), RESULTS, "Medellín", limit=8)
    osaka, kai = places
    # The ranking page shows 4.9 next to Osaka Nikkei, but it lists many places: no rating from it
    assert osaka.rating is None
    assert kai.rating == 4.6 and kai.review_count == 312 and kai.rating_source == "Restaurant Guru"


def test_ids_are_stable_and_limit_is_respected():
    first = web_places.ground_places(extraction(("Sushi Gozen", None, 0)), RESULTS, "Medellín", limit=8)
    again = web_places.ground_places(extraction(("Sushi Gozen", None, 0)), RESULTS, "Medellín", limit=8)
    assert first[0].id == again[0].id and first[0].id.startswith("web:")
    many = extraction(("Sushi Gozen", None, 0), ("Kai Sushi Bar", None, 0), ("Osaka Nikkei", None, 0))
    assert len(web_places.ground_places(many, RESULTS, "Medellín", limit=2)) == 2


def test_no_extraction_means_no_places():
    assert web_places.ground_places(None, RESULTS, "Medellín", limit=8) == []


@pytest.fixture
def tavily_results(monkeypatch):
    calls = {"queries": [], "prompts": []}

    async def search(query, max_results=5, include_domains=None, ttl_s=None, max_chars=500):
        calls["queries"].append(query)
        return list(RESULTS)

    async def structured_call(schema, system, user):
        calls["prompts"].append(user)
        return calls.get("answers", {}).get(len(calls["prompts"]), extraction(("Kai Sushi Bar", None, 1)))

    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    monkeypatch.setattr(tavily, "search", search)
    monkeypatch.setattr(nodes, "structured_call", structured_call)
    return calls


async def test_discovery_searches_the_city_and_wraps_results(tavily_results):
    places = await web_places.discover_places(MEDELLIN, CTX)
    assert [p.name for p in places] == ["Kai Sushi Bar"]
    assert tavily_results["queries"] == ["best casual sushi restaurants in Medellín"]
    assert "<untrusted_data" in tavily_results["prompts"][0]


async def test_results_with_injection_never_reach_the_llm(tavily_results, monkeypatch):
    def check(text):  # the second result plays the injected page
        return GuardrailResult(allowed="Fresh rolls" not in text, stage="untrusted_content")

    monkeypatch.setattr(spotlight, "check_untrusted", check)
    await web_places.discover_places(MEDELLIN, CTX)
    assert "Fresh rolls" not in tavily_results["prompts"][0]
    assert "Sushi Gozen" in tavily_results["prompts"][0]


async def test_discovery_is_off_without_tavily(monkeypatch):
    monkeypatch.setattr(tavily, "is_enabled", lambda: False)
    assert await web_places.discover_places(MEDELLIN, CTX) == []


# ---------- order of the sources in the web search agent ----------

OSM_PLACE = PlaceCandidate(id="osm:node/1", name="Green Bowl", source="osm")
WEB_PLACE = PlaceCandidate(id="web:1", name="Kai Sushi Bar", source="web", review_url="https://x.example",
                           review_snippet="Kai Sushi Bar: rating 4.6 of 5")
PHOTON_PLACE = PlaceCandidate(id="osm:node/2", name="Sushi House", source="osm")


@pytest.fixture
def sources(monkeypatch):
    found = {"osm": [], "tavily": [], "photon": []}
    called = []

    async def find_places(*args, photon_fallback=True, **kwargs):
        assert photon_fallback is False  # Photon must come after Tavily
        called.append("osm")
        return list(found["osm"])

    async def discover_places(location, ctx):
        called.append("tavily")
        return list(found["tavily"])

    async def search_photon(*args, **kwargs):
        called.append("photon")
        return list(found["photon"])

    async def enrich(places, area):
        return places

    monkeypatch.setattr(osm, "find_places", find_places)
    monkeypatch.setattr(osm, "search_photon", search_photon)
    monkeypatch.setattr(web_places, "discover_places", discover_places)
    monkeypatch.setattr(tavily, "is_enabled", lambda: True)
    monkeypatch.setattr("connoisseur.places.ratings.enrich_with_ratings", enrich)
    return found, called


async def run_web_search():
    return await nodes.web_search({"context": CTX, "location": MEDELLIN, "route": []})


async def test_osm_places_skip_the_other_sources(sources):
    found, called = sources
    found["osm"] = [OSM_PLACE]
    out = await run_web_search()
    assert called == ["osm"] and out["route"] == ["web", "web:osm", "ratings"]


async def test_tavily_answers_when_osm_has_nothing(sources):
    found, called = sources
    found["tavily"] = [WEB_PLACE]
    out = await run_web_search()
    assert called == ["osm", "tavily"] and out["route"] == ["web", "web:tavily", "ratings"]
    assert out["web_candidates"][0].source == "web"
    assert out["web_snippets"][0].url == "https://x.example"


async def test_photon_is_the_last_resort(sources):
    found, called = sources
    found["photon"] = [PHOTON_PLACE]
    out = await run_web_search()
    assert called == ["osm", "tavily", "photon"] and out["route"] == ["web", "web:photon", "ratings"]


async def test_route_records_when_no_source_has_places(sources):
    out = await run_web_search()
    assert out["route"] == ["web", "web:none"] and out["web_candidates"] == []


def test_the_same_place_named_twice_is_kept_once():
    results = [WebSnippet(title="Top sushi", url="https://restaurantguru.com/sushi",
                          content="ONI Nikkei - sushi medellin Sushi, Restaurant. ONI Nikkei is great.")]
    places = web_places.ground_places(
        extraction(("ONI Nikkei", None, 0), ("ONI Nikkei - sushi medellin", None, 0)), results, "Medellín", limit=8)
    assert [p.name for p in places] == ["ONI Nikkei"]


def test_a_ranking_page_can_name_several_places():
    grouped = WebPlaceExtraction(results=[ResultPlaces(result=0, places=[
        WebPlace(name="Sushi Gozen", cuisine=None), WebPlace(name="Kai Sushi Bar", cuisine="Japanese")])])
    places = web_places.ground_places(grouped, RESULTS, "Medellín", limit=8)
    assert [p.name for p in places] == ["Sushi Gozen", "Kai Sushi Bar"]


async def test_an_answer_without_names_is_retried_once(tavily_results):
    tavily_results["answers"] = {1: WebPlaceExtraction(results=[])}
    places = await web_places.discover_places(MEDELLIN, CTX)
    assert len(tavily_results["prompts"]) == 2 and [p.name for p in places] == ["Kai Sushi Bar"]


async def test_discovery_searches_beyond_review_sites_when_they_have_nothing(tavily_results, monkeypatch):
    domains_seen = []

    async def search(query, max_results=5, include_domains=None, ttl_s=None, max_chars=500):
        domains_seen.append(include_domains)
        return [] if include_domains else list(RESULTS)

    monkeypatch.setattr(tavily, "search", search)
    places = await web_places.discover_places(MEDELLIN, CTX)
    assert domains_seen[0] and domains_seen[1] is None
    assert [p.name for p in places] == ["Kai Sushi Bar"]
