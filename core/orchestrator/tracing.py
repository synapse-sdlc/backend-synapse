"""
Langfuse v4 observability integration for Synapse.

v4 API overview:
  - No client.trace() method. The root observation IS the trace.
  - client.start_observation(name, as_type="span"|"generation", ...) → span/gen object
  - Child observations use trace_context={"trace_id": parent.trace_id}
  - span.update(output=..., usage_details={"input": N, "output": N})
  - span.end()
  - client.flush()

Usage in loop.py:
    from core.orchestrator.tracing import start_trace, flush

    trace = start_trace("agent-loop:spec-drafting", session_id=..., user_id=..., input=...)
    gen = trace.start_generation("llm-turn-1", model="claude", input=[...])
    gen.end(output={...}, usage={"input": 100, "output": 50})
    tool_span = trace.start_span("tool:read_file", input={...})
    tool_span.end(output={...})
    trace.end(output={...})
    flush()

All functions are no-ops when keys are not configured.
"""

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("synapse.tracing")

_client = None
_init_done = False

# Single background flusher — reused across calls to avoid thread-per-flush
_flush_event = threading.Event()
_flush_thread: Optional[threading.Thread] = None
_flush_lock = threading.Lock()


def _get_client():
    """Lazy-init the Langfuse v4 client; returns None when not configured."""
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
        # Don't set _init_done so next call retries
    return _client


# ---------------------------------------------------------------------------
# No-op stubs — absorb all calls when Langfuse is disabled
# ---------------------------------------------------------------------------

class _NoopSpan:
    """Stub returned when Langfuse is disabled or an operation fails."""

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
# TraceHandle — wraps the root observation and exposes child helpers
# ---------------------------------------------------------------------------

class TraceHandle:
    """
    Wraps a root Langfuse v4 observation (the trace root span).
    Exposes helper methods to create child generations and spans that are
    automatically linked to this trace via trace_context.
    """

    def __init__(self, root_span, client):
        self._root = root_span
        self._client = client

    @property
    def trace_id(self) -> Optional[str]:
        try:
            return self._root.trace_id
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
        """Open a generation child span (LLM call)."""
        try:
            tid = self.trace_id
            kwargs: dict[str, Any] = {"name": name, "as_type": "generation"}
            if tid:
                kwargs["trace_context"] = {"trace_id": tid}
            if model:
                kwargs["model"] = model
            if input is not None:
                kwargs["input"] = input
            if metadata:
                kwargs["metadata"] = metadata
            span = self._client.start_observation(**kwargs)
            return _GenHandle(span)
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
        """Open a generic child span (tool call, etc.)."""
        try:
            tid = self.trace_id
            kwargs: dict[str, Any] = {"name": name, "as_type": "span"}
            if tid:
                kwargs["trace_context"] = {"trace_id": tid}
            if input is not None:
                kwargs["input"] = input
            if metadata:
                kwargs["metadata"] = metadata
            span = self._client.start_observation(**kwargs)
            return _SpanHandle(span)
        except Exception as exc:
            logger.warning("Failed to start tool span: %s", exc)
            return _NOOP  # type: ignore[return-value]

    def update(self, *, output: Optional[Any] = None, metadata: Optional[dict] = None):
        """Update root trace output/metadata."""
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if metadata:
                kwargs["metadata"] = metadata
            if kwargs:
                self._root.update(**kwargs)
        except Exception as exc:
            logger.warning("Failed to update trace: %s", exc)

    def end(self, *, output: Optional[Any] = None):
        """Close the root trace span."""
        try:
            if output is not None:
                self._root.update(output=output)
            self._root.end()
        except Exception as exc:
            logger.warning("Failed to end trace: %s", exc)


class _GenHandle:
    """Wraps a Langfuse generation observation."""

    def __init__(self, span):
        self._span = span

    def end(self, *, output: Optional[Any] = None, usage: Optional[dict] = None):
        """
        Close the generation span.
        usage dict: {"input": N, "output": N}  (token counts)
        """
        try:
            kwargs: dict[str, Any] = {}
            if output is not None:
                kwargs["output"] = output
            if usage:
                # v4 expects usage_details={"input": N, "output": N}
                kwargs["usage_details"] = {
                    "input": usage.get("input", 0),
                    "output": usage.get("output", 0),
                }
            if kwargs:
                self._span.update(**kwargs)
            self._span.end()
        except Exception as exc:
            logger.warning("Failed to end generation span: %s", exc)


class _SpanHandle:
    """Wraps a Langfuse generic observation."""

    def __init__(self, span):
        self._span = span

    def end(self, *, output: Optional[Any] = None):
        try:
            if output is not None:
                self._span.update(output=output)
            self._span.end()
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
    """
    Create a root Langfuse trace (top-level observation).
    Returns a TraceHandle or a no-op stub when Langfuse is disabled.
    session_id, user_id, and tags are stored in metadata since v4
    doesn't have dedicated top-level fields for them on observations.
    """
    client = _get_client()
    if client is None:
        return _NOOP  # type: ignore[return-value]
    try:
        _meta: dict[str, Any] = dict(metadata or {})
        if session_id:
            _meta["session_id"] = session_id
        if user_id:
            _meta["user_id"] = user_id
        if tags:
            _meta["tags"] = tags
        kwargs: dict[str, Any] = {"name": name, "as_type": "span"}
        if input is not None:
            kwargs["input"] = input
        if _meta:
            kwargs["metadata"] = _meta
        root = client.start_observation(**kwargs)
        return TraceHandle(root, client)
    except Exception as exc:
        logger.warning("Failed to create Langfuse trace: %s", exc)
        return _NOOP  # type: ignore[return-value]


def flush(blocking: bool = False):
    """Flush all buffered Langfuse events to the server.

    Non-blocking by default (signals a single background daemon thread)
    so it doesn't hold up the agent loop between turns.
    Use blocking=True at process/task shutdown so nothing is lost.
    """
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
    """Start the single background flush thread if it isn't running."""
    global _flush_thread
    with _flush_lock:
        if _flush_thread is not None and _flush_thread.is_alive():
            return
        _flush_thread = threading.Thread(
            target=_flush_loop, args=(client,), daemon=True)
        _flush_thread.start()


def _flush_loop(client):
    """Background loop: wait for signal, then flush."""
    while True:
        _flush_event.wait()
        _flush_event.clear()
        try:
            client.flush()
        except Exception as exc:
            logger.warning("Langfuse background flush failed: %s", exc)
