"""Exercise the MCP server in memory through a real FastMCP client (no subprocess, no LLM)."""
import asyncio
import json

from fastmcp import Client

from connoisseur.server import mcp


def call(tool, **arguments):
    async def _run():
        async with Client(mcp) as client:
            result = await client.call_tool(tool, arguments)
            return json.loads(result.content[0].text)

    return asyncio.run(_run())


def test_server_exposes_expected_tools():
    async def _list():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert {"get_restaurant_info", "recommend_by_vibe", "get_review"} <= asyncio.run(_list())


def test_get_restaurant_info_found():
    data = call("get_restaurant_info", restaurant_name="Iron")
    assert data["status"] == "found"
    assert data["count"] >= 1


def test_get_restaurant_info_not_found():
    data = call("get_restaurant_info", restaurant_name="zzz-does-not-exist")
    assert data["status"] == "not_found"


def test_recommend_by_vibe_returns_structured_matches():
    data = call("recommend_by_vibe", vibe="moody")
    assert data["vibe_searched"] == "moody"
    assert data["structured_matches"]


def test_get_review_not_found():
    assert call("get_review", restaurant_name="zzz-does-not-exist")["status"] == "not_found"


def test_semantic_search_without_index_explains_how_to_build_it():
    data = call("semantic_search", query="ramen")
    assert data["status"] == "not_found"
    assert "build_index" in data["message"]


def test_tool_arguments_are_length_limited():
    async def _run():
        async with Client(mcp) as client:
            return await client.call_tool("get_review", {"restaurant_name": "x" * 500}, raise_on_error=False)

    assert asyncio.run(_run()).is_error
