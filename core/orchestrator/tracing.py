"""
Langfuse v2 observability integration for Synapse.

v2 API:
  - client.trace(name, session_id, user_id, input, metadata, tags) → trace
  - trace.generation(name, model, input, metadata) → generation
  - generation.end(output, usage)  # usage: {prompt_tokens, completion_tokens, total_tokens}
  - trace.span(name, input, metadata) → span
  - span.end(output)
  - client.flush()
"""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("synapse.tracing")

_client = None
_init_done = False

_flush_event = threading.Event()
_flush_thread: Optional[threading.Thread] = None
_flush_lock = threading.Lock()


def _get_client():
    global _client, _init_done
    if _init_done:
        return _client
    try:
        from app.config import settings
        if not (settings.langfuse_public_key and settings.langfuse_secret_key):
            logger.debug("Langfuse not configured — tracing disabled")
            _init_done = True
            return None
        from langfuse import Langfuse
        host = settings.langfuse_base_url or settings.langfuse_host
        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=host,
        )
        logger.info("Langfuse tracing enabled → %s", host)
        _init_done = True
    except ImportError:
        logger.warning("langfuse package not installed — tracing disabled")
        _init_done = True
    except Exception as exc:
        logger.warning("Langfuse init failed — tracing disabled: %s", exc)
    return _client


# ---------------------------------------------------------------------------
# No-op stubs
# ---------------------------------------------------------------------------

class _NoopSpan:
    trace_id: Optional[str] = None

    def start_generation(self, *_, **__) -> "_NoopSpan":
        return _NOOP

    def start_span(self, *_, **__) -> "_NoopSpan":
        return _NOOP

    def update(self, **_) -> "_NoopSpan":
        return _NOOP

    def end(self, **_) -> "_NoopSpan":
        return _NOOP


_NOOP = _NoopSpan()


# ---------------------------------------------------------------------------
# TraceHandle
# ---------------------------------------------------------------------------

class TraceHandle:
    def __init__(self, trace, client):
        self._trace = trace
        self._client = client

    @property
    def trace_id(self) -> Optional[str]:
        try:
            return self._trace.id
        except Exception:
            return None

    def start_generation(
        self,
        name: str,
        *,
        model: Optional[str] = None,
        input: Optional[Any] = None,
        metadata: Optional[dict] = None,
    ) -> "_GenHandle":
        try:
            kwargs: dict[str, Any] = {"name": name}
            if model:
                kwargs["model"] = model
            if input is not None:
                kwargs["input"] = input
            if metadata:
                kwargs["metadata"] = metadata
            gen = self._trace.generation(**kwargs)
            return _GenHandle(gen)
        except Exception as exc:
            logger.warning("Failed to start generation span: %s", exc)
            return _NOOP  # type: ignore[return-value]

    def start_span(
        self,
        name: str,
        *,
        input: Optional[Any] = None,
        metadata: Optional[dict] = None,
    ) -> "_SpanHandle":
        try:
            kwargs: dict[str, Any] = {"name": name}
            if input is not None:
                kwargs["input"] = input
            if metadata:
                kwargs["metadata"] = metadata
            span = self._trace.span(**kwargs)
            return _SpanHandle(span)
        except Exception as exc:
            logger.warning("Failed to start tool span: %s", exc)
            return _NOOP  # type: ignore[return-value]

    def update(self, *, output: Optional[Any] = None, metadata: Optional[dict] = None):
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata:
                kwargs["metadata"] = metadata
            if kwargs:
                self._trace.update(**kwargs)
        except Exception as exc:
            logger.warning("Failed to update trace: %s", exc)

    def end(self, *, output: Optional[Any] = None):
        try:
            if output is not None:
                self._trace.update(output=output)
        except Exception as exc:
            logger.warning("Failed to end trace: %s", exc)


class _GenHandle:
    def __init__(self, gen):
        self._gen = gen

    def end(self, *, output: Optional[Any] = None, usage: Optional[dict] = None):
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if usage:
                kwargs["usage"] = {
                    "prompt_tokens": usage.get("input", 0),
                    "completion_tokens": usage.get("output", 0),
                    "total_tokens": usage.get("input", 0) + usage.get("output", 0),
                }
            self._gen.end(**kwargs)
        except Exception as exc:
            logger.warning("Failed to end generation span: %s", exc)


class _SpanHandle:
    def __init__(self, span):
        self._span = span

    def end(self, *, output: Optional[Any] = None):
        try:
            self._span.end(output=output)
        except Exception as exc:
            logger.warning("Failed to end span: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_trace(
    name: str,
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    input: Optional[Any] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list] = None,
) -> TraceHandle:
    client = _get_client()
    if client is None:
        return _NOOP  # type: ignore[return-value]
    try:
        kwargs: dict[str, Any] = {"name": name}
        if session_id:
            kwargs["session_id"] = session_id
        if user_id:
            kwargs["user_id"] = user_id
        if input is not None:
            kwargs["input"] = input
        if metadata:
            kwargs["metadata"] = metadata
        if tags:
            kwargs["tags"] = tags
        trace = client.trace(**kwargs)
        return TraceHandle(trace, client)
    except Exception as exc:
        logger.warning("Failed to create Langfuse trace: %s", exc)
        return _NOOP  # type: ignore[return-value]


def flush(blocking: bool = False):
    client = _get_client()
    if client is None:
        return
    if blocking:
        try:
            client.flush()
        except Exception as exc:
            logger.warning("Langfuse flush failed: %s", exc)
    else:
        _ensure_flush_thread(client)
        _flush_event.set()


def _ensure_flush_thread(client):
    global _flush_thread
    with _flush_lock:
        if _flush_thread is not None and _flush_thread.is_alive():
            return
        _flush_thread = threading.Thread(
            target=_flush_loop, args=(client,), daemon=True)
        _flush_thread.start()


def _flush_loop(client):
    while True:
        _flush_event.wait()
        _flush_event.clear()
        try:
            client.flush()
        except Exception as exc:
            logger.warning("Langfuse background flush failed: %s", exc)
