"""Nodes of the recommendation workflow. Each node reads the shared state and returns a partial update."""
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from connoisseur.agents.prompts import load_prompt
from connoisseur.agents.state import ChatState
from connoisseur.llm import get_chat_model, message_text
from connoisseur.log import get_logger
from connoisseur.places import osm, tavily
from connoisseur.retrieval import service
from connoisseur.schemas import PlaceCandidate, ProfileExtraction, RecommendationSet, UserContext
from connoisseur.settings import get_settings

log = get_logger(__name__)

HISTORY_TURNS = 6

# Fixed UI strings in the two languages the demo targets; other languages fall back to English
TEXT = {
    "English": {
        "dataset": "Connoisseur California guide",
        "osm": "OpenStreetMap",
        "web": "Web",
        "restaurants": "Restaurants",
        "recipes": "Recipes",
        "osm_credit": "Place data © OpenStreetMap contributors.",
        "tip": "Tip: tell me your city or neighbourhood and I'll search places near you.",
        "none": "I couldn't find matching places{where}. Could you tell me another area or relax a filter?",
        "near": " near {place}",
        "ask_location": "Happy to find places near you! Where are you? Tell me your city or neighbourhood "
        "(e.g. \"El Poblado, Medellín\") or share your location.",
        "location_not_found": "I couldn't find \"{place}\" on the map. Could you write it another way, "
        "for example \"neighbourhood, city\"?",
        "database": "Editing the restaurant database is not available from the chat. "
        "Maintainers can use `python -m connoisseur.data_management`.",
        "out_of_scope": "I can only help with restaurants, food and recipes. "
        "Try asking for a place to eat in your city or a recipe idea!",
        "help": "Hi! I'm Connoisseur Companion. I can:\n"
        "- Recommend restaurants near you anywhere in the world (tell me your city or neighbourhood)\n"
        "- Search my curated California guide by cuisine, vibe or budget\n"
        "- Suggest recipes for what you feel like cooking\n"
        "- Tell you about a specific restaurant in the guide and its reviews\n\n"
        "Try: *\"a quiet vegan café in El Poblado, Medellín\"* or *\"tell me about Sakura Garden\"*.",
    },
    "Spanish": {
        "dataset": "Guía Connoisseur de California",
        "osm": "OpenStreetMap",
        "web": "Web",
        "restaurants": "Restaurantes",
        "recipes": "Recetas",
        "osm_credit": "Datos de lugares © colaboradores de OpenStreetMap.",
        "tip": "Tip: dime tu ciudad o barrio y busco lugares cerca de ti.",
        "none": "No encontré lugares que coincidan{where}. ¿Me dices otra zona o relajamos algún filtro?",
        "near": " cerca de {place}",
        "ask_location": "¡Con gusto busco lugares cerca de ti! ¿Dónde estás? Dime tu ciudad o barrio "
        "(por ejemplo \"El Poblado, Medellín\") o comparte tu ubicación.",
        "location_not_found": "No encontré \"{place}\" en el mapa. ¿Me lo escribes de otra forma, "
        "por ejemplo \"barrio, ciudad\"?",
        "database": "Editar la base de datos de restaurantes no está disponible desde el chat. "
        "Los mantenedores pueden usar `python -m connoisseur.data_management`.",
        "out_of_scope": "Solo puedo ayudarte con restaurantes, comida y recetas. "
        "¡Pídeme un lugar para comer en tu ciudad o una idea de receta!",
        "help": "¡Hola! Soy Connoisseur Companion. Puedo:\n"
        "- Recomendarte restaurantes cerca de ti en cualquier parte del mundo (dime tu ciudad o barrio)\n"
        "- Buscar en mi guía curada de California por cocina, ambiente o presupuesto\n"
        "- Sugerirte recetas según lo que quieras cocinar\n"
        "- Contarte sobre un restaurante específico de la guía y sus reseñas\n\n"
        "Prueba: *\"un café vegano tranquilo en El Poblado, Medellín\"* o *\"cuéntame de Sakura Garden\"*.",
    },
}


