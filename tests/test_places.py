"""OpenStreetMap and Tavily clients against recorded responses (respx), no real network."""
import httpx
import pytest
import respx

from connoisseur.places import osm, tavily
from connoisseur.schemas import Coordinates
from connoisseur.settings import get_settings

NOMINATIM = {
    "lat": "6.2006",
    "lon": "-75.5636",
    "display_name": "El Poblado, Medellín, Colombia",
    "address": {"city": "Medellín", "state": "Antioquia", "country_code": "co"},
}
PASADENA = {
    "lat": "34.14",
    "lon": "-118.14",
    "display_name": "Pasadena, California, United States",
    "address": {"city": "Pasadena", "state": "California", "country_code": "us"},
}
OVERPASS = {
    "elements": [
        {"type": "node", "id": 1, "lat": 6.2010, "lon": -75.5640,
         "tags": {"name": "Green Bowl", "amenity": "restaurant", "cuisine": "vegan", "diet:vegan": "only"}},
        {"type": "way", "id": 2, "center": {"lat": 6.2100, "lon": -75.5700},
         "tags": {"name": "Burger Hub", "amenity": "fast_food", "cuisine": "burger"}},
        {"type": "node", "id": 3, "lat": 6.2, "lon": -75.56, "tags": {"amenity": "cafe"}},  # no name
    ]
}


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(osm, "_last_nominatim_call", 0.0)
    monkeypatch.setattr(osm, "_overpass_down_until", 0.0)


@respx.mock
async def test_geocode_parses_nominatim():
    respx.get(f"{get_settings().nominatim_url}/search").mock(return_value=httpx.Response(200, json=[NOMINATIM]))
    loc = await osm.geocode("El Poblado, Medellín")
    assert loc.city == "Medellín"
    assert loc.coordinates.lat == pytest.approx(6.2006)
    assert not loc.in_california


@respx.mock
async def test_geocode_detects_california():
    respx.get(f"{get_settings().nominatim_url}/search").mock(return_value=httpx.Response(200, json=[PASADENA]))
    assert (await osm.geocode("Pasadena")).in_california


@respx.mock
async def test_geocode_returns_none_on_error():
    respx.get(f"{get_settings().nominatim_url}/search").mock(return_value=httpx.Response(503))
    assert await osm.geocode("Nowhere") is None


@respx.mock
async def test_find_places_ranks_diet_matches_first_and_skips_unnamed():
    respx.post(get_settings().overpass_urls[0]).mock(return_value=httpx.Response(200, json=OVERPASS))
    places = await osm.find_places(Coordinates(lat=6.2006, lon=-75.5636), cuisines=["vegan"], dietary=["vegan"])
    assert [p.name for p in places] == ["Green Bowl", "Burger Hub"]
    assert places[0].diet_tags == ["vegan"]
    assert places[0].source == "osm"
    assert places[1].url == "https://www.openstreetmap.org/way/2"


@respx.mock
async def test_find_places_falls_back_to_next_server_when_busy():
    first, second = get_settings().overpass_urls[:2]
    respx.post(first).mock(return_value=httpx.Response(502))
    respx.post(second).mock(return_value=httpx.Response(200, json=OVERPASS))
    places = await osm.find_places(Coordinates(lat=6.2, lon=-75.56))
    assert len(places) == 2


async def test_tavily_disabled_without_key():
    assert not tavily.is_enabled()
    assert await tavily.search("vegan medellin") == []


PHOTON = {
    "features": [
        {"geometry": {"coordinates": [-75.5815, 6.2445]},
         "properties": {"name": "Sushi House", "osm_type": "N", "osm_id": 10, "osm_value": "restaurant",
                        "district": "Laureles-Estadio", "city": "Medellín"}},
        {"geometry": {"coordinates": [-74.1, 4.6]},  # Bogotá: outside the radius, must be dropped
         "properties": {"name": "Sushi Far", "osm_type": "N", "osm_id": 11, "osm_value": "restaurant"}},
        {"geometry": {"coordinates": [-75.58, 6.245]},
         "properties": {"name": "Café Central", "osm_type": "W", "osm_id": 12, "osm_value": "cafe"}},
    ]
}


@respx.mock
async def test_find_places_falls_back_to_photon_when_overpass_is_down():
    for url in get_settings().overpass_urls:
        respx.post(url).mock(side_effect=httpx.ReadTimeout("slow"))
    photon = respx.get(get_settings().photon_url).mock(return_value=httpx.Response(200, json=PHOTON))
    places = await osm.find_places(Coordinates(lat=6.2442, lon=-75.5812), cuisines=["sushi"])
    assert photon.called
    assert photon.calls[0].request.url.params["q"] == "sushi"
    assert [p.name for p in places] == ["Sushi House", "Café Central"]  # name match ranks first
    assert places[0].id == "osm:node/10"
    assert places[1].url == "https://www.openstreetmap.org/way/12"


@respx.mock
async def test_find_places_returns_empty_when_every_source_fails():
    for url in get_settings().overpass_urls:
        respx.post(url).mock(return_value=httpx.Response(504))
    respx.get(get_settings().photon_url).mock(return_value=httpx.Response(500))
    assert await osm.find_places(Coordinates(lat=6.2, lon=-75.5)) == []


@respx.mock
async def test_overpass_circuit_breaker_skips_servers_after_failure():
    routes = [respx.post(url).mock(return_value=httpx.Response(504)) for url in get_settings().overpass_urls]
    respx.get(get_settings().photon_url).mock(return_value=httpx.Response(200, json=PHOTON))
    await osm.find_places(Coordinates(lat=6.2442, lon=-75.5812), cuisines=["sushi"])
    calls_after_first = sum(r.call_count for r in routes)
    await osm.find_places(Coordinates(lat=6.25, lon=-75.59), cuisines=["sushi"])
    assert sum(r.call_count for r in routes) == calls_after_first  # second search went straight to Photon
