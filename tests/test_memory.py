"""Conversation and user memory: merge rules, storage with expiry, lifecycle and the /memory endpoint."""
import pytest
from fastapi.testclient import TestClient

from connoisseur import api, memory
from connoisseur.memory import CardRef, ConversationState, DynamoMemoryStore, InProcessStore, Profile
from connoisseur.schemas import UserContext


def ctx(**kw) -> UserContext:
    return UserContext(**{"intent": "restaurant", "search_query": "sushi", **kw})


# ---------- merge rules ----------

def test_a_follow_up_keeps_what_it_does_not_mention():
    before = Profile(location="Medellín", cuisines=["sushi"], vibe="casual")
    after = memory.merge_profile(before, ctx(budget="$", search_query="cheap"), "algo más barato")
    assert after.location == "Medellín" and after.cuisines == ["sushi"] and after.vibe == "casual"
    assert after.budget == "$"


def test_new_values_replace_old_ones():
    before = Profile(location="Medellín", cuisines=["sushi"])
    after = memory.merge_profile(before, ctx(location="Brooklyn", cuisines=["pizza"]), "pizza in Brooklyn")
    assert after.location == "Brooklyn" and after.cuisines == ["pizza"]


def test_near_me_drops_the_remembered_city():
    after = memory.merge_profile(Profile(location="Medellín"), ctx(near_me=True), "sushi near me")
    assert after.location is None


def test_diet_accumulates_until_the_user_drops_it():
    before = Profile(dietary=["vegan"])
    assert memory.merge_profile(before, ctx(dietary=["gluten-free"]), "also gluten free").dietary == [
        "vegan", "gluten-free"]
    assert memory.merge_profile(before, ctx(), "I'm not vegan anymore", dropped=["Vegan"]).dietary == []


def test_allergies_are_detected_by_rules_and_never_dropped_by_the_model():
    first = memory.merge_profile(Profile(), ctx(), "soy alérgico al maní")
    assert first.allergies == ["peanut"]
    later = memory.merge_profile(first, ctx(dietary=[]), "ahora algo italiano", dropped=["peanut"])
    assert later.allergies == ["peanut"]


def test_a_failed_extraction_keeps_the_remembered_profile():
    before = Profile(location="Medellín", cuisines=["sushi"], allergies=["peanut"])
    after = memory.merge_profile(before, UserContext(intent="restaurant"), "?")
    assert after.model_dump(exclude={"topic"}) == before.model_dump(exclude={"topic"})


def test_long_or_repeated_values_are_clipped():
    after = memory.merge_profile(Profile(), ctx(cuisines=["Sushi", "sushi", "x" * 300]), "sushi")
    assert after.cuisines == ["Sushi", "x" * memory.MAX_TEXT]


def test_applied_context_still_searches_for_the_remembered_cuisine():
    applied = memory.apply_profile(ctx(search_query="cheap place"), Profile(location="Medellín", cuisines=["sushi"]))
    assert applied.location == "Medellín" and applied.search_query == "sushi cheap place"


def test_cards_are_numbered_per_type_as_the_answer_shows_them():
    refs = memory.card_refs([
        {"type": "restaurant", "id": "web:1", "name": "ONI Nikkei", "location": "Medellín", "reasoning": "x"},
        {"type": "restaurant", "id": "web:2", "name": "Kuro <Sushi>"},
        {"type": "recipe", "id": "recipe:1", "name": "Ramen"},
    ])
    assert [(r.type, r.n, r.name) for r in refs] == [
        ("restaurant", 1, "ONI Nikkei"), ("restaurant", 2, "Kuro <Sushi>"), ("recipe", 1, "Ramen")]
    text = memory.describe_cards(refs)
    assert "restaurant 2: Kuro Sushi" in text and "<" not in text


# ---------- storage ----------

def test_in_process_store_expires_items(monkeypatch):
    store = InProcessStore()
    now = [1_000.0]
    monkeypatch.setattr(memory.time, "time", lambda: now[0])
    store.put("k", {"a": 1}, ttl_s=10)
    assert store.get("k") == {"a": 1}
    now[0] += 11
    assert store.get("k") is None


class FakeDynamo:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = Item

    def get_item(self, TableName, Key):
        item = self.items.get(Key["pk"]["S"])
        return {"Item": item} if item else {}

    def delete_item(self, TableName, Key):
        self.items.pop(Key["pk"]["S"], None)


def test_dynamo_store_round_trip_and_expiry(monkeypatch):
    client = FakeDynamo()
    store = DynamoMemoryStore("t", client=client)
    store.put("mem:user:1", {"dietary": ["vegan"], "home_location": "Medellín"}, ttl_s=60)
    assert store.get("mem:user:1") == {"dietary": ["vegan"], "home_location": "Medellín"}
    # DynamoDB deletes expired items late: an expired item still in the table must not be used
    client.items["mem:user:1"]["expires_at"] = {"N": "1"}
    assert store.get("mem:user:1") is None
    store.delete("mem:user:1")
    assert client.items == {}


# ---------- lifecycle ----------

@pytest.fixture
def store(monkeypatch):
    fresh = InProcessStore()
    monkeypatch.setattr(memory, "get_store", lambda: fresh)
    return fresh


def state(**profile) -> ConversationState:
    return ConversationState(profile=Profile(**profile), last_cards=[CardRef(n=1, type="restaurant", id="web:1",
                                                                             name="ONI Nikkei")])


def test_conversation_round_trip(store):
    memory.save("user:github:1", "chat-1", state(location="Medellín", allergies=["peanut"]), "Medellín")
    loaded, from_saved = memory.load("user:github:1", "chat-1")
    assert loaded.profile.location == "Medellín" and loaded.last_cards[0].name == "ONI Nikkei"
    assert not from_saved