def t(ctx: UserContext | None, key: str) -> str:
    language = ctx.language.strip().capitalize() if ctx else "English"
    return TEXT.get(language, TEXT["English"])[key]


# ---------- helpers ----------

def _transcript(state: ChatState) -> str:
    turns = (state.get("history") or [])[-HISTORY_TURNS:]
    lines = [f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns if t.get("content")]
    lines.append(f"user: {state['message']}")
    return "\n".join(lines)


async def structured_call[T: BaseModel](schema: type[T], system: str, user: str) -> T | None:
    """Ask the model for an instance of `schema`; returns None when the model's output is unusable."""
    try:
        model = get_chat_model(temperature=0).with_structured_output(schema)
        result = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        return result if isinstance(result, schema) else schema.model_validate(result)
    except Exception as exc:  # noqa: BLE001 - small local models fail in many ways; degrade, don't crash
        log.warning("structured_output_failed", schema=schema.__name__, error=str(exc)[:300])
        return None


async def text_call(system: str, user: str, temperature: float | None = None) -> str:
    response = await get_chat_model(temperature=temperature).ainvoke(
        [SystemMessage(content=system), HumanMessage(content=user)]
    )
    return message_text(response.content).strip()


def _candidate_line(c: PlaceCandidate) -> dict:
    return c.model_dump(
        include={"id", "name", "location", "cuisine", "rating", "price_range", "vibe", "description", "diet_tags"},
        exclude_none=True,
    )


def _preferences(ctx: UserContext) -> str:
    return ctx.model_dump_json(exclude={"search_query", "restaurant_name", "intent"}, exclude_none=True)


# ---------- phase 1: profile and routing ----------

async def profile(state: ChatState) -> dict:
    """User Profile Generator + intent router: one structured LLM call."""
    extracted = await structured_call(ProfileExtraction, load_prompt("profile"), _transcript(state))
    if extracted is None:
        # Degrade gracefully: treat the message as a restaurant search
        ctx = UserContext(intent="restaurant", search_query=state["message"][:200])
    else:
        ctx = extracted.to_context()
    if not ctx.search_query:
        ctx.search_query = service.build_query(ctx)
    log.info("profile", intent=ctx.intent, has_location=bool(ctx.location or state.get("coords")))
    return {"context": ctx, "route": [f"intent:{ctx.intent}"]}


def route_intent(state: ChatState) -> str:
    intent = state["context"].intent
    if intent in ("restaurant", "both"):
        return "locate"
    if intent == "recipe":
        return "retrieve_local"
    if intent == "lookup":
        return "tool_agent"
    return "respond"


async def respond(state: ChatState) -> dict:
    """Direct answers that need no retrieval: help text and polite refusals."""
    # Fixed text on purpose: an ungrounded LLM reply here invented restaurants, addresses and phones
    ctx = state["context"]
    key = ctx.intent if ctx.intent in ("database", "out_of_scope") else "help"
    return {"answer": t(ctx, key), "route": state["route"] + [f"canned:{key}"]}


# ---------- phase 2: location and retrieval ----------

async def locate(state: ChatState) -> dict:
    ctx, coords = state["context"], state.get("coords")
    location = None
    # A place named in the conversation wins over the device position ("sushi in Pasadena")
    if ctx.location:
        location = await osm.geocode(ctx.location)
    elif coords:
        location = await osm.reverse_geocode(coords)
    where = "none" if location is None else ("california" if location.in_california else "elsewhere")
    return {"location": location, "route": state["route"] + [f"location:{where}"]}


def route_after_locate(state: ChatState) -> str:
    ctx, location = state["context"], state.get("location")
    if location is None and (ctx.near_me or ctx.location):
        # "near me" without coordinates, or a place the map doesn't know: ask instead of
        # silently answering from the California guide
        return "ask_location"
    if location is not None and not location.in_california:
        return "web_search"  # outside the curated guide's coverage
    return "retrieve_local"


async def ask_location(state: ChatState) -> dict:
    ctx = state["context"]
    if ctx.location:
        answer = t(ctx, "location_not_found").format(place=ctx.location)
    else:
        answer = t(ctx, "ask_location")
    return {"answer": answer, "cards": [], "route": state["route"] + ["ask_location"]}


