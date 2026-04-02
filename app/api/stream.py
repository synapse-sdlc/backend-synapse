import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.config import settings

router = APIRouter()


@router.get("/features/{feature_id}/stream")
async def stream_events(feature_id: str):
    """SSE endpoint that streams agent progress events from Redis pub/sub."""

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
                    # Send keepalive every second to prevent connection timeout
                    yield {"comment": "keepalive"}
                await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(f"feature:{feature_id}")
            await r.aclose()

    return EventSourceResponse(event_generator())
