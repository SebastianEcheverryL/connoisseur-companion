"""Conversation memory and per-user memory, kept on the server.

- Conversation state (`conv:<user>:<session>`, 7 days): the profile accumulated over the chat (place,
  cuisines, diet, allergies...) and the last recommendations, so "something cheaper" or "the second
  one" make sense several turns later.
- User memory (`mem:<user>`, 180 days, signed-in users only): usual city, diet and allergies, used to
  start a new chat. "Forget my preferences" deletes it.

No messages and no coordinates are stored: the chat history still comes from the client.
Locally the store lives in memory; on AWS it is the DynamoDB table of the quota counters (STATE_TABLE),
with the same `expires_at` TTL attribute.
"""
import json
import re
import time
from functools import lru_cache
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel, Field

from connoisseur.places.ratings import fold
from connoisseur.schemas import UserContext
from connoisseur.security import allergens
from connoisseur.settings import get_settings

MAX_ITEMS = 8
MAX_TEXT = 100
MAX_CARDS = 6


class Profile(BaseModel):
    """What the user asked for so far in this conversation."""

    location: str | None = None
    cuisines: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    vibe: str | None = None
    budget: str | None = None
    occasion: str | None = None
    topic: str | None = None  # "restaurant" or "recipe": what the cuisine, vibe and budget were about


class CardRef(BaseModel):
    """A recommendation shown earlier, numbered as in the answer ("the second one")."""

    n: int
    type: str
    id: str
    name: str
    location: str | None = None
    cuisine: str | None = None
    rating: float | None = None
    rating_source: str | None = None
    source: str | None = None
    url: str | None = None


class ConversationState(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    last_cards: list[CardRef] = Field(default_factory=list)
    home_location: str | None = None  # from the user memory: the default city of this chat


class UserMemory(BaseModel):
    home_location: str | None = None
    dietary: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)


# ---------- merging (deterministic: the LLM extracts, code decides what is kept) ----------

def _clip(items: list[str]) -> list[str]:
    out, seen = [], set()
    for item in items:
        text = " ".join(str(item).split())[:MAX_TEXT]
        if text and fold(text) not in seen:
            seen.add(fold(text))
            out.append(text)
    return out[:MAX_ITEMS]