async def retrieve_local(state: ChatState) -> dict:
    ctx = state["context"]
    restaurants = service.search_restaurants(ctx) if ctx.intent in ("restaurant", "both") else []
    recipes = service.search_recipes(ctx) if ctx.intent in ("recipe", "both") else []
    return {"local_candidates": restaurants, "recipes": recipes, "route": state["route"] + ["local"]}


def route_after_local(state: ChatState) -> str:
    ctx, location = state["context"], state.get("location")
    too_few = len(state.get("local_candidates") or []) < get_settings().min_local_results
    if ctx.intent in ("restaurant", "both") and location is not None and too_few:
        return "web_search"  # in California but the guide has little for this request
    return "experts"


async def web_search(state: ChatState) -> dict:
    """Web Search agent: OpenStreetMap for real places near the user, Tavily for review context.

    It is deliberately deterministic: web content never decides which tool runs next.
    """
    ctx, location = state["context"], state.get("location")
    if location is None:
        return {"web_candidates": [], "web_snippets": [], "route": state["route"] + ["web:skipped"]}

    places = await osm.find_places(
        location.coordinates, cuisines=ctx.cuisines, dietary=ctx.dietary, area_name=location.city,
        query=ctx.search_query,
    )
    snippets = []
    if tavily.is_enabled() and places:
        area = location.city or location.query
        snippets = await tavily.search(f"best {ctx.search_query} restaurants in {area} reviews", max_results=5)
        _attach_snippets(places, snippets)
    log.info("web_search", places=len(places), snippets=len(snippets))
    return {"web_candidates": places, "web_snippets": snippets, "route": state["route"] + ["web"]}


def _attach_snippets(places: list[PlaceCandidate], snippets: list[tavily.WebSnippet]) -> None:
    """Add a review snippet to places whose name appears in a web result."""
    for place in places:
        pattern = re.compile(rf"\b{re.escape(place.name.lower())}\b")
        for snip in snippets:
            if pattern.search(f"{snip.title} {snip.content}".lower()):
                place.description = f"{place.description or ''} | web: {snip.content[:200]}".strip(" |")
                break


# ---------- phase 3: expert analyses (parallel) ----------

def _expert_input(state: ChatState) -> str:
    restaurants = (state.get("local_candidates") or []) + (state.get("web_candidates") or [])
    return (
        f"User preferences: {_preferences(state['context'])}\n"
        f"Restaurants: {json.dumps([_candidate_line(c) for c in restaurants[:6]], ensure_ascii=False)}\n"
        f"Recipes: {json.dumps([r.model_dump(exclude={'score'}) for r in (state.get('recipes') or [])[:5]])}"
    )


def _has_candidates(state: ChatState) -> bool:
    return bool(state.get("local_candidates") or state.get("web_candidates") or state.get("recipes"))


def route_to_experts(state: ChatState) -> list[str] | str:
    if not _has_candidates(state) or not get_settings().enable_expert_analysis:
        return "recommend"
    return ["trend_analyst", "style_expert", "nutrition_expert"]


def _expert(prompt_name: str, state_key: str):
    async def node(state: ChatState) -> dict:
        try:
            return {state_key: await text_call(load_prompt(prompt_name), _expert_input(state))}
        except Exception as exc:  # noqa: BLE001 - one failed expert must not sink the whole answer
            log.warning("expert_failed", expert=prompt_name, error=str(exc)[:200])
            return {state_key: ""}

    node.__name__ = prompt_name
    return node


trend_analyst = _expert("trend_analyst", "trend_analysis")
style_expert = _expert("style_expert", "style_analysis")
nutrition_expert = _expert("nutrition_expert", "nutrition_analysis")


# ---------- phase 4: grounded recommendations ----------

