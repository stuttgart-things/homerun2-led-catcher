"""Runtime control endpoints — inspect and switch the subscribed Redis streams."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from led_catcher.consumer import RedisConsumer

logger = logging.getLogger(__name__)


class StreamsRequest(BaseModel):
    """Body of ``POST /streams``."""

    model_config = ConfigDict(populate_by_name=True)

    streams: list[str]
    skip_backlog: bool = Field(default=True, alias="skipBacklog")


def create_control_router(consumer: RedisConsumer) -> APIRouter:
    """Build the ``/streams`` router bound to a running consumer."""
    router = APIRouter()

    @router.get("/streams")
    async def get_streams() -> dict:
        return {
            "streams": consumer.streams,
            "consumerGroup": consumer.consumer_group,
            "consumerName": consumer.consumer_name,
        }

    @router.post("/streams")
    async def post_streams(body: StreamsRequest) -> dict:
        try:
            return await consumer.set_streams(body.streams, skip_backlog=body.skip_backlog)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("failed to switch streams")
            raise HTTPException(status_code=503, detail=f"failed to switch streams: {exc}") from exc

    return router
