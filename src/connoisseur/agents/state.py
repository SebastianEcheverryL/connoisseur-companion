"""Shared state of the LangGraph workflow."""
from typing import TypedDict

from connoisseur.memory import CardRef, Profile
from connoisseur.places.tavily import WebSnippet
from connoisseur.schemas import Coordinates, PlaceCandidate, RecipeCandidate, ResolvedLocation, UserContext


class ChatState(TypedDict, total=False):
    # Input
    message: str
    history: list[dict]  # [{"role": "user"|"assistant", "content": str}]
    coords: Coordinates | None  # optional browser geolocation, already rounded
    blocked: bool  # set by the input guardrail

    # Memory (connoisseur.memory): what earlier turns established, loaded before the graph runs
    memory: Profile  # in: the profile so far; out: merged with this message
    last_cards: list[CardRef]  # recommendations of the previous answers, numbered as shown
    home_location: str | None  # the user's saved city, the default when no place is given
    named_location: str | None  # out: a place this message named (becomes the saved city)
    used_home: bool  # out: the saved city filled in for a missing location

    # Phase 1: profile + routing
    context: UserContext
    location: ResolvedLocation | None

    # Phase 2: retrieval (local dataset and/or web)
    local_candidates: list[PlaceCandidate]
    web_candidates: list[PlaceCandidate]
    web_snippets: list[WebSnippet]
    recipes: list[RecipeCandidate]
    notice: str  # a line put before the recommendations ("no reliable web recipe for ajiaco")

    # Phase 3: expert analyses (run in parallel, each writes its own key)
    trend_analysis: str
    style_analysis: str
    nutrition_analysis: str

    # Phase 4: output
    answer: str
    cards: list[dict]
    route: list[str]  # which branches ran, for tracing and evals
    evidence: list[str]  # what the answering agent saw (tool results or candidates), for groundedness checks