def test_a_new_chat_starts_from_the_user_memory(store):
    memory.save("user:github:1", "chat-1", state(location="Medellín", dietary=["vegan"], allergies=["peanut"]),
                "Medellín")
    loaded, from_saved = memory.load("user:github:1", "chat-2")
    assert from_saved
    assert loaded.profile.dietary == ["vegan"] and loaded.profile.allergies == ["peanut"]
    assert loaded.home_location == "Medellín"
    assert loaded.profile.location is None and loaded.last_cards == []  # a new chat names its own place


def test_the_saved_city_is_one_the_user_named(store):
    memory.save("user:github:1", "chat-1", state(location="Medellín"), named_location=None)
    assert store.get("mem:user:github:1") is None  # nothing named, no diet, no allergies: nothing to keep


def test_anonymous_users_get_no_memory_across_chats(store):
    memory.save("ip:1.2.3.4", "chat-1", state(location="Medellín", allergies=["peanut"]), "Medellín")
    assert memory.load("ip:1.2.3.4", "chat-1")[0].profile.location == "Medellín"
    assert memory.load("ip:1.2.3.4", "chat-2") == (ConversationState(), False)


def test_without_a_session_nothing_is_stored(store):
    memory.save("user:github:1", None, state(location="Medellín"), "Medellín")
    assert memory.load("user:github:1", None) == (ConversationState(), False)
    assert store.get("mem:user:github:1") is None


def test_forget_deletes_the_user_memory_and_the_conversation(store):
    memory.save("user:github:1", "chat-1", state(dietary=["vegan"]), "Medellín")
    memory.forget("user:github:1", "chat-1")
    assert memory.load("user:github:1", "chat-1") == (ConversationState(), False)
    assert memory.load("user:github:1", "chat-2") == (ConversationState(), False)


# ---------- API ----------

@pytest.fixture
def client(monkeypatch, store):
    captured = {}

    async def fake_run_chat(message, history=None, coords=None, **kwargs):
        captured.update(kwargs)
        return {"type": "answer", "answer": "ok", "cards": [], "route": []}

    monkeypatch.setattr(api, "run_chat", fake_run_chat)
    with TestClient(api.app) as c:
        c.captured = captured
        yield c


def test_chat_passes_the_user_to_the_runner(client):
    client.post("/chat/sync", json={"message": "hola", "session_id": "chat-1"})
    assert client.captured["session_id"] == "chat-1"
    assert client.captured["user_id"].startswith("ip:")  # no trusted user header in this test


def test_delete_memory_forgets_the_caller(client, monkeypatch, store):
    monkeypatch.setattr(api, "user_key", lambda request: "user:github:1")
    memory.save("user:github:1", "chat-1", state(dietary=["vegan"]), "Medellín")
    resp = client.delete("/memory", params={"session_id": "chat-1"})
    assert resp.status_code == 200 and resp.json() == {"status": "forgotten"}
    assert store.get("mem:user:github:1") is None and store.get("conv:user:github:1:chat-1") is None


def test_delete_memory_validates_the_session_id(client):
    assert client.delete("/memory", params={"session_id": "../../etc"}).status_code == 422


def test_an_allergy_filed_as_a_diet_moves_to_the_allergies():
    after = memory.merge_profile(Profile(), ctx(dietary=["peanut", "vegan", "peanut-free"]), "soy alérgico al maní")
    assert after.dietary == ["vegan", "peanut-free"] and after.allergies == ["peanut"]


def test_a_new_topic_starts_its_own_cuisine_vibe_and_budget():
    recipe = memory.merge_profile(Profile(), ctx(intent="recipe", cuisines=["noodles"], budget="$"), "noodles baratos")
    restaurant = memory.merge_profile(recipe, ctx(location="El Poblado"), "algo vegano en El Poblado")
    assert restaurant.cuisines == [] and restaurant.budget is None and restaurant.topic == "restaurant"
    # A lookup or a thank-you does not change the topic
    after = memory.merge_profile(Profile(cuisines=["sushi"], topic="restaurant"), ctx(intent="lookup"), "y el segundo?")
    assert after.cuisines == ["sushi"] and after.topic == "restaurant"


def test_a_price_level_needs_price_words():
    kept = memory.merge_profile(Profile(budget="$$"), ctx(budget="$$$"), "recomiéndame algo para cenar")
    assert kept.budget == "$$"
    assert memory.merge_profile(Profile(budget="$$"), ctx(budget="$"), "algo más barato").budget == "$"
    assert memory.merge_profile(Profile(), ctx(budget="$$$$"), "something fancy").budget == "$$$$"


def test_sentences_copied_into_vibe_or_occasion_are_dropped():
    after = memory.merge_profile(Profile(vibe="quiet"), ctx(vibe="Sushi en Laureles, Medellín",
                                                          occasion="Soy alérgico al maní."), "x")
    assert after.vibe == "quiet" and after.occasion is None
    assert memory.merge_profile(Profile(), ctx(occasion="date night"), "x").occasion == "date night"


def test_after_a_topic_switch_only_cuisines_the_message_names_count():
    recipe = Profile(cuisines=["noodles"], topic="recipe")
    # Nova Lite re-extracted "noodles" from the history for a restaurant request that never says it
    after = memory.merge_profile(recipe, ctx(cuisines=["noodles", "vegan food"]), "algo de vegan food en El Poblado")
    assert after.cuisines == ["vegan food"]
    same_topic = memory.merge_profile(Profile(cuisines=["sushi"], topic="restaurant"), ctx(cuisines=["sushi"]), "y más barato")
    assert same_topic.cuisines == ["sushi"]
