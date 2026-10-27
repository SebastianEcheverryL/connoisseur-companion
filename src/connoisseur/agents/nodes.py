"""Nodes of the recommendation workflow. Each node reads the shared state and returns a partial update."""
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from connoisseur import data, memory
from connoisseur.agents import grounding
from connoisseur.agents.prompts import load_prompt
from connoisseur.agents.state import ChatState
from connoisseur.llm import get_chat_model, message_text
from connoisseur.log import get_logger
from connoisseur.places import dishes, osm, ratings, tavily, web_places
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
        "dishes": "These are the dishes of **{name}** mentioned on the web:",
        "no_dishes": "I couldn't find on the web which dishes {name} serves. Check their menu or reviews "
        "before you go.",
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
        "dishes": "Estos son los platos de **{name}** que mencionan en la web:",
        "no_dishes": "No encontré en la web qué platos sirve {name}. Te sugiero revisar su menú o sus reseñas "
        "antes de ir.",
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
    return _memory_block(state) + "\n".join(lines)


def _memory_block(state: ChatState) -> str:
    """What earlier turns established: it outlives the short transcript window."""
    blocks = []
    known = state.get("memory")
    if known and (prefs := known.model_dump(exclude_none=True, exclude_defaults=True)):
        blocks.append(f"Known preferences so far: {json.dumps(prefs, ensure_ascii=False)}")
    if cards := state.get("last_cards"):
        blocks.append("Previous recommendations, numbered as shown to the user:\n"
                      + spotlight.tag(memory.describe_cards(cards), source="previous_answer"))
    return "".join(f"{block}\n\n" for block in blocks) + ("Conversation:\n" if blocks else "")


async def structured_call[T: BaseModel](schema: type[T], system: str, user: str) -> T | None:
    """Ask the model for an instance of `schema`; returns None when the model's output is unusable.

    Schemas with a `from_partial` classmethod get a second chance: an answer that misses some fields
    is completed with defaults instead of being thrown away.
    """
    try:
        model = get_chat_model(temperature=0).with_structured_output(schema, include_raw=True)
        out = await model.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        parsed = out.get("parsed")
        if parsed is not None:
            return parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        payload = _raw_payload(out.get("raw"))
        recover = getattr(schema, "from_partial", None)
        if recover and payload is not None and (result := recover(payload)) is not None:
            log.info("structured_output_recovered", schema=schema.__name__,
                     missing=sorted(set(schema.model_fields) - set(payload)))
            return result
        log.warning("structured_output_failed", schema=schema.__name__, error=str(out.get("parsing_error"))[:300])
        return None
    except Exception as exc:  # noqa: BLE001 - small local models fail in many ways; degrade, don't crash
        log.warning("structured_output_failed", schema=schema.__name__, error=str(exc)[:300])
        return None


def _raw_payload(raw) -> dict | None:
    """The arguments the model produced: a tool call (Bedrock) or a JSON message body (Ollama)."""
    if raw is None:
        return None
    if getattr(raw, "tool_calls", None):
        args = raw.tool_calls[0].get("args")
        return args if isinstance(args, dict) else None
    try:
        data = json.loads(message_text(raw.content))
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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


def _allergies(state: ChatState) -> set[str]:
    """Allergies said in any turn of the conversation (kept by memory), plus this message's."""
    known = state.get("memory")
    found = allergens.detect_allergies(state["message"], state["context"].dietary)
    return found | set(known.allergies if known else [])


def _preferences(ctx: UserContext) -> str:
    return ctx.model_dump_json(exclude={"search_query", "restaurant_name", "intent"}, exclude_none=True)


# ---------- phase 1: profile and routing ----------

