"""Shared state of the LangGraph workflow."""
from typing import TypedDict

from connoisseur.places.tavily import WebSnippet
from connoisseur.schemas import Coordinates, PlaceCandidate, RecipeCandidate, ResolvedLocation, UserContext


class ChatState(TypedDict, total=False):
    # Input
    message: str
    history: list[dict]  # [{"role": "user"|"assistant", "content": str}]
    coords: Coordinates | None  # optional browser geolocation, already rounded

    # Phase 1: profile + routing
    context: UserContext
    location: ResolvedLocation | None

    # Phase 2: retrieval (local dataset and/or web)
    local_candidates: list[PlaceCandidate]
    web_candidates: list[PlaceCandidate]
    web_snippets: list[WebSnippet]
    recipes: list[RecipeCandidate]

    # Phase 3: expert analyses (run in parallel, each writes its own key)
    trend_analysis: str
    style_analysis: str
    nutrition_analysis: str

    # Phase 4: output
    answer: str
    cards: list[dict]
    route: list[str]  # which branches ran, for tracing and evals
