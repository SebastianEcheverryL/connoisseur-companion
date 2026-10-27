"""Tracing with Langfuse (OpenTelemetry-based). Everything here is a no-op when Langfuse is not configured.

One chat request = one trace:
  chat (agent)
   ├─ LangGraph nodes and LLM generations   <- LangChain callback handler
   ├─ retrieval / geocode / places / ratings <- @traced spans
   └─ MCP tool calls                          <- @traced spans

Sensitive data is masked before it leaves the process (see `mask_sensitive`).
"""
import asyncio
import functools
import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from connoisseur import __version__
from connoisseur.log import get_logger
from connoisseur.security.pii import redact
from connoisseur.settings import get_settings

log = get_logger(__name__)

REDACTED = "[redacted]"
# Keys whose values must never reach the tracing backend. Any lat/lon is dropped: a user's position
# can hide inside a geocoding input or output, and place coordinates add nothing to a trace.
SENSITIVE_KEYS = {"coords", "lat", "lon", "api_key", "authorization", "password", "secret", "tavily_api_key"}
def mask_sensitive(data: Any = None, **_: Any) -> Any:
    """Recursively redact user coordinates, secrets, emails and phone numbers."""
    if isinstance(data, str):
        return redact(data)
    if isinstance(data, dict):
        return {k: REDACTED if str(k).lower() in SENSITIVE_KEYS else mask_sensitive(v) for k, v in data.items()}
    if isinstance(data, list | tuple):
        return [mask_sensitive(v) for v in data]
    return data


def is_enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_public_key and s.langfuse_secret_key)


@lru_cache
def get_langfuse():
    """The Langfuse client, or None when tracing is disabled."""
    if not is_enabled():
        return None
    from langfuse import Langfuse

    s = get_settings()
    client = Langfuse(
        public_key=s.langfuse_public_key,
        secret_key=s.langfuse_secret_key.get_secret_value(),
        base_url=s.langfuse_host,
        environment=s.environment,
        release=__version__,
        sample_rate=s.langfuse_sample_rate,
        mask=mask_sensitive,
    )
    log.info("langfuse_enabled", host=s.langfuse_host)
    return client


def langchain_callbacks() -> list:
    """Callback handlers to pass in a LangChain/LangGraph `config`."""
    if get_langfuse() is None:
        return []
    from langfuse.langchain import CallbackHandler

    return [CallbackHandler(public_key=get_settings().langfuse_public_key)]


@dataclass
class TraceHandle:
    trace_id: str | None = None
    _span: Any = None

    def finish(self, output: Any = None, **metadata: Any) -> None:
        if self._span is not None:
            self._span.update(output=output, metadata=metadata or None)


@contextmanager
def trace_request(
    name: str,
    *,
    input: Any = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
) -> Iterator[TraceHandle]:
    """Open the root observation of a request and propagate user/session ids to every child span."""
    lf = get_langfuse()
    if lf is None:
        yield TraceHandle()
        return
    from langfuse import propagate_attributes

    with (
        lf.start_as_current_observation(name=name, as_type="agent", input=input) as span,
        propagate_attributes(user_id=user_id, session_id=session_id, tags=tags, trace_name=name),
    ):
        yield TraceHandle(trace_id=lf.get_current_trace_id(), _span=span)


def traced(name: str | None = None, as_type: str = "span"):
    """Decorator: record the call as a child observation (input = arguments, output = result)."""

    def decorator(fn):
        span_name = name or fn.__name__

        def _input(args, kwargs):
            return {"args": [_summarise(a) for a in args], **{k: _summarise(v) for k, v in kwargs.items()}}

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                lf = get_langfuse()
                if lf is None:
                    return await fn(*args, **kwargs)
                with lf.start_as_current_observation(name=span_name, as_type=as_type,
                                                     input=_input(args, kwargs)) as span:
                    result = await fn(*args, **kwargs)
                    span.update(output=_summarise(result))
                    return result

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            lf = get_langfuse()
            if lf is None:
                return fn(*args, **kwargs)
            with lf.start_as_current_observation(name=span_name, as_type=as_type,
                                                 input=_input(args, kwargs)) as span:
                result = fn(*args, **kwargs)
                span.update(output=_summarise(result))
                return result

        return sync_wrapper

    return decorator


def _summarise(value: Any, max_items: int = 10) -> Any:
    """Make values JSON-friendly and bounded before they are attached to a span."""
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list | tuple):
        items = [_summarise(v) for v in value[:max_items]]
        return items + [f"... {len(value) - max_items} more"] if len(value) > max_items else items
    if isinstance(value, dict):
        return {k: _summarise(v) for k, v in value.items()}
    return value


def score(trace_id: str, name: str, value: float | str, comment: str | None = None,
          data_type: str | None = None) -> bool:
    """Attach a score (e.g. user feedback) to a trace. Returns False when tracing is disabled."""
    lf = get_langfuse()
    if lf is None:
        return False
    lf.create_score(trace_id=trace_id, name=name, value=value, comment=comment, data_type=data_type)
    return True


def check_credentials() -> bool | None:
    """Ask Langfuse whether the keys are valid (None = tracing disabled).

    Without this, wrong keys or the wrong region only show up as "Failed to export spans batch code: 401".
    It makes one blocking HTTP call: run it once at startup, off the event loop.
    """
    lf = get_langfuse()
    if lf is None:
        return None
    host = get_settings().langfuse_host
    try:
        ok, error = lf.auth_check(), None
    except Exception as exc:  # noqa: BLE001 - the SDK raises on 401/403 and network errors
        ok, error = False, str(exc)[:200]
    if ok:
        log.info("langfuse_auth_ok", host=host)
    else:
        log.error("langfuse_auth_failed", host=host, error=error,
                  hint="check the key pair and that the host matches the project region (EU or US)")
    return ok


def flush() -> None:
    """Send buffered spans now. On Lambda this must happen before the response ends: once it is sent,
    the environment is frozen and the spans would wait (or be lost) until the next invocation."""
    lf = get_langfuse()
    if lf is None:
        return
    try:
        lf.flush()
    except Exception as exc:  # noqa: BLE001 - tracing must never break an answer
        log.warning("langfuse_flush_failed", error=str(exc)[:200])


async def aflush() -> None:
    """`flush` without blocking the event loop."""
    if get_langfuse() is not None:
        await asyncio.to_thread(flush)
