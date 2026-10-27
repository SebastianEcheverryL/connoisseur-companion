"""Typed contracts shared by the agents, the retrieval layer, the web tools and the API."""
import html
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

Intent = Literal["restaurant", "recipe", "both", "lookup", "clarification", "out_of_scope", "database"]
Source = Literal["dataset", "osm", "web"]


class Coordinates(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

    def rounded(self, decimals: int = 2) -> "Coordinates":
        """Round to ~1 km (2 decimals) so precise user locations never travel further than needed."""
        return Coordinates(lat=round(self.lat, decimals), lon=round(self.lon, decimals))


class UserContext(BaseModel):
    """What the profile agent understood from the conversation."""

    intent: Intent = "clarification"
    language: str = Field(default="English", description="Language the user writes in, e.g. Spanish")
    location: str | None = Field(default=None, description="City or neighbourhood mentioned by the user")
    near_me: bool = Field(default=False, description="User wants places near their current position")
    cuisines: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list, description="e.g. vegan, gluten-free, halal")
    vibe: str | None = Field(default=None, description="Atmosphere, e.g. moody, romantic, quiet")
    budget: Literal["$", "$$", "$$$", "$$$$"] | None = None
    occasion: str | None = None
    restaurant_name: str | None = Field(default=None, description="Specific restaurant the user asks about")
    search_query: str = Field(default="", description="Short semantic search query built from the request")


class ProfileExtraction(BaseModel):
    """What the LLM must return. Every field is required (nullable where it makes sense):
    with schema-constrained decoding, optional fields let small models answer `{}`."""

    intent: Intent
    language: str
    location: str | None
    near_me: bool
    cuisines: list[str]
    dietary: list[str]
    vibe: str | None
    budget: Literal["$", "$$", "$$$", "$$$$"] | None
    occasion: str | None
    restaurant_name: str | None
    search_query: str
    dropped_preferences: list[str] = Field(description="Diet items the user says no longer apply")

    # Models write missing values as words or placeholders: "Mexican null" reached the place search, and
    # Nova Lite answered location="(user's location)" and vibe="&quot;casual&quot;"
    @field_validator("location", "vibe", "budget", "occasion", "restaurant_name", mode="before")
    @classmethod
    def _null_text(cls, value):
        return _clean(value)

    @field_validator("cuisines", "dietary", "dropped_preferences", mode="before")
    @classmethod
    def _clean_list(cls, value):
        if value is None or isinstance(value, str) and _clean(value) is None:
            return []
        items = [value] if isinstance(value, str) else value
        return [text for item in items if isinstance(item, str) and (text := _clean(item))]

    @field_validator("search_query", mode="before")
    @classmethod
    def _clean_query(cls, value):
        if not isinstance(value, str):
            return value
        text = re.sub(r"\([^)]*\)", " ", html.unescape(value))
        return " ".join(word for word in text.split() if _clean(word) is not None)

    @classmethod
    def from_partial(cls, data: dict) -> "ProfileExtraction | None":
        """Recover an answer that omits fields (Bedrock Nova does): fill the gaps with neutral defaults
        instead of dropping the whole profile. A field with an invalid value still fails."""
        known = {key: value for key, value in data.items() if key in PROFILE_DEFAULTS}
        try:
            return cls.model_validate({**PROFILE_DEFAULTS, **known})
        except ValidationError:
            return None

    def to_context(self) -> UserContext:
        return UserContext(**self.model_dump())


NULL_WORDS = {"", "null", "none", "n/a", "unknown", "unspecified"}
# Also template slots: Nova Lite answered restaurant_name="<<Previous recommendations[0].name>>"
PLACEHOLDER = re.compile(r"^\(.*\)$|^<.*>$|^\[.*\]$|^\{.*\}$|\bnot (specified|mentioned|provided|given)\b"
                         r"|\buser'?s (location|preference)|\bprevious recommendations?\b", re.IGNORECASE)

