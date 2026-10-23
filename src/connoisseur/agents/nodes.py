"""Nodes of the recommendation workflow. Each node reads the shared state and returns a partial update."""
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from connoisseur.agents.prompts import load_prompt
from connoisseur.agents.state import ChatState
from connoisseur.llm import get_chat_model, message_text
from connoisseur.log import get_logger
from connoisseur.places import osm, ratings, tavily
from connoisseur.retrieval import service
from connoisseur.schemas import PlaceCandidate, ProfileExtraction, RecommendationSet, UserContext
from connoisseur.security import allergens, spotlight
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
        "reviews": "reviews",
        "allergy_note": "Allergy note: I can't guarantee any dish is free of an allergen. Check the full ingredient "
        "list and ask the restaurant about cross-contamination before eating.",
        "blocked_input": "Sorry, I can't help with that. I'm here for restaurants, food and recipes.",
        "blocked_injection": "That message looks like an attempt to change my instructions, so I won't follow it. "
        "Ask me about restaurants, food or recipes instead!",
        "blocked_output": "Sorry, I couldn't produce a safe answer for that. Could you rephrase your request?",
        "timeout": "That took too long, sorry. Please try again, maybe with a simpler request.",
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
        "reviews": "reseñas",
        "allergy_note": "Nota sobre alergias: no puedo garantizar que un plato esté libre de un alérgeno. Revisa la "
        "lista completa de ingredientes y pregunta en el restaurante por contaminación cruzada antes de comer.",
        "blocked_input": "Lo siento, no puedo ayudarte con eso. Estoy aquí para restaurantes, comida y recetas.",
        "blocked_injection": "Ese mensaje parece un intento de cambiar mis instrucciones, así que no lo voy a seguir. "
        "¡Pregúntame por restaurantes, comida o recetas!",
        "blocked_output": "Lo siento, no pude generar una respuesta segura para eso. ¿Puedes reformular tu pedido?",
        "timeout": "Esto tardó demasiado, lo siento. Inténtalo de nuevo, quizá con un pedido más simple.",
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
    return t_lang(ctx.language if ctx else "English", key)


def t_lang(language: str, key: str) -> str:
    return TEXT.get(language.strip().capitalize(), TEXT["English"])[key]


_SPANISH_HINTS = re.compile(r"[ñ¿¡áéíóú]|\b(que|quiero|dime|c[oó]mo|d[oó]nde|para|por favor|hola|recomi[eé]ndame)\b",
                            re.IGNORECASE)


def guess_language(text: str) -> str:
    """Cheap guess used before the profile agent runs (e.g. to word a refusal)."""
    return "Spanish" if _SPANISH_HINTS.search(text) else "English"


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
        include={"id", "name", "location", "cuisine", "rating", "rating_source", "review_count", "price_range",
                 "vibe", "description", "diet_tags"},
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
    recipes = service.search_recipes(ctx, k=8) if ctx.intent in ("recipe", "both") else []
    route = state["route"] + ["local"]
    allergies = allergens.detect_allergies(state["message"], ctx.dietary)
    if allergies and recipes:
        # Deterministic: recipes that list the allergen never reach the LLM
        safe = [r for r in recipes if not allergens.contains_allergen(r.ingredients, allergies)]
        route.append(f"allergen_filter:{len(recipes) - len(safe)}")
        recipes = safe
    return {"local_candidates": restaurants, "recipes": recipes[:5], "route": route}


def route_after_local(state: ChatState) -> str:
    ctx, location = state["context"], state.get("location")
    too_few = len(state.get("local_candidates") or []) < get_settings().min_local_results
    if ctx.intent in ("restaurant", "both") and location is not None and too_few:
        return "web_search"  # in California but the guide has little for this request
    return "experts"


async def web_search(state: ChatState) -> dict:
    """Web Search agent: OpenStreetMap for real places near the user, Tavily to research their ratings.

    It is deliberately deterministic: web content never decides which tool runs next, and ratings
    are parsed from review snippets with regular expressions, not generated by the LLM.
    """
    ctx, location = state["context"], state.get("location")
    if location is None:
        return {"web_candidates": [], "web_snippets": [], "route": state["route"] + ["web:skipped"]}

    places = await osm.find_places(
        location.coordinates, cuisines=ctx.cuisines, dietary=ctx.dietary, area_name=location.city,
        query=ctx.search_query,
    )
    route = state["route"] + ["web"]
    if tavily.is_enabled() and places:
        places = await ratings.enrich_with_ratings(places, location.city or location.query)
        route.append("ratings")
    # Review excerpts go to the recommender as untrusted context, tied to the place they describe.
    # A page carrying an injection is dropped together with the rating parsed from it.
    snippets = []
    for p in places:
        if not (p.review_url and p.review_snippet):
            continue
        _, blocked = spotlight.sanitize(p.review_snippet)
        if blocked:
            route.append("web:snippet_blocked")
            p.rating = p.rating_source = p.review_count = p.review_url = p.review_snippet = None
            continue
        snippets.append(tavily.WebSnippet(title=p.name, url=p.review_url, content=p.review_snippet))
    log.info("web_search", places=len(places), rated=sum(p.rating is not None for p in places))
    return {"web_candidates": places, "web_snippets": snippets, "route": route}


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
        web_block = "\nReview excerpts:\n" + "\n".join(
            spotlight.wrap(f"{s.title}: {s.content}", source="web_review") for s in snippets
        )
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
    allergies = allergens.detect_allergies(state["message"], ctx.dietary)
    result = format_answer(picks, restaurants, recipes, ctx, location, allergies)
    # What the recommender was allowed to rely on: used by groundedness evaluations
    result["evidence"] = [json.dumps(_candidate_line(c), ensure_ascii=False) for c in restaurants] + [
        r.model_dump_json(exclude={"score"}) for r in recipes
    ] + [f"{s.title}: {s.content}" for s in snippets]
    return result


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


def _rating_text(c: PlaceCandidate, ctx: UserContext) -> str | None:
    """"4.5/5" for the guide; "⭐ 4.5/5 [Tripadvisor](url), 1,234 reviews" for researched web places."""
    if c.rating is None:
        return None
    if not c.rating_source:
        return f"{c.rating}/5"
    source = f"[{c.rating_source}]({c.review_url})" if c.review_url else c.rating_source
    reviews = f", {c.review_count:,} {t(ctx, 'reviews')}" if c.review_count else ""
    return f"⭐ {c.rating}/5 {source}{reviews}"


def format_answer(picks, restaurants, recipes, ctx: UserContext, location, allergies=frozenset()) -> dict:
    chosen_r, chosen_p = ground(picks, restaurants, recipes)
    if allergies:
        # Never let the model guarantee allergen safety (see security/allergens.py)
        chosen_r = [(c, allergens.strip_safety_claims(why)) for c, why in chosen_r]
        chosen_p = [(r, allergens.strip_safety_claims(why)) for r, why in chosen_p]
        if picks:
            picks.summary = allergens.strip_safety_claims(picks.summary)
    lines = [picks.summary] if picks and picks.summary else []
    cards = []
    if chosen_r:
        lines.append(f"\n**{t(ctx, 'restaurants')}**")
        for i, (c, why) in enumerate(chosen_r, 1):
            meta = " · ".join(filter(None, [c.cuisine, c.location, _rating_text(c, ctx),
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
    if allergies:
        lines.append(f"\n⚠️ _{t(ctx, 'allergy_note')}_")
    return {"answer": "\n".join(lines).strip(), "cards": cards}