async def recommend(state: ChatState) -> dict:
    ctx = state["context"]
    restaurants = (state.get("local_candidates") or []) + (state.get("web_candidates") or [])
    recipes = state.get("recipes") or []
    location = state.get("location")

    if not restaurants and not recipes:
        where = t(ctx, "near").format(place=location.display_name) if location else ""
        return {"answer": t(ctx, "none").format(where=where), "cards": []}

    snippets = state.get("web_snippets") or []
    web_block = ""
    if snippets:
        web_block = "\n<untrusted_web_content>\n" + "\n".join(
            f"- {s.title}: {s.content}" for s in snippets
        ) + "\n</untrusted_web_content>"
    experts = "\n".join(
        f"{name}: {state.get(key)}"
        for name, key in (("Trends", "trend_analysis"), ("Styles", "style_analysis"),
                          ("Nutrition", "nutrition_analysis"))
        if state.get(key)
    )
    where = (
        f"User location: {location.display_name}"
        if location
        else "User location: unknown. The candidates come from a California guide: never say they are "
        "near the user."
    )
    user = (
        f"Answer language: {ctx.language}\n"
        f"{where}\n"
        f"User message: {state['message']}\n"
        f"User preferences: {_preferences(ctx)}\n"
        f"Restaurant candidates: {json.dumps([_candidate_line(c) for c in restaurants], ensure_ascii=False)}\n"
        f"Recipe candidates: {json.dumps([r.model_dump(exclude={'score'}) for r in recipes])}\n"
        f"Expert notes:\n{experts or '(none)'}{web_block}"
    )
    picks = await structured_call(RecommendationSet, load_prompt("recommender"), user)
    return format_answer(picks, restaurants, recipes, ctx, location)


def ground(picks: RecommendationSet | None, restaurants, recipes) -> tuple[list, list]:
    """Keep only picks whose id matches a retrieved candidate (anti-hallucination)."""
    by_id = {c.id: c for c in restaurants}
    recipe_by_id = {r.id: r for r in recipes}
    if picks is None:
        picks = RecommendationSet(summary="", restaurants=[], recipes=[])
    chosen_r = [(by_id[p.candidate_id], p.reasoning) for p in picks.restaurants if p.candidate_id in by_id]
    chosen_p = [(recipe_by_id[p.candidate_id], p.reasoning) for p in picks.recipes
                if p.candidate_id in recipe_by_id]
    dropped = len(picks.restaurants) + len(picks.recipes) - len(chosen_r) - len(chosen_p)
    if dropped:
        log.warning("ungrounded_picks_dropped", count=dropped)
    # Fall back to the retriever's ranking when the model returned nothing usable
    if not chosen_r and not chosen_p:
        chosen_r = [(c, "") for c in sorted(restaurants, key=lambda c: c.score, reverse=True)[:3]]
        chosen_p = [(r, "") for r in recipes[:3]]
    return chosen_r[:3], chosen_p[:3]


def format_answer(picks, restaurants, recipes, ctx: UserContext, location) -> dict:
    chosen_r, chosen_p = ground(picks, restaurants, recipes)
    lines = [picks.summary] if picks and picks.summary else []
    cards = []
    if chosen_r:
        lines.append(f"\n**{t(ctx, 'restaurants')}**")
        for i, (c, why) in enumerate(chosen_r, 1):
            meta = " · ".join(filter(None, [c.cuisine, c.location, f"{c.rating}/5" if c.rating else None,
                                            "$" * c.price_range if c.price_range else None]))
            lines.append(f"{i}. **{c.name}** ({meta}) - _{t(ctx, c.source)}_")
            if why:
                lines.append(f"   {why}")
            cards.append({"type": "restaurant", "reasoning": why, **c.model_dump(exclude_none=True)})
    if chosen_p:
        lines.append(f"\n**{t(ctx, 'recipes')}**")
        for i, (r, why) in enumerate(chosen_p, 1):
            lines.append(f"{i}. **{r.name}**" + (f" ({r.cuisine})" if r.cuisine else ""))
            if why:
                lines.append(f"   {why}")
            cards.append({"type": "recipe", "reasoning": why, **r.model_dump(exclude_none=True)})
    if any(c.source == "osm" for c, _ in chosen_r):
        lines.append(f"\n_{t(ctx, 'osm_credit')}_")
    if ctx.intent in ("restaurant", "both") and location is None:
        lines.append(f"\n_{t(ctx, 'tip')}_")
    return {"answer": "\n".join(lines).strip(), "cards": cards}