async def profile(state: ChatState) -> dict:
    """User Profile Generator + intent router: one structured LLM call, then the remembered profile.

    The LLM reads this message (with the known preferences and previous recommendations); code
    decides what is kept from earlier turns (memory.merge_profile), so a failed or forgetful
    extraction no longer loses the place or the allergies.
    """
    extracted = await structured_call(ProfileExtraction, load_prompt("profile"), _transcript(state))
    if extracted is None:
        # Degrade gracefully: treat the message as a restaurant search
        ctx = UserContext(intent="restaurant", language=guess_language(state["message"]),
                          search_query=state["message"][:200])
    else:
        ctx = extracted.to_context()
    named_location = ctx.location
    known = memory.merge_profile(state.get("memory") or memory.Profile(), ctx, state["message"],
                                 extracted.dropped_preferences if extracted else [])
    route = [f"intent:{ctx.intent}"]
    used_home = False
    home = state.get("home_location")
    if (home and not known.location and not ctx.near_me and not state.get("coords")
            and ctx.intent in ("restaurant", "both")):
        known = known.model_copy(update={"location": home})  # the city saved from an earlier chat
        used_home = True
        route.append("memory:home_location")
    ctx = memory.apply_profile(ctx, known)
    if not ctx.search_query:
        ctx.search_query = service.build_query(ctx)
    log.info("profile", intent=ctx.intent, has_location=bool(ctx.location or state.get("coords")),
             remembered=bool(state.get("memory") or state.get("last_cards")))
    return {"context": ctx, "memory": known, "named_location": named_location, "used_home": used_home,
            "route": route}


def route_intent(state: ChatState) -> str:
    intent = state["context"].intent
    if intent in ("restaurant", "both"):
        return "locate"
    if intent == "recipe":
        return "retrieve_local"
    if intent == "lookup":
        # The guide's tools only know the curated dataset: earlier web/OSM picks are recalled instead,
        # and so is a named place outside the guide ("¿qué pido en Carmen?"), researched on the web
        if card := _previous_card(state):
            return "tool_agent" if card.id.startswith("dataset:") else "recall"
        name = state["context"].restaurant_name
        if name and tavily.is_enabled() and not data.find_restaurants(name):
            return "recall"
        return "tool_agent"
    return "respond"


def _previous_card(state: ChatState) -> memory.CardRef | None:
    """The earlier recommendation the user is asking about (the profile agent copies its name)."""
    wanted = ratings.fold(state["context"].restaurant_name or "")
    if not wanted:
        return None
    for card in state.get("last_cards") or []:
        name = ratings.fold(card.name)
        if name and (name == wanted or name in wanted or wanted in name):
            return card
    return None


_DISH_QUESTION = re.compile(
    r"\b(plat(o|os|illo|illos)|dish(es)?|men[uú]s?|carta|especialidad(es)?|specialt(y|ies)|signature|"
    r"qu[eé] (me )?(pido|ordeno|como|comer|pruebo)|pedir|ordenar|probar|"
    r"what (to|should i|do you recommend i) (eat|get|try)|order)\b",
    re.IGNORECASE,
)


def asks_for_dishes(text: str) -> bool:
    return bool(_DISH_QUESTION.search(text))


def _lookup_target(state: ChatState) -> memory.CardRef:
    """The earlier pick the user points to, or the place they named (with the city known so far)."""
    if card := _previous_card(state):
        return card
    ctx = state["context"]
    return memory.CardRef(n=0, type="restaurant", id="web:lookup", name=ctx.restaurant_name or "",
                          location=ctx.location)


async def _pages_about(card: memory.CardRef) -> tuple[list[str], list[str]]:
    """Review pages that name this place: (tagged blocks for the prompt, evidence lines)."""
    s = get_settings()
    if not tavily.is_enabled():
        return [], []
    blocks, evidence = [], []
    query = f'"{card.name}" {card.location or ""} restaurant reviews'
    for r in await tavily.search(query, max_results=5, include_domains=s.rating_domains or None,
                                 ttl_s=s.rating_cache_ttl_s):
        if not ratings.about_place(r, card.name, card.location):
            continue  # another place, or a branch in another city
        safe, blocked = spotlight.sanitize(f"{r.title}: {r.content}")
        if not blocked:
            blocks.append(spotlight.tag(safe, source=ratings.site_name(r.url)))
            evidence.append(f"{safe} ({r.url})")
    return blocks, evidence


async def recall(state: ChatState) -> dict:
    """Questions about a place outside the guide (an earlier web/OSM pick or one the user names):
    what is known about it plus web results about it. "What should I order?" gets the dishes instead."""
    ctx, card, s = state["context"], _lookup_target(state), get_settings()
    if asks_for_dishes(state["message"]):
        return await _dishes_answer(state, card)
    details = json.dumps(card.model_dump(exclude_none=True, exclude={"n", "id"}), ensure_ascii=False)
    results, page_evidence = await _pages_about(card)
    evidence = [details, *page_evidence]
    user = (
        f"Answer language: {ctx.language}\n"
        f"User message: {state['message']}\n"
        f"Place details: {spotlight.tag(details.replace('<', '').replace('>', ''), source='previous_answer')}\n"
        f"Web results about it:\n" + ("\n".join(results) or "(none)")
    )
    answer = await text_call(load_prompt("recall"), user, temperature=0)
    route = state["route"] + ["recall"] + (["recall:web"] if results else [])
    if s.enable_grounding_check:
        answer, revised = await grounding.enforce(state["message"], answer, evidence, ctx.language)
        route.append("grounding:revised" if revised else "grounding:ok")
    return {"answer": answer, "route": route, "evidence": evidence}


