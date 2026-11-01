from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List

from fastapi import WebSocket

from app.config.settings import get_settings
from app.redis.client import RedisClient
from app.redis.keys import (
    websocket_connection_key,
    websocket_connection_prefix,
    websocket_user_connections_key,
)
from app.websocket.connection import WebSocketConnection


logger = logging.getLogger(__name__)

_REGISTER_CONNECTION_SCRIPT = """
local user_set = KEYS[1]
local connection_key = KEYS[2]
local connection_id = ARGV[1]
local value = ARGV[2]
local lease = tonumber(ARGV[3])
local max_connections = tonumber(ARGV[4])
local prefix = ARGV[5]
local ids = redis.call('SMEMBERS', user_set)
for _, id in ipairs(ids) do
  if not redis.call('EXISTS', prefix .. id) then redis.call('SREM', user_set, id) end
end
if redis.call('SCARD', user_set) >= max_connections then return 0 end
redis.call('SADD', user_set, connection_id)
redis.call('SET', connection_key, value, 'EX', lease)
redis.call('EXPIRE', user_set, lease)
return 1
"""

_UNREGISTER_CONNECTION_SCRIPT = """
local user_set = KEYS[1]
local connection_key = KEYS[2]
local connection_id = ARGV[1]
local prefix = ARGV[2]
local target = ARGV[3] .. '|' .. ARGV[4] .. '|'
redis.call('DEL', connection_key)
redis.call('SREM', user_set, connection_id)
local count = 0
local ids = redis.call('SMEMBERS', user_set)
for _, id in ipairs(ids) do
  local value = redis.call('GET', prefix .. id)
  if not value then
    redis.call('SREM', user_set, id)
  elseif string.sub(value, 1, string.len(target)) == target then
    count = count + 1
  end
end
if redis.call('SCARD', user_set) == 0 then redis.call('DEL', user_set) end
return count
"""

_TOUCH_CONNECTION_SCRIPT = """
if not redis.call('EXISTS', KEYS[2]) then return 0 end
redis.call('EXPIRE', KEYS[2], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return 1
"""


