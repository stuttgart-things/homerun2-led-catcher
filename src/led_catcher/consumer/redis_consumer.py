"""Redis Streams consumer with consumer group support."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Callable

import redis.asyncio as aioredis

from led_catcher.config import Config
from led_catcher.models import CaughtMessage, Message

logger = logging.getLogger(__name__)

MessageHandler = Callable[[CaughtMessage], None]


class RedisConsumer:
    """Consumes messages from Redis Streams using consumer groups.

    Follows the homerun2 pattern:
    1. XREADGROUP to consume from stream
    2. Extract messageID from stream entry
    3. JSON.GET to resolve full payload from Redis JSON
    4. Call registered handlers
    5. XACK to acknowledge
    """

    def __init__(self, cfg: Config, handlers: list[MessageHandler]) -> None:
        self._cfg = cfg
        self._handlers = handlers
        self._running = False
        self._client: aioredis.Redis | None = None

    async def _connect(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.Redis(
                host=self._cfg.redis.addr,
                port=self._cfg.redis.port,
                password=self._cfg.redis.password or None,
                decode_responses=True,
            )
        return self._client

    async def _ensure_group(self, client: aioredis.Redis) -> None:
        stream = self._cfg.redis.stream
        group = self._cfg.consumer_group
        try:
            groups = await client.xinfo_groups(stream)
            if not any(g["name"] == group for g in groups):
                await client.xgroup_create(stream, group, id="0", mkstream=True)
                logger.info("created consumer group %s on stream %s", group, stream)
        except aioredis.ResponseError:
            await client.xgroup_create(stream, group, id="0", mkstream=True)
            logger.info("created consumer group %s on stream %s (new stream)", group, stream)

    async def _resolve_payload(self, client: aioredis.Redis, message_id: str) -> Message | None:
        try:
            result = await client.execute_command("JSON.GET", message_id, "$")
            if result is None:
                logger.warning("JSON.GET returned None for %s", message_id)
                return None
            import json

            data = json.loads(result)
            if isinstance(data, list) and len(data) > 0:
                return Message.from_dict(data[0])
            return Message.from_dict(data)
        except Exception:
            logger.exception("failed to resolve payload for %s", message_id)
            return None

    async def run(self) -> None:
        client = await self._connect()
        await self._ensure_group(client)

        stream = self._cfg.redis.stream
        group = self._cfg.consumer_group
        consumer = self._cfg.consumer_name

        logger.info(
            "consumer starting",
            extra={"redis_addr": self._cfg.redis.addr, "stream": stream, "consumer_group": group},
        )

        self._running = True
        while self._running:
            try:
                entries = await client.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={stream: ">"},
                    count=10,
                    block=5000,
                )
                if not entries:
                    continue

                for _stream_name, messages in entries:
                    for entry_id, fields in messages:
                        await self._process_entry(client, stream, group, entry_id, fields)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("error reading from stream")
                await asyncio.sleep(2)

    async def _process_entry(
        self,
        client: aioredis.Redis,
        stream: str,
        group: str,
        entry_id: str,
        fields: dict,
    ) -> None:
        message_id = fields.get("messageID", "")
        if not message_id:
            logger.warning("stream entry %s has no messageID field", entry_id)
            await client.xack(stream, group, entry_id)
            return

        msg = await self._resolve_payload(client, message_id)
        if msg is None:
            await client.xack(stream, group, entry_id)
            return

        caught = CaughtMessage(
            message=msg,
            object_id=message_id,
            stream_id=entry_id,
            caught_at=datetime.now(),
        )

        for handler in self._handlers:
            try:
                handler(caught)
            except Exception:
                logger.exception("handler error")

        await client.xack(stream, group, entry_id)

    async def shutdown(self) -> None:
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("consumer shut down")
