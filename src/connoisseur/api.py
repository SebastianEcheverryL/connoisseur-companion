"""HTTP API: chat with server-sent events, the MCP server over Streamable HTTP, and a health check.

Run locally:  uvicorn connoisseur.api:app --reload   (dev UI at /ui, MCP at /mcp/)
"""
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from connoisseur import __version__, observability
from connoisseur.agents.runner import run_chat, stream_chat
from connoisseur.log import configure_logging, get_logger
from connoisseur.retrieval.store import get_store
from connoisseur.schemas import Coordinates
from connoisseur.server import mcp
from connoisseur.settings import get_settings

configure_logging()
log = get_logger(__name__)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    location: Coordinates | None = Field(default=None, description="Optional browser geolocation")
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[\w-]+$")


class FeedbackRequest(BaseModel):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$", description="trace_id returned with the answer")
    value: Literal[0, 1] = Field(description="1 = helpful, 0 = not helpful")
    comment: str | None = Field(default=None, max_length=1000)


mcp_app = mcp.http_app(path="/", stateless_http=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp_app.lifespan(app):
        yield
    observability.flush()  # do not lose the last traces on shutdown


app = FastAPI(title="Connoisseur Companion API", version=__version__, lifespan=lifespan)
app.mount("/mcp", mcp_app)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    start = time.perf_counter()
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    log.info("http_request", method=request.method, status=response.status_code,
             ms=round((time.perf_counter() - start) * 1000))
    return response


@app.get("/health")
def health() -> dict:
    s = get_settings()
    store = get_store()
    return {
        "status": "ok",
        "version": __version__,
        "llm_provider": s.llm_provider,
        "vector_index": store is not None,
        "tracing": observability.is_enabled(),
    }


def _history(req: ChatRequest) -> list[dict]:
    return [turn.model_dump() for turn in req.history]


async def _sse(req: ChatRequest) -> AsyncIterator[str]:
    try:
        async for event in stream_chat(req.message, _history(req), req.location, session_id=req.session_id):
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception:  # report the failure to the client instead of cutting the stream
        log.exception("chat_failed")
        error = {"type": "error", "message": "Something went wrong while answering. Please try again."}
        yield f"event: error\ndata: {json.dumps(error)}\n\n"
    yield "event: done\ndata: {}\n\n"


@app.post("/chat")
async def chat(req: ChatRequest):
    """Stream progress (`step`) and the final `answer` as server-sent events."""
    if req.session_id:
        structlog.contextvars.bind_contextvars(session_id=req.session_id)
    return StreamingResponse(_sse(req), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/chat/sync")
async def chat_sync(req: ChatRequest) -> dict:
    """Same as /chat but returns only the final answer as JSON (handy for scripts and evals)."""
    return await run_chat(req.message, _history(req), req.location, session_id=req.session_id)


@app.post("/feedback")
def feedback(req: FeedbackRequest) -> dict:
    """Thumbs up/down from the UI, stored as a `user_feedback` score on the answer's trace."""
    if not observability.score(req.trace_id, "user_feedback", float(req.value), comment=req.comment,
                               data_type="BOOLEAN"):
        raise HTTPException(status_code=503, detail="Tracing is not configured.")
    return {"status": "recorded"}


def _mount_dev_ui() -> None:
    if not get_settings().enable_dev_ui:
        return
    try:
        import gradio as gr

        from connoisseur.app import build_demo
    except ImportError:
        log.info("dev_ui_disabled", reason="gradio not installed (pip install -e '.[ui]')")
        return
    gr.mount_gradio_app(app, build_demo(), path="/ui")


_mount_dev_ui()
