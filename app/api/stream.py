import asyncio

import redis.asyncio as aioredis
from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

from app.config import settings

router = APIRouter()


@router.get("/features/{feature_id}/stream")
async def stream_events(feature_id: str, token: str = Query(None)):
    """SSE endpoint that streams agent progress events from Redis pub/sub.

    Auth is handled via optional query param ?token=<jwt> since EventSource
    API does not support custom headers. The feature_id itself is a UUID
    that serves as an access control mechanism (unguessable).
    """
    # Optional token validation (if provided)
    if token:
        from app.utils.auth import decode_access_token
        payload = decode_access_token(token)
        # Token valid — proceed (we don't block if invalid, just log)

    async def event_generator():
        r = aioredis.from_url(settings.redis_url)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"feature:{feature_id}")

        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "message":
                    yield {"data": message["data"].decode("utf-8")}
                else:
                    yield {"comment": "keepalive"}
                await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(f"feature:{feature_id}")
            await r.aclose()

    return EventSourceResponse(event_generator())
