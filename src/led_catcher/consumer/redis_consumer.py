"""Redis Streams consumer with consumer group support."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Callable

import redis.asyncio as aioredis

from led_catcher.config import Config
from led_catcher.models import CaughtMessage, Message

logger = logging.getLogger(__name__)

MessageHandler = Callable[[CaughtMessage], None]

BLOCK_MS = 5000
"""XREADGROUP block timeout — also the upper bound for a runtime stream switch to take effect."""

SOCKET_TIMEOUT_SECONDS = BLOCK_MS / 1000 + 5
"""How long a socket read may take, which has to outlast a blocking XREADGROUP.

redis-py defaults this to 5 seconds — exactly BLOCK_MS. The server answers a
blocking read with nil after BLOCK_MS, and the client gives up at the same
instant, so the read it asked for times out rather than returning empty. The
loop then lands in its error branch and only comes round after the retry
delay, which is why a stream switch took about a minute to take effect instead
of the documented BLOCK_MS (#56).

Measured against redis-py 8.1.0: block=4000 returns in 4.0s, block=5000 raises
after 59s, and block=5000 with this timeout set returns in 5.0s.
"""


STARTUP_ATTEMPT_TIMEOUT_SECONDS = 5.0
"""Upper bound for a single startup attempt (ping + consumer group setup)."""

STARTUP_BACKOFF_INITIAL_SECONDS = 1.0
STARTUP_BACKOFF_MAX_SECONDS = 16.0


class RedisNotReadyError(RuntimeError):
    """Redis did not answer within ``REDIS_STARTUP_TIMEOUT``."""


def normalize_streams(streams: list[str]) -> list[str]:
    """Trim, drop empties and de-duplicate while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in streams:
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


class RedisConsumer:
    """Consumes messages from Redis Streams using consumer groups.

    Follows the homerun2 pattern:
    1. XREADGROUP to consume from stream
    2. Extract messageID from stream entry
    3. JSON.GET to resolve full payload from Redis JSON
    4. Call registered handlers
    5. XACK to acknowledge

    The subscribed stream set is mutable state, not a read-through to ``Config``,
    so it can be replaced at runtime via :meth:`set_streams`.
    """

    def __init__(self, cfg: Config, handlers: list[MessageHandler]) -> None:
        self._cfg = cfg
        self._handlers = handlers
        self._running = False
        self._client: aioredis.Redis | None = None
        self._streams: list[str] = normalize_streams(cfg.redis.streams) or ["messages"]
        self._configured_streams: list[str] = list(self._streams)
        self._switch_lock = asyncio.Lock()
        self._state = "waiting_for_redis"

    @property
    def streams(self) -> list[str]:
        """The currently subscribed streams."""
        return list(self._streams)

    @property
    def configured_streams(self) -> list[str]:
        """The streams configured via the environment at startup.

        Never changed by :meth:`set_streams`, so the web simulator can tell an
        override apart from normal operation and offer a way back.
        """
        return list(self._configured_streams)

    @property
    def is_overridden(self) -> bool:
        """True when the active set differs from the environment configuration."""
        return self._streams != self._configured_streams

    @property
    def state(self) -> str:
        """``waiting_for_redis``, ``running``, ``stopped`` or ``failed``."""
        return self._state

    def mark_failed(self) -> None:
        """Record that the consumer task ended without a shutdown request."""
        if self._state != "stopped":
            self._state = "failed"

    @property
    def consumer_group(self) -> str:
        return self._cfg.consumer_group

    @property
    def consumer_name(self) -> str:
        return self._cfg.consumer_name

    async def _connect(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.Redis(
                host=self._cfg.redis.addr,
                port=self._cfg.redis.port,
                password=self._cfg.redis.password or None,
                decode_responses=True,
                socket_timeout=SOCKET_TIMEOUT_SECONDS,
            )
        return self._client

    async def _ensure_group(self, client: aioredis.Redis, streams: list[str]) -> None:
        group = self._cfg.consumer_group
        for stream in streams:
            try:
                groups = await client.xinfo_groups(stream)
                if not any(g["name"] == group for g in groups):
                    await client.xgroup_create(stream, group, id="0", mkstream=True)
                    logger.info("created consumer group %s on stream %s", group, stream)
            except aioredis.ResponseError:
                await client.xgroup_create(stream, group, id="0", mkstream=True)
                logger.info("created consumer group %s on stream %s (new stream)", group, stream)

    async def set_streams(self, streams: list[str], skip_backlog: bool = True) -> dict:
        """Replace the subscribed stream set.

        Streams being *added* get their consumer group created if missing and — unless
        ``skip_backlog`` is False — have the group's last-delivered-id advanced to ``$``
        so that whatever accumulated while unsubscribed is not replayed onto the panel.

        Streams already in the set are left untouched: switching
        ``["messages"] -> ["messages", "scale"]`` does not disturb ``messages``.

        The new set is picked up by the read loop on its next iteration, i.e. within
        ``BLOCK_MS``.

        Returns a summary dict with the resulting set plus the added/removed streams.
        """
        requested = normalize_streams(streams)
        if not requested:
            raise ValueError("streams must contain at least one non-empty name")

        async with self._switch_lock:
            current = set(self._streams)
            added = [s for s in requested if s not in current]
            removed = [s for s in self._streams if s not in set(requested)]

            if added:
                client = await self._connect()
                await self._ensure_group(client, added)
                if skip_backlog:
                    group = self._cfg.consumer_group
                    for stream in added:
                        await client.xgroup_setid(stream, group, "$")
                        logger.info("advanced group %s on stream %s to $", group, stream)

            self._streams = requested

        logger.info(
            "subscribed streams updated",
            extra={"streams": requested, "consumer_group": self._cfg.consumer_group},
        )
        return {
            "streams": requested,
            "added": added,
            "removed": removed,
            "skipBacklog": skip_backlog,
        }

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

    async def _startup_attempt(self) -> None:
        client = await self._connect()
        await client.ping()
        await self._ensure_group(client, self._streams)

    async def wait_ready(self, timeout: float) -> None:
        """Retry connecting and creating the consumer groups until Redis answers.

        Exponential backoff (1s, 2s, 4s, 8s, capped at 16s), each attempt bounded
        by ``STARTUP_ATTEMPT_TIMEOUT_SECONDS``, for at most ``timeout`` seconds.
        These calls used to run once, outside the read loop's error handling, so a
        Redis that was not up yet ended the consumer task while the process and
        ``/healthz`` stayed green and nothing was ever consumed (#65).

        Raises :class:`RedisNotReadyError` when the budget runs out. A cancellation
        (shutdown signal) ends the wait immediately.
        """
        deadline = time.monotonic() + timeout
        backoff = STARTUP_BACKOFF_INITIAL_SECONDS
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - time.monotonic()
            try:
                await asyncio.wait_for(
                    self._startup_attempt(),
                    timeout=max(0.001, min(STARTUP_ATTEMPT_TIMEOUT_SECONDS, remaining)),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure is retried within the budget
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RedisNotReadyError(
                        f"redis at {self._cfg.redis.addr}:{self._cfg.redis.port} not ready "
                        f"after {attempt} attempts: {exc!r}"
                    ) from exc
                sleep_for = min(backoff, remaining)
                logger.warning(
                    "redis not ready, retrying",
                    extra={"attempt": attempt, "error": repr(exc), "next_sleep": sleep_for},
                )
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, STARTUP_BACKOFF_MAX_SECONDS)
                continue
            if attempt > 1:
                logger.info("redis ready after retries", extra={"attempts": attempt})
            return

    async def run(self) -> None:
        await self.wait_ready(self._cfg.redis_startup_timeout)
        client = await self._connect()

        group = self._cfg.consumer_group
        consumer = self._cfg.consumer_name

        logger.info(
            "consumer starting",
            extra={
                "redis_addr": self._cfg.redis.addr,
                "streams": self._streams,
                "consumer_group": group,
            },
        )

        self._running = True
        self._state = "running"
        while self._running:
            try:
                # Rebuilt every iteration so a runtime switch is picked up here.
                stream_ids = {s: ">" for s in self._streams}
                entries = await client.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams=stream_ids,
                    count=10,
                    block=BLOCK_MS,
                )
                if not entries:
                    continue

                for stream_name, messages in entries:
                    for entry_id, fields in messages:
                        await self._process_entry(client, stream_name, group, entry_id, fields)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("error reading from streams")
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
        if self._state != "failed":
            self._state = "stopped"
        self._running = False
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("consumer shut down")
