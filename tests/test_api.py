"""HTTP API with the agent runner replaced by a stub."""
import json

import pytest
from fastapi.testclient import TestClient

from connoisseur import api


async def fake_stream(message, history=None, coords=None, **kwargs):
    yield {"type": "step", "node": "profile", "label": "Understanding your request"}
    yield {"type": "answer", "answer": f"echo: {message}", "cards": [], "route": ["intent:test"],
           "coords": coords.model_dump() if coords else None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api, "stream_chat", fake_stream)
    with TestClient(api.app) as c:
        yield c


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "ollama"


def test_chat_streams_steps_then_answer(client):
    resp = client.post("/chat", json={"message": "hola"})
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "x-request-id" in resp.headers
    events = parse_sse(resp.text)
    assert [e.get("type") for e in events] == ["step", "answer", None]  # last one is `done`
    assert events[1]["answer"] == "echo: hola"


def test_chat_rejects_empty_and_oversized_messages(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001}).status_code == 422


def test_chat_validates_coordinates(client):
    assert client.post("/chat", json={"message": "hi", "location": {"lat": 120, "lon": 0}}).status_code == 422


def test_chat_reports_errors_as_events(client, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("boom")
        yield  # pragma: no cover

    monkeypatch.setattr(api, "stream_chat", broken)
    events = parse_sse(client.post("/chat", json={"message": "hi"}).text)
    assert events[0]["type"] == "error"
    assert "boom" not in events[0]["message"]  # internal details are not leaked


def test_mcp_endpoint_lists_tools(client):
    resp = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"accept": "application/json, text/event-stream"},
    )
    assert resp.status_code == 200
    assert "search_nearby_restaurants" in resp.text


TRACE_ID = "0" * 31 + "1"


def test_feedback_requires_tracing(client):
    resp = client.post("/feedback", json={"trace_id": TRACE_ID, "value": 1})
    assert resp.status_code == 503


def test_feedback_records_score(client, monkeypatch):
    calls = []
    monkeypatch.setattr(api.observability, "score", lambda *a, **kw: calls.append((a, kw)) or True)
    resp = client.post("/feedback", json={"trace_id": TRACE_ID, "value": 0, "comment": "wrong city"})
    assert resp.json() == {"status": "recorded"}
    args, kwargs = calls[0]
    assert args == (TRACE_ID, "user_feedback", 0.0)
    assert kwargs["comment"] == "wrong city"


@pytest.mark.parametrize("body", [{"trace_id": "not-a-trace", "value": 1}, {"trace_id": TRACE_ID, "value": 5}])
def test_feedback_validates_input(client, body):
    assert client.post("/feedback", json=body).status_code == 422


def test_session_id_is_forwarded(client, monkeypatch):
    seen = {}

    async def capture(message, history=None, coords=None, **kwargs):
        seen.update(kwargs)
        yield {"type": "answer", "answer": "ok"}

    monkeypatch.setattr(api, "stream_chat", capture)
    client.post("/chat", json={"message": "hi", "session_id": "abc-123"})
    assert seen["session_id"] == "abc-123"
    assert client.post("/chat", json={"message": "hi", "session_id": "bad id!"}).status_code == 422
