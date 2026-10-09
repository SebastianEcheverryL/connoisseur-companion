"""MCP client demo: tool calls, sampling (delegated to Claude) and roots."""
import asyncio
import json
import os
import sys
from pathlib import Path

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    Root,
    TextContent,
)

from connoisseur.config import PROJECT_DIR, SAMPLING_MODEL

SERVER_SCRIPT = str(Path(__file__).parent / "server.py")

# StdioServerParameters launches the server as a subprocess over stdin/stdout
server_params = StdioServerParameters(
    command=sys.executable,
    args=[SERVER_SCRIPT],
    env={**os.environ, "PYTHONPATH": str(PROJECT_DIR / "src")},
)

_anthropic_client: Anthropic | None = None


def get_anthropic_client() -> Anthropic:
    """Create the Anthropic client lazily so the module imports without an API key."""
    global _anthropic_client
    if _anthropic_client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. It is only needed when the server "
                "requests LLM sampling; copy .env.example to .env and set it."
            )
        _anthropic_client = Anthropic()
    return _anthropic_client


# ROOTS - tell the server which directories it can access
def list_roots() -> list[Root]:
    """Limit the server's file access to this project directory."""
    return [Root(uri=PROJECT_DIR.as_uri(), name=PROJECT_DIR.name)]


# SAMPLING - handle LLM requests delegated from the server
async def handle_sampling(params: CreateMessageRequestParams) -> CreateMessageResult:
    """Run a Claude call on behalf of the server and return the result."""
    prompt = params.messages[0].content.text

    print("\n[Sampling] Server requested LLM task:")
    print(f"  Prompt preview: {prompt[:150]}...")

    response = get_anthropic_client().messages.create(
        model=SAMPLING_MODEL,
        max_tokens=params.maxTokens or 200,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = response.content[0].text
    print(f"  LLM Response: {response_text[:100]}...")

    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=response_text),
        model=SAMPLING_MODEL,
    )


async def call_tool(tool_name: str, arguments: dict) -> dict:
    """Connect to the server, call a tool, and return the parsed JSON result."""
    async with stdio_client(server_params) as (read, write), ClientSession(
        read,
        write,
        sampling_callback=handle_sampling,
        list_roots_callback=list_roots,
    ) as session:
        await session.initialize()
        result = await session.call_tool(tool_name, arguments=arguments)
        text = result.content[0].text
        if result.isError:
            # On failure the server sends a plain error message, not JSON
            raise RuntimeError(f"Tool '{tool_name}' failed: {text}")
        return json.loads(text)


async def verify_connection():
    """Connect to the server and verify all expected tools and resources exist."""
    print("=" * 60)
    print("MCP Connection Verification")
    print("=" * 60)

    async with stdio_client(server_params) as (read, write), ClientSession(
        read,
        write,
        sampling_callback=handle_sampling,
        list_roots_callback=list_roots,
    ) as session:
        await session.initialize()

        tools_result = await session.list_tools()
        tool_names = [tool.name for tool in tools_result.tools]
        print(f"\nDiscovered {len(tool_names)} tools:")
        for tool in tools_result.tools:
            print(f"  - {tool.name}: {tool.description[:80]}...")

        for expected in ("get_restaurant_info", "recommend_by_vibe", "get_review"):
            assert expected in tool_names, f"{expected} not found on the server"
        print("\nAll required tools verified!")

        resources_result = await session.list_resources()
        print(f"\nDiscovered {len(resources_result.resources)} resources:")
        for resource in resources_result.resources:
            print(f"  - {resource.uri}: {resource.name}")

        roots = list_roots()
        print(f"\nConfigured {len(roots)} roots:")
        for root in roots:
            print(f"  - {root.name}: {root.uri}")


async def demo_get_restaurant_info():
    print("\n" + "-" * 60)
    print("Demo: get_restaurant_info('Iron Griddle')")
    print("-" * 60)
    data = await call_tool("get_restaurant_info", {"restaurant_name": "Iron Griddle"})
    print(json.dumps(data, indent=2))


async def demo_recommend_by_vibe():
    print("\n" + "-" * 60)
    print("Demo: recommend_by_vibe('moody')")
    print("-" * 60)
    data = await call_tool("recommend_by_vibe", {"vibe": "moody"})
    print(f"Vibe: {data['vibe_searched']}")
    print(f"Structured matches: {len(data['structured_matches'])}")
    for match in data["structured_matches"]:
        print(f"  - {match['name']} ({match['food_style']}) - {match['rating']}/5")
    print(f"Raw text excerpts: {len(data['raw_text_excerpts'])}")


async def demo_get_review():
    print("\n" + "-" * 60)
    print("Demo: get_review('Sakura Garden')")
    print("-" * 60)
    data = await call_tool("get_review", {"restaurant_name": "Sakura Garden"})
    print(json.dumps(data, indent=2))


async def main():
    """Run all demos sequentially."""
    await demo_get_restaurant_info()
    await demo_recommend_by_vibe()
    await demo_get_review()
    await verify_connection()


if __name__ == "__main__":
    asyncio.run(main())