async def _dishes_answer(state: ChatState, card: memory.CardRef) -> dict:
    """The dishes pages about the place name, each with its link, in fixed text.

    No free LLM text on purpose: asked to introduce the list, Nova Lite repeated it, called the dishes
    "typical" and wrote placeholders such as <link_to_menu>; its grounding check also removed dishes
    the evidence did list. Every dish here was found literally in the page it links to.
    """
    ctx = state["context"]
    found = await dishes.find_dishes(card.name, card.location)
    route = state["route"] + ["recall", f"recall:dishes:{len(found)}"]
    if not found:
        return {"answer": t(ctx, "no_dishes").format(name=card.name), "route": route, "evidence": []}
    by_page: dict[tuple[str, str], list[str]] = {}
    for d in found:
        by_page.setdefault((d.site, d.url), []).append(d.name)
    lines = [t(ctx, "dishes").format(name=card.name),
             *(f"- {', '.join(names)} · [{site}]({url})" for (site, url), names in by_page.items())]
    return {"answer": "\n".join(lines), "route": route,
            "evidence": [f"Dish named on {d.site}: {d.name} ({d.url})" for d in found]}


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
    allergies = _allergies(state)
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
    """Web Search agent: real places near the user, from the first source that has them.

    1. OpenStreetMap (Overpass): tagged places around the point; Tavily then researches their ratings.
    2. Tavily: restaurants named on review and listing sites, kept only if the cited result names them.
    3. Photon: OpenStreetMap search by name, the last resort.

    It is deliberately deterministic: web content never decides which tool runs next, and ratings
    are parsed from review snippets with regular expressions, not generated by the LLM.
    """
    ctx, location = state["context"], state.get("location")
    if location is None:
        return {"web_candidates": [], "web_snippets": [], "route": state["route"] + ["web:skipped"]}

    search = {"cuisines": ctx.cuisines, "dietary": ctx.dietary, "area_name": location.city,
              "query": ctx.search_query}
    route = state["route"] + ["web"]
    if places := await osm.find_places(location.coordinates, photon_fallback=False, **search):
        route.append("web:osm")
    elif places := await web_places.discover_places(location, ctx):
        route.append("web:tavily")
    elif places := await osm.search_photon(location.coordinates, **search):
        route.append("web:photon")
    else:
        route.append("web:none")
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
        f"User allergies: {', '.join(sorted(_allergies(state))) or 'none mentioned'}\n"
        f"Restaurant candidates: {json.dumps([_candidate_line(c) for c in restaurants], ensure_ascii=False)}\n"
        f"Recipe candidates: {json.dumps([r.model_dump(exclude={'score'}) for r in recipes])}\n"
        f"Expert notes:\n{experts or '(none)'}{web_block}"
    )
    picks = await structured_call(RecommendationSet, load_prompt("recommender"), user)
    allergies = _allergies(state)
    result = format_answer(picks, restaurants, recipes, ctx, location, allergies)
    # What the recommender was allowed to rely on: used by groundedness evaluations
    result["evidence"] = [json.dumps(_candidate_line(c), ensure_ascii=False) for c in restaurants] + [
        r.model_dump_json(exclude={"score", "text_score", "image_score"}) for r in recipes
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
            # Two trailing spaces = Markdown hard line break, so the reasoning starts on its own line
            lines.append(f"{i}. **{c.name}** ({meta}) - _{t(ctx, c.source)}_" + ("  " if why else ""))
            if why:
                lines.append(f"   {why}")
            cards.append({"type": "restaurant", "reasoning": why, **c.model_dump(exclude_none=True)})
    if chosen_p:
        lines.append(f"\n**{t(ctx, 'recipes')}**")
        for i, (r, why) in enumerate(chosen_p, 1):
            lines.append(f"{i}. **{r.name}**" + (f" ({r.cuisine})" if r.cuisine else "") + ("  " if why else ""))
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