DisconnectCallback = Callable[[str, str], Awaitable[None]]


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: Dict[str, List[WebSocketConnection]] = {}
        self._lock = asyncio.Lock()
        self._instance_id = str(uuid.uuid4())
        self._pubsub = None
        self._pubsub_task: asyncio.Task | None = None
        self._monitor_task: asyncio.Task | None = None
        self._disconnect_callback: DisconnectCallback | None = None

    def set_disconnect_callback(self, callback: DisconnectCallback) -> None:
        self._disconnect_callback = callback

    async def start_pubsub(self) -> None:
        if self._pubsub_task is not None:
            return
        self._pubsub = RedisClient.instance().pubsub()
        await self._pubsub.subscribe("game:broadcast")
        self._pubsub_task = asyncio.create_task(self._consume_broadcasts(), name="game-websocket-broadcasts")
        self._monitor_task = asyncio.create_task(self._monitor_connections(), name="websocket-heartbeat-monitor")

    async def stop_pubsub(self) -> None:
        tasks = (self._pubsub_task, self._monitor_task)
        self._pubsub_task = None
        self._monitor_task = None
        for task in tasks:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        pubsub = self._pubsub
        self._pubsub = None
        if pubsub is not None:
            try:
                await pubsub.unsubscribe("game:broadcast")
                await pubsub.close()
            except Exception:
                logger.exception("ws.pubsub.shutdown_failed")

    async def connect(self, websocket: WebSocket, user_id: str, game_id: str, player_slot: int) -> bool:
        settings = get_settings()
        connection_id = str(uuid.uuid4())
        lease_seconds = max(settings.websocket_heartbeat_seconds * 3, 45)
        value = f"{game_id}|{user_id}|{self._instance_id}"
        redis = RedisClient.instance()
        registered = await redis.eval(
            _REGISTER_CONNECTION_SCRIPT,
            2,
            websocket_user_connections_key(user_id),
            websocket_connection_key(connection_id, user_id),
            connection_id,
            value,
            lease_seconds,
            settings.websocket_max_connections_per_user,
            websocket_connection_prefix(user_id),
        )
        if not registered:
            return False
        now = datetime.utcnow()
        conn = WebSocketConnection(
            connection_id=connection_id,
            websocket=websocket,
            user_id=user_id,
            game_id=game_id,
            player_slot=player_slot,
            connected_at=now,
            last_heartbeat=now,
        )
        async with self._lock:
            self._connections.setdefault(game_id, []).append(conn)
        logger.info(
            "ws.connected",
            extra={"game_id": game_id, "user_id": user_id, "player_slot": player_slot},
        )
        return True

    async def disconnect(self, websocket: WebSocket, game_id: str) -> bool:
        async with self._lock:
            conn = self._remove_connection(game_id, websocket)
        if conn is None:
            return False
        try:
            last_connection = await self._unregister(conn)
        except Exception:
            logger.exception("ws.presence.unregister_failed", extra={"game_id": game_id, "user_id": conn.user_id})
            return False
        logger.info(
            "ws.disconnected",
            extra={"game_id": game_id, "user_id": conn.user_id, "last_game_connection": last_connection},
        )
        if last_connection:
            await self._notify_disconnect(conn.game_id, conn.user_id)
        return last_connection

    async def has_player_connection(self, game_id: str, user_id: str) -> bool:
        redis = RedisClient.instance()
        user_set = websocket_user_connections_key(user_id)
        ids = await redis.smembers(user_set)
        stale = []
        target = f"{game_id}|{user_id}|"
        for raw_id in ids:
            conn_id = self._decode(raw_id)
            value = await redis.get(websocket_connection_key(conn_id, user_id))
            if value is None:
                stale.append(conn_id)
            elif self._decode(value).startswith(target):
                return True
        if stale:
            await redis.srem(user_set, *stale)
            if await redis.scard(user_set) == 0:
                await redis.delete(user_set)
        return False

    async def heartbeat(self, game_id: str, player_slot: int, user_id: str | None = None) -> None:
        async with self._lock:
            connections = list(self._connections.get(game_id, []))
        now = datetime.utcnow()
        settings = get_settings()
        lease = max(settings.websocket_heartbeat_seconds * 3, 45)
        for conn in connections:
            if conn.player_slot == player_slot and (user_id is None or conn.user_id == user_id):
                conn.last_heartbeat = now
                await RedisClient.instance().eval(
                    _TOUCH_CONNECTION_SCRIPT,
                    2,
                    websocket_user_connections_key(conn.user_id),
                    websocket_connection_key(conn.connection_id, conn.user_id),
                    lease,
                )

    async def broadcast(self, game_id: str, message: dict) -> None:
        await self._broadcast_local(game_id, message)
        try:
            await RedisClient.instance().publish(
                "game:broadcast",
                json.dumps({"origin": self._instance_id, "game_id": game_id, "message": message}),
            )
        except Exception:
            logger.exception("ws.broadcast.publish_failed", extra={"game_id": game_id})

    async def _broadcast_local(self, game_id: str, message: dict) -> None:
        async with self._lock:
            connections = list(self._connections.get(game_id, []))
        stale: List[WebSocketConnection] = []
        for conn in connections:
            try:
                await conn.websocket.send_json(message)
            except Exception:
                logger.exception("ws.broadcast.failed", extra={"game_id": game_id, "user_id": conn.user_id})
                stale.append(conn)
        for conn in stale:
            await self._dispose_connection(conn)

    async def _consume_broadcasts(self) -> None:
        while True:
            try:
                event = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not event or event.get("type") != "message":
                    continue
                payload = json.loads(event["data"])
                if payload.get("origin") != self._instance_id:
                    await self._broadcast_local(payload["game_id"], payload["message"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ws.pubsub.consume_failed")
                await asyncio.sleep(0.1)

    async def send_to_player(self, game_id: str, player_slot: int, message: dict) -> None:
        async with self._lock:
            connections = list(self._connections.get(game_id, []))
        stale = []
        delivered = 0
        for conn in connections:
            if conn.player_slot != player_slot:
                continue
            try:
                await conn.websocket.send_json(message)
                delivered += 1
            except Exception:
                stale.append(conn)
                logger.exception("ws.send_to_player.failed", extra={"game_id": game_id, "user_id": conn.user_id})
        for conn in stale:
            await self._dispose_connection(conn)
        logger.info(
            "ws.send_to_player",
            extra={"game_id": game_id, "player_slot": player_slot, "recipients": delivered},
        )

    async def prune_stale(self, timeout_seconds: int) -> None:
        threshold = datetime.utcnow() - timedelta(seconds=timeout_seconds)
        async with self._lock:
            stale = [
                conn
                for connections in self._connections.values()
                for conn in connections
                if conn.last_heartbeat is not None and conn.last_heartbeat <= threshold
            ]
        for conn in stale:
            try:
                await conn.websocket.close(code=4408, reason="heartbeat timeout")
            except Exception:
                pass
            await self._dispose_connection(conn)

    async def _monitor_connections(self) -> None:
        settings = get_settings()
        timeout = settings.websocket_heartbeat_seconds * 3
        while True:
            await asyncio.sleep(settings.websocket_heartbeat_seconds)
            try:
                await self.prune_stale(timeout)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ws.heartbeat.monitor_failed")

    async def _dispose_connection(self, conn: WebSocketConnection) -> None:
        try:
            await conn.websocket.close(code=1011, reason="connection is no longer writable")
        except Exception:
            pass
        async with self._lock:
            removed = self._remove_connection(conn.game_id, conn.websocket)
        if removed is None:
            return
        try:
            if await self._unregister(removed):
                await self._notify_disconnect(removed.game_id, removed.user_id)
        except Exception:
            logger.exception(
                "ws.presence.stale_unregister_failed",
                extra={"game_id": removed.game_id, "user_id": removed.user_id},
            )

    async def _unregister(self, conn: WebSocketConnection) -> bool:
        count = await RedisClient.instance().eval(
            _UNREGISTER_CONNECTION_SCRIPT,
            2,
            websocket_user_connections_key(conn.user_id),
            websocket_connection_key(conn.connection_id, conn.user_id),
            conn.connection_id,
            websocket_connection_prefix(conn.user_id),
            conn.game_id,
            conn.user_id,
        )
        return int(count) == 0

    async def _notify_disconnect(self, game_id: str, user_id: str) -> None:
        if self._disconnect_callback is not None:
            try:
                await self._disconnect_callback(game_id, user_id)
            except Exception:
                logger.exception("ws.disconnect.callback_failed", extra={"game_id": game_id, "user_id": user_id})

    def _remove_connection(self, game_id: str, websocket: WebSocket) -> WebSocketConnection | None:
        connections = self._connections.get(game_id, [])
        conn = next((item for item in connections if item.websocket is websocket), None)
        if conn is None:
            return None
        remaining = [item for item in connections if item is not conn]
        if remaining:
            self._connections[game_id] = remaining
        else:
            self._connections.pop(game_id, None)
        return conn

    @staticmethod
    def _decode(value) -> str:
        return value.decode() if isinstance(value, bytes) else str(value)


ws_manager = WebSocketManager()
