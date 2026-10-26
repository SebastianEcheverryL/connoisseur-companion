"""AWS deployment behaviour without AWS: SSM secrets, DynamoDB quotas, trusted user header, MCP-only mode."""
import pytest
from fastapi.testclient import TestClient

from connoisseur import api
from connoisseur.aws import load_ssm_parameters
from connoisseur.retrieval import store
from connoisseur.security.quota import DynamoCounterStore, MemoryCounterStore, QuotaService
from connoisseur.settings import get_settings


class FakeSSM:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return self

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.pages)


def test_ssm_parameters_become_env_vars_without_overriding(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "from-env")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    fake = FakeSSM([
        {"Parameters": [{"Name": "/connoisseur/prod/LANGFUSE_SECRET_KEY", "Value": "sk-lf-1"},
                        {"Name": "/connoisseur/prod/TAVILY_API_KEY", "Value": "from-ssm"}]},
        {"Parameters": [{"Name": "/connoisseur/prod/MCP_TOKEN", "Value": "CHANGE_ME"}]},
    ])
    loaded = load_ssm_parameters("/connoisseur/prod/", client=fake)
    assert loaded == ["LANGFUSE_SECRET_KEY"]
    assert fake.calls[0] == {"Path": "/connoisseur/prod/", "WithDecryption": True, "Recursive": False}
    import os

    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-lf-1"
    assert os.environ["TAVILY_API_KEY"] == "from-env"  # explicit environment wins
    assert "MCP_TOKEN" not in os.environ  # Terraform placeholder is skipped
    monkeypatch.delenv("LANGFUSE_SECRET_KEY")


class FakeDynamo:
    def __init__(self):
        self.items: dict[str, int] = {}
        self.requests = []

    def update_item(self, **kwargs):
        self.requests.append(kwargs)
        key = kwargs["Key"]["pk"]["S"]
        self.items[key] = self.items.get(key, 0) + int(kwargs["ExpressionAttributeValues"][":one"]["N"])
        return {"Attributes": {"count": {"N": str(self.items[key])}}}


def test_dynamodb_counters_are_atomic_adds_with_ttl(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "per_minute_message_limit", 2)
    monkeypatch.setattr(s, "daily_message_limit", 100)
    fake = FakeDynamo()
    quota = QuotaService(DynamoCounterStore("quotas", client=fake), clock=lambda: 1_800_000_000.0)
    assert [quota.consume("user:1").allowed for _ in range(3)] == [True, True, False]
    request = fake.requests[0]
    assert request["TableName"] == "quotas"
    assert request["UpdateExpression"].startswith("ADD #count :one")
    assert int(request["ExpressionAttributeValues"][":expires"]["N"]) > 1_700_000_000


@pytest.fixture
def client(monkeypatch):
    async def fake_stream(message, history=None, coords=None, **kwargs):
        yield {"type": "answer", "answer": "ok"}

    monkeypatch.setattr(api, "stream_chat", fake_stream)
    monkeypatch.setattr(api, "get_quota_service", lambda: QuotaService(MemoryCounterStore()))
    with TestClient(api.app) as c:
        yield c


def request_with(headers):
    return type("R", (), {"headers": headers, "client": type("C", (), {"host": "1.2.3.4"})()})()


def test_user_header_is_trusted_only_behind_platform_auth(monkeypatch):
    assert api.user_key(request_with({"x-user-id": "github:1"})) == "ip:1.2.3.4"
    monkeypatch.setattr(get_settings(), "trust_user_header", True)
    assert api.user_key(request_with({"x-user-id": "github:1"})) == "user:github:1"


def test_mcp_only_mode_hides_chat_routes(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "expose_chat", False)
    assert client.post("/chat/sync", json={"message": "hi"}).status_code == 404
    assert client.post("/feedback", json={"trace_id": "0" * 32, "value": 1}).status_code == 404
    assert client.delete("/memory").status_code == 404
    assert client.get("/health").status_code == 200
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    ok = client.post("/mcp/", json=body, headers={"accept": "application/json, text/event-stream"})
    assert ok.status_code == 200


def test_baked_index_is_copied_to_a_writable_dir(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "chroma.sqlite3").write_bytes(b"")
    s = get_settings()
    monkeypatch.setattr(s, "chroma_seed_dir", seed)
    monkeypatch.setattr(s, "chroma_dir", tmp_path / "tmp-chroma")
    monkeypatch.setattr(store, "VectorStore", lambda path: ("store", path))
    store.get_store.cache_clear()
    try:
        assert store.get_store() == ("store", tmp_path / "tmp-chroma")
        assert (tmp_path / "tmp-chroma" / "chroma.sqlite3").exists()
    finally:
        store.get_store.cache_clear()