# Same fallback as when the profile call fails outright: treat the message as a restaurant search
PROFILE_DEFAULTS = {
    "intent": "restaurant", "language": "English", "location": None, "near_me": False, "cuisines": [],
    "dietary": [], "vibe": None, "budget": None, "occasion": None, "restaurant_name": None, "search_query": "",
    "dropped_preferences": [],
}


def _clean(value):
    """A model's text value without entities and quotes, or None when it only stands for "nothing"."""
    if not isinstance(value, str):
        return value
    text = html.unescape(value).strip().strip("\"'`“”‘’").strip()
    if text.lower() in NULL_WORDS or PLACEHOLDER.search(text) or not re.search(r"\w", text):
        return None  # also "." and "-", seen as vibe and occasion
    return text


class ResolvedLocation(BaseModel):
    query: str
    display_name: str
    coordinates: Coordinates
    city: str | None = None
    state: str | None = None
    country_code: str | None = None

    @property
    def in_california(self) -> bool:
        return (self.country_code or "").lower() == "us" and (self.state or "").lower() == "california"


class PlaceCandidate(BaseModel):
    """A restaurant from any source, normalised so downstream agents do not care where it came from."""

    id: str
    name: str
    source: Source
    location: str | None = None
    cuisine: str | None = None
    rating: float | None = None
    rating_source: str | None = Field(default=None, description="Site(s) the rating came from, for web places")
    review_count: int | None = None
    review_url: str | None = None
    review_snippet: str | None = None
    price_range: int | None = None
    vibe: str | None = None
    description: str | None = None
    diet_tags: list[str] = Field(default_factory=list)
    signatures: list[str] = Field(default_factory=list, description="Signature dishes (guide places only)")
    coordinates: Coordinates | None = None
    url: str | None = None
    score: float = 0.0


class RecipeCandidate(BaseModel):
    id: str
    name: str
    cuisine: str | None = None
    ingredients: list[str] = Field(default_factory=list)
    score: float = 0.0
    # Per-modality similarity behind the fused score, shown in the UI (never sent to the LLM)
    text_score: float | None = None
    image_score: float | None = None


class WebPlace(BaseModel):
    name: str = Field(description="Restaurant name exactly as written in the search result")
    cuisine: str | None = Field(description="Cuisine if the result says it, otherwise null")


class ResultPlaces(BaseModel):
    # Grouped by result: with a flat list, Nova Lite wrote one name per result and missed the rest
    result: int = Field(description="Number of the search result")
    places: list[WebPlace] = Field(description="Every restaurant this result names")


class WebPlaceExtraction(BaseModel):
    # Required on purpose (see ProfileExtraction): an empty object must not validate
    results: list[ResultPlaces]


class ResultDishes(BaseModel):
    # Grouped by result for the same reason as ResultPlaces
    result: int = Field(description="Number of the search result")
    dishes: list[str] = Field(description="Every dish or drink of this restaurant that the result names")


class DishExtraction(BaseModel):
    # Required on purpose (see ProfileExtraction): an empty object must not validate
    results: list[ResultDishes]


class Recommendation(BaseModel):
    candidate_id: str = Field(description="id of one of the provided candidates")
    reasoning: str = Field(description="2-3 sentences explaining why it fits the user")


class RecommendationSet(BaseModel):
    # Required on purpose (see ProfileExtraction): an empty object must not validate
    summary: str = Field(description="One friendly sentence introducing the picks")
    restaurants: list[Recommendation]
    recipes: list[Recommendation]

    @classmethod
    def from_partial(cls, data: dict) -> "RecommendationSet | None":
        """Nova Lite leaves out the empty list ("recipes" for a restaurant search): fill it in. An answer
        with neither list is still unusable."""
        if "restaurants" not in data and "recipes" not in data:
            return None
        try:
            return cls.model_validate({"summary": "", "restaurants": [], "recipes": [], **data})
        except ValidationError:
            return None