def _text(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(value.split())[:MAX_TEXT] or None


# A price level is only believed when the message talks about price: Nova Lite answered "$$$" for "dinner"
PRICE_WORDS = re.compile(
    r"\$|cheap|pric|budget|afford|expensive|fancy|upscale|splurge|luxur"
    r"|barat|econ[oó]mic|caro|cara\b|precio|presupuesto|lujo|elegante|costos",
    re.IGNORECASE,
)
TOPICS = {"restaurant": "restaurant", "both": "restaurant", "recipe": "recipe"}


def _wording(value: str | None) -> str | None:
    """A vibe or occasion is a few words ("moody", "date night"). Nova Lite copied whole sentences of
    the message into them ("Soy alérgico al maní."), which memory would then keep for the whole chat."""
    text = _text(value)
    return text if text and len(text.split()) <= 3 else None


def merge_profile(prev: Profile, ctx: UserContext, message: str, dropped: list[str] | None = None) -> Profile:
    """Combine what was known with what this message adds.

    - New values replace old ones; a field the message does not mention keeps its value
      ("now in Brooklyn" keeps "sushi"), unless the topic changes between recipes and restaurants:
      then cuisine, vibe, occasion and budget start over (the noodles of a recipe are not a
      restaurant search).
    - A price level needs price words in the message; models invent one otherwise.
    - Diet accumulates; an item leaves only when the user drops it ("I'm not vegan anymore").
    - Allergies only accumulate, detected by rules on every message: forgetting them takes an
      explicit "forget my preferences". A missed allergy is worse than a stale one.
    """
    removed = {fold(item) for item in dropped or []}
    found = allergens.detect_allergies(message, ctx.dietary)
    # Allergies live in their own field: Nova Lite filed "peanut" as a diet despite the prompt
    diet = [d for d in [*prev.dietary, *ctx.dietary] if fold(d) not in removed and not allergens.is_allergen_name(d)]
    topic = TOPICS.get(ctx.intent) or prev.topic
    switched = prev.topic is not None and topic != prev.topic
    same = Profile() if switched else prev
    cuisines = ctx.cuisines
    if switched:
        # The model re-reads the old request in the history: only cuisines this message names count
        cuisines = [c for c in cuisines if fold(c) in fold(message)]
    budget = ctx.budget if ctx.budget and PRICE_WORDS.search(message) else None
    return Profile(
        location=_text(ctx.location) or (None if ctx.near_me else prev.location),
        cuisines=_clip(cuisines or same.cuisines),
        dietary=_clip(diet),
        allergies=sorted(set(prev.allergies) | found),
        vibe=_wording(ctx.vibe) or same.vibe,
        budget=budget or same.budget,
        occasion=_wording(ctx.occasion) or same.occasion,
        topic=topic,
    )


def apply_profile(ctx: UserContext, profile: Profile) -> UserContext:
    """The context the downstream agents use: this message's request plus what is remembered."""
    query = ctx.search_query
    missing = [c for c in profile.cuisines if fold(c) not in fold(query)]
    if query and missing:
        query = f"{' '.join(missing)} {query}"  # "something cheaper" still searches for sushi
    return ctx.model_copy(update={
        "location": profile.location,
        "cuisines": profile.cuisines,
        "dietary": profile.dietary,
        "vibe": profile.vibe,
        "budget": profile.budget,
        "occasion": profile.occasion,
        "search_query": query,
    })


def card_refs(cards: list[dict]) -> list[CardRef]:
    """Number the cards per type, as the answer lists them (restaurants 1..n, recipes 1..m)."""
    refs, counters = [], {}
    for card in cards:
        kind = card.get("type", "restaurant")
        counters[kind] = counters.get(kind, 0) + 1
        refs.append(CardRef(n=counters[kind], type=kind, **{
            key: card.get(key) for key in CardRef.model_fields if key not in ("n", "type") and key in card
        }))
    return refs[:MAX_CARDS]


def describe_cards(cards: list[CardRef]) -> str:
    """One line per card for prompts. Names can come from the web: no angle brackets, short."""
    lines = []
    for card in cards:
        name = card.name.replace("<", "").replace(">", "")[:80]
        details = ", ".join(filter(None, [card.cuisine, card.location]))
        lines.append(f"{card.type} {card.n}: {name}" + (f" ({details})" if details else ""))
    return "\n".join(lines)


# ---------- storage ----------

class MemoryStore(Protocol):
    def get(self, key: str) -> dict | None: ...
    def put(self, key: str, data: dict, ttl_s: int) -> None: ...
    def delete(self, key: str) -> None: ...


class InProcessStore:
    def __init__(self):
        self._data: dict[str, tuple[dict, float]] = {}
        self._lock = Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            item = self._data.get(key)
        if item is None or item[1] <= time.time():
            return None
        return item[0]

    def put(self, key: str, data: dict, ttl_s: int) -> None:
        with self._lock:
            self._data[key] = (data, time.time() + ttl_s)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class DynamoMemoryStore:
    """One item per key: the JSON document in `data`, expired by DynamoDB's TTL on `expires_at`."""

    def __init__(self, table: str, client=None):
        if client is None:
            import boto3

            client = boto3.client("dynamodb")
        self.table = table
        self.client = client

    def get(self, key: str) -> dict | None:
        item = self.client.get_item(TableName=self.table, Key={"pk": {"S": key}}).get("Item")
        # DynamoDB deletes expired items lazily (up to days later): check the expiry ourselves
        if not item or int(item.get("expires_at", {}).get("N", "0")) <= time.time():
            return None
        return json.loads(item["data"]["S"])

    def put(self, key: str, data: dict, ttl_s: int) -> None:
        self.client.put_item(TableName=self.table, Item={
            "pk": {"S": key},
            "data": {"S": json.dumps(data, ensure_ascii=False)},
            "expires_at": {"N": str(int(time.time()) + ttl_s)},
        })

    def delete(self, key: str) -> None:
        self.client.delete_item(TableName=self.table, Key={"pk": {"S": key}})


@lru_cache
def get_store() -> MemoryStore:
    table = get_settings().state_table
    return DynamoMemoryStore(table) if table else InProcessStore()


# ---------- conversation lifecycle ----------

def _conversation_key(user_key: str | None, session_id: str) -> str:
    return f"conv:{user_key or 'anon'}:{session_id}"


def _remembers_user(user_key: str | None) -> bool:
    # Only signed-in users: an IP is shared by many people
    return bool(user_key and user_key.startswith("user:"))


def load(user_key: str | None, session_id: str | None) -> tuple[ConversationState, bool]:
    """The state of this conversation, or a new one seeded from the user memory. Returns (state, from_saved)."""
    if not session_id:
        return ConversationState(), False  # no session: stateless, as before
    store = get_store()
    if (data := store.get(_conversation_key(user_key, session_id))) is not None:
        return ConversationState.model_validate(data), False
    if _remembers_user(user_key) and (saved := store.get(f"mem:{user_key}")) is not None:
        user = UserMemory.model_validate(saved)
        profile = Profile(dietary=user.dietary, allergies=user.allergies)
        return ConversationState(profile=profile, home_location=user.home_location), True
    return ConversationState(), False


def save(user_key: str | None, session_id: str | None, state: ConversationState,
         named_location: str | None) -> None:
    """Store the conversation, and what is worth remembering about the user for the next chat."""
    if not session_id:
        return
    s, store = get_settings(), get_store()
    store.put(_conversation_key(user_key, session_id), state.model_dump(exclude_none=True), s.conversation_ttl_s)
    if not _remembers_user(user_key):
        return
    user = UserMemory(
        home_location=_text(named_location) or state.home_location,  # a city the user named, never coordinates
        dietary=state.profile.dietary,
        allergies=state.profile.allergies,
    )
    if user.home_location or user.dietary or user.allergies:
        store.put(f"mem:{user_key}", user.model_dump(exclude_none=True), s.user_memory_ttl_s)


def forget(user_key: str | None, session_id: str | None) -> None:
    store = get_store()
    if user_key:
        store.delete(f"mem:{user_key}")
    if session_id:
        store.delete(_conversation_key(user_key, session_id))


def summary(state: ConversationState, from_saved: bool, used_home: bool) -> dict[str, Any]:
    """What the UI shows as "Remembered": diet, allergies and the city in use."""
    return {
        "location": state.profile.location,
        "dietary": state.profile.dietary,
        "allergies": state.profile.allergies,
        "from_saved": from_saved or used_home,
    }
