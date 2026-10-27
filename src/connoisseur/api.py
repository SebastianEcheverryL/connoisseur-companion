"""HTTP API: chat with server-sent events, the MCP server over Streamable HTTP, and a health check.

Run locally:  uvicorn connoisseur.api:app --reload   (dev UI at /ui, MCP at /mcp/)
"""
import asyncio
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from connoisseur import __version__, memory, observability
from connoisseur.agents.runner import run_chat, stream_chat
from connoisseur.log import configure_logging, get_logger
from connoisseur.retrieval.store import get_store
from connoisseur.schemas import Coordinates
from connoisseur.security.quota import get_quota_service
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


# Fields of the runner's answer event that stay on the server (raw tool output is not for clients)
INTERNAL_FIELDS = {"evidence"}

mcp_app = mcp.http_app(path="/", stateless_http=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate the Langfuse keys in the background so a bad key is one clear log line, not a slower cold start
    auth_check = asyncio.create_task(asyncio.to_thread(observability.check_credentials))
    async with mcp_app.lifespan(app):
        yield
    await auth_check
    observability.flush()  # do not lose the last traces on shutdown


app = FastAPI(title="Connoisseur Companion API", version=__version__, lifespan=lifespan)
app.mount("/mcp", mcp_app)


def _token_ok(header: str | None, expected) -> bool:
    """Constant-time check of an `Authorization: Bearer <token>` header against a SecretStr (None = open)."""
    if expected is None:
        return True
    scheme, _, token = (header or "").partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.get_secret_value().encode())


CHAT_PATHS = ("/chat", "/feedback", "/memory", "/ui")


@app.middleware("http")
async def chat_routes_enabled(request: Request, call_next):
    """EXPOSE_CHAT=false turns this deployment into an MCP-only server (the optional public function on AWS)."""
    if not get_settings().expose_chat and request.url.path.startswith(CHAT_PATHS):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


@app.middleware("http")
async def mcp_auth(request: Request, call_next):
    """/mcp is open to any MCP client on the internet once deployed, so it requires MCP_TOKEN when set."""
    if request.url.path.startswith("/mcp") and not _token_ok(request.headers.get("authorization"),
                                                            get_settings().mcp_token):
        return JSONResponse({"detail": "Invalid or missing MCP token"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


def require_api_key(request: Request) -> None:
    if not _token_ok(request.headers.get("authorization"), get_settings().api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key",
                            headers={"WWW-Authenticate": "Bearer"})


def user_key(request: Request) -> str:
    """Who to count quota against. The trusted Vercel BFF sends the signed-in user id. X-User-Id is only
    honoured when the caller is authenticated: by API_KEY, or by the platform (TRUST_USER_HEADER, set on
    the AWS Lambda whose Function URL requires IAM auth). Otherwise fall back to the client IP."""
    s = get_settings()
    if (s.api_key is not None or s.trust_user_header) and request.headers.get("x-user-id"):
        return "user:" + request.headers["x-user-id"][:64]
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return "ip:" + (forwarded or (request.client.host if request.client else "unknown"))


def enforce_quota(request: Request) -> str:
    key = user_key(request)
    decision = get_quota_service().consume(key)
    if not decision.allowed:
        log.warning("quota_exceeded", reason=decision.reason, user=key)
        raise HTTPException(status_code=429, detail=decision.reason,
                            headers={"Retry-After": str(decision.retry_after_s)})
    return key


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


async def _sse(req: ChatRequest, user: str) -> AsyncIterator[str]:
    try:
        async for event in stream_chat(req.message, _history(req), req.location, session_id=req.session_id,
                                       user_id=user):
            event = {k: v for k, v in event.items() if k not in INTERNAL_FIELDS}
            yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
    except Exception:  # report the failure to the client instead of cutting the stream
        log.exception("chat_failed")
        error = {"type": "error", "message": "Something went wrong while answering. Please try again."}
        yield f"event: error\ndata: {json.dumps(error)}\n\n"
    # The answer is already on screen; send the trace before the stream ends and Lambda freezes
    await observability.aflush()
    yield "event: done\ndata: {}\n\n"


@app.post("/chat", dependencies=[Depends(require_api_key)])
async def chat(req: ChatRequest, user: str = Depends(enforce_quota)):
    """Stream progress (`step`) and the final `answer` as server-sent events."""
    if req.session_id:
        structlog.contextvars.bind_contextvars(session_id=req.session_id)
    return StreamingResponse(_sse(req, user), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/chat/sync", dependencies=[Depends(require_api_key)])
async def chat_sync(req: ChatRequest, user: str = Depends(enforce_quota)) -> dict:
    """Same as /chat but returns only the final answer as JSON (handy for scripts and evals)."""
    result = await run_chat(req.message, _history(req), req.location, session_id=req.session_id, user_id=user)
    await observability.aflush()
    return {k: v for k, v in result.items() if k not in INTERNAL_FIELDS}


@app.delete("/memory", dependencies=[Depends(require_api_key)])
def forget_memory(request: Request,
                  session_id: str | None = Query(default=None, max_length=64, pattern=r"^[\w-]+$")) -> dict:
    """"Forget my preferences": deletes the user's saved memory and this conversation's state."""
    memory.forget(user_key(request), session_id)
    log.info("memory_forgotten", conversation=session_id is not None)
    return {"status": "forgotten"}


@app.post("/feedback", dependencies=[Depends(require_api_key)])
def feedback(req: FeedbackRequest) -> dict:
    """Thumbs up/down from the UI, stored as a `user_feedback` score on the answer's trace."""
    if not observability.score(req.trace_id, "user_feedback", float(req.value), comment=req.comment,
                               data_type="BOOLEAN"):
        raise HTTPException(status_code=503, detail="Tracing is not configured.")
    observability.flush()
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
