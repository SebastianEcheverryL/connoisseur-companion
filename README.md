# Connoisseur Companion

An AI assistant that recommends restaurants anywhere in the world and recipes to cook at home. A **LangGraph multi-agent workflow** understands the request, searches a curated California guide with **multimodal RAG** or real places nearby on **OpenStreetMap**, has three expert agents review the options in parallel, and returns **grounded** recommendations. The same data is exposed through an **MCP (Model Context Protocol) server**.

Everything runs locally with [Ollama](https://ollama.com): no paid LLM API is needed. The provider layer can switch to Amazon Bedrock for cloud deployment.

![Connoisseur Companion chat UI](docs/screenshots/chat-ui.jpg)

## Highlights

- **Profile agent + router.** One structured LLM call extracts intent, location, cuisines, diet, vibe, budget and language, then routes to the right branch.
- **Web-search agent.** Outside California (or when the guide has too few matches) it geocodes the location with Nominatim, finds restaurants with Overpass (falling back to Photon when Overpass is busy), ranks them by cuisine and `diet:*` tags, and, with a Tavily key, researches ratings for the top places on review sites. It is deterministic on purpose: web content never decides which tool runs next.
- **Researched ratings, not generated ones.** Ratings for web places are parsed with regular expressions from review snippets that mention the place by name, normalised to a 0-5 scale, averaged with one value per site, and shown with their source and link (e.g. "⭐ 4.6/5 Tripadvisor, 1,234 reviews"). The LLM never produces a rating.
- **"Near me" that is honest.** Browser geolocation is optional and rounded to ~1 km before it leaves the page. Without a position, the agent asks where you are instead of answering from the California guide, and a place named in the message wins over the device position.
- **Multimodal RAG.** MiniLM text embeddings and CLIP image embeddings (ONNX via `fastembed`, no torch) in ChromaDB. Recipes are ranked by a **fusion reranker** that merges text and image similarity per recipe.
- **Grounded answers.** The recommender may only cite retrieved candidate ids; any invented place is dropped before the answer is shown, and every pick shows its source.
- **MCP server.** 5 read-only tools and 1 resource, served in-process to the agents and over Streamable HTTP at `/mcp/` for any MCP client.
- **Production-minded.** Typed settings, provider factory (Ollama / Bedrock), structured logs with request ids, SSE streaming API, HTTP caching and retries for public OSM servers, and a `pytest` suite with the LLM and the network faked.

## Architecture

```mermaid
flowchart TD
    U[User message<br/>+ optional geolocation] --> P[Profile agent<br/>intent, location, preferences]
    P -->|help / out of scope| R[Fixed reply]
    P -->|specific restaurant| T[ReAct tool agent<br/>MCP tools in-process]
    P -->|recipes| L
    P -->|restaurants| G[Locate<br/>Nominatim]
    G -->|near me without a position| A[Ask where you are]
    G -->|California or no location| L[Local retrieval<br/>ChromaDB + fusion]
    G -->|elsewhere| W[Web-search agent<br/>Overpass / Photon + Tavily]
    L -->|too few results| W
    L --> E{{Trend ∥ Style ∥ Nutrition experts}}
    W --> E
    E --> C[Recommender<br/>grounded picks + sources]
```

```
FastAPI  ── POST /chat (SSE)   ── LangGraph workflow ── Ollama | Bedrock
         ── POST /chat/sync
         ── /mcp/  (MCP Streamable HTTP)
         ── /ui    (Gradio developer UI)
         ── GET /health
```

## Repository layout

```
src/connoisseur/
  api.py              FastAPI app: /chat (SSE), /chat/sync, /mcp, /ui, /health
  agents/             LangGraph graph, nodes, ReAct tool agent, runner, versioned prompts (*.md)
  retrieval/          fastembed embeddings, ChromaDB store, fusion reranker, search service
  places/             OpenStreetMap (Nominatim + Overpass), Tavily, TTL cache
  server.py           FastMCP server: 5 read-only tools + culinary map resource
  llm.py              provider factory (Ollama / Bedrock)
  settings.py         typed settings from env vars / .env
  schemas.py          shared Pydantic contracts
  app.py              Gradio developer UI
  client.py           MCP client demo: tool calls, sampling and roots
  data_management.py  LLM structuring with a self-repair loop + console CRUD
scripts/              download_data.py, build_index.py
notebooks/            the original step-by-step pipeline, 01 extraction -> 08 chatbot
tests/                pytest suite
```

## Quickstart

Requirements: Python 3.12+ and [Ollama](https://ollama.com) running locally.

```bash
ollama pull llama3.1 && ollama pull llama3

python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[ui]"                                  # API + developer UI
cp .env.example .env                                    # optional settings (Tavily key, models...)

python scripts/download_data.py --with-images           # raw IBM datasets (see Credits)
python scripts/build_index.py                           # vector index in .chroma/ (~1 min, CPU)

uvicorn connoisseur.api:app --reload                    # http://localhost:8000/ui
```

Other entry points:

```bash
curl -N localhost:8000/chat -H "content-type: application/json" \
     -d '{"message": "a quiet vegan cafe in El Poblado, Medellín"}'
python -m connoisseur.client                            # MCP client demo (sampling runs on Ollama)
python -m connoisseur.data_management                   # console tool to browse/add/edit restaurants
```

### Tests

```bash
pip install -e ".[dev]"
pytest              # LLM and HTTP are faked; index tests run only if .chroma/ exists
ruff check src tests scripts
```

## Technical notes

- **Why required fields in LLM schemas.** With schema-constrained decoding, a schema whose fields are all optional lets a small model answer `{}`. The extraction and recommendation schemas make every field required (nullable where needed), which fixed misrouted intents with `llama3.1`.
- **Why fixed help text.** An earlier version let the LLM answer greetings freely; it invented a restaurant with an address and a phone number. Help and refusals are now fixed text in English or Spanish.
- **Why a deterministic web agent.** Letting an LLM pick tools based on web pages invites indirect prompt injection. The web branch always runs the same steps, and web text reaches the recommender wrapped as untrusted data.
- **Why ONNX embeddings.** `fastembed` produces the same MiniLM and CLIP vectors as the notebooks without torch, which keeps the future Lambda container small.
- **Why per-entity fusion.** The notebook ranked text and image hits as separate rows. The package merges them per recipe, so a recipe that matches by text and by image ranks higher.
- **Being a good OSM citizen.** Nominatim calls are throttled to 1 per second, results are cached (geocoding for a month, places for a day), Overpass falls back across public mirrors with backoff, a circuit breaker skips Overpass for 5 minutes after it fails (Photon answers meanwhile), and answers credit OpenStreetMap contributors.

## Known limitations

- `price_range` comes from LLM extraction and is imperfect: some `$$$$` entries were recorded as 3, and 42 of the 201 restaurants have no price (`null`). The dataset-quality tests check ranges and schema, not factual accuracy.
- The source text contains a few duplicated restaurant descriptions, so some names appear twice.
- Answer quality depends on the local models you run; smaller models make more extraction and tool-calling mistakes.
- With the three expert agents enabled, a full recommendation takes about 1 minute on a laptop with `llama3.1` 8B. Set `ENABLE_EXPERT_ANALYSIS=false` for faster answers.
- OpenStreetMap coverage varies by city: some places have no cuisine or diet tags, which limits ranking.

## Credits

This project grew out of the IBM **RAG and Agentic AI** course (Coursera). The dataset sources (`California-Culinary-Map.txt`, `Recipes.json`, `Synthetic-User-Reviews.json` and the synthetic recipe images) belong to IBM and are **not redistributed**; `scripts/download_data.py` fetches them from their original location. `data/structured_restaurant_data.json` and `data/augmented_user_review.json` are derived outputs produced by running the notebooks with local models. The course provided the scenario, datasets and lab scaffolding; the code here was reworked to run fully locally with Ollama and extended with the packaging, the production API, real retrieval in the agent workflow (the course notebook simulated it with the LLM), the web-search agent, tests, CI and dataset-quality checks. Place data from OpenStreetMap is © OpenStreetMap contributors, available under the ODbL.

Licensed under the [MIT License](LICENSE).
