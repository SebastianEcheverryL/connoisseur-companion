"""Masking and no-op behaviour of the tracing layer (Langfuse is not configured in tests)."""
from connoisseur import observability as obs
from connoisseur.agents import prompts


def test_mask_redacts_coordinates_secrets_and_contacts():
    data = {
        "message": "Estoy en 6.2442, -75.5812, escríbeme a ana@example.com o al +57 300 123 4567",
        "coords": {"lat": 6.24, "lon": -75.58},
        "location": {"display_name": "Laureles, Medellín", "lat": 6.24, "lon": -75.58},
        "headers": [{"authorization": "Bearer abc"}],
        "note": "key tvly-abcdefghijklmnop",
    }
    masked = obs.mask_sensitive(data)
    assert masked["message"] == "Estoy en [coords], escríbeme a [email] o al [phone]"
    assert masked["coords"] == obs.REDACTED
    assert masked["location"] == {"display_name": "Laureles, Medellín", "lat": obs.REDACTED, "lon": obs.REDACTED}
    assert masked["headers"][0]["authorization"] == obs.REDACTED
    assert "tvly-" not in masked["note"]


def test_mask_keeps_dates_ratings_and_ids():
    text = "Visited 2025-11-15, rated 4.5/5, review 900000001, 1,234 reviews"
    assert obs.mask_sensitive(text) == text


def test_tracing_is_a_no_op_without_keys():
    assert not obs.is_enabled()
    assert obs.langchain_callbacks() == []
    with obs.trace_request("chat", input={"message": "hi"}) as trace:
        assert trace.trace_id is None
        trace.finish(output="ok")
    assert obs.score("0" * 32, "user_feedback", 1.0) is False


async def test_traced_decorator_is_transparent_when_disabled():
    @obs.traced("double")
    async def double(x):
        return x * 2

    @obs.traced()
    def triple(x):
        return x * 3

    assert await double(2) == 4
    assert triple(2) == 6
    assert triple.__name__ == "triple"


def test_prompts_fall_back_to_local_files():
    assert prompts.load_prompt("profile") == prompts.local_prompt("profile")
    assert "User Profile Generator" in prompts.load_prompt("profile")
