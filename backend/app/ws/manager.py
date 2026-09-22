"""WebSocket connection manager + per-user message rate limiting (§3.2)."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket


@dataclass
class TokenBucket:
    """Simple token bucket: capacity tokens, refilled at `rate` tokens/second."""

    rate: float = 2.0
    capacity: float = 2.0
    tokens: float = field(default_factory=lambda: 2.0)
    updated: float = field(default_factory=time.monotonic)

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class ConnectionManager:
    """Tracks live sockets per room and enforces send-rate limits."""

    def __init__(self, *, msg_rate_per_second: float = 2.0) -> None:
        # room_id -> {profile_id -> WebSocket}
        self._rooms: dict[str, dict[str, WebSocket]] = defaultdict(dict)
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._msg_rate = msg_rate_per_second

    async def connect(self, room_id: str, profile_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._rooms[room_id][profile_id] = websocket
            self._buckets.setdefault(profile_id, TokenBucket(rate=self._msg_rate, capacity=self._msg_rate))

    async def disconnect(self, room_id: str, profile_id: str) -> None:
        async with self._lock:
            room = self._rooms.get(room_id)
            if room:
                room.pop(profile_id, None)
                if not room:
                    self._rooms.pop(room_id, None)

    def allow_message(self, profile_id: str) -> bool:
        bucket = self._buckets.setdefault(
            profile_id, TokenBucket(rate=self._msg_rate, capacity=self._msg_rate)
        )
        return bucket.allow()

    def is_member_online(self, room_id: str, profile_id: str) -> bool:
        return profile_id in self._rooms.get(room_id, {})

    def online_profiles(self, room_id: str) -> list[str]:
        return list(self._rooms.get(room_id, {}).keys())

    async def broadcast(self, room_id: str, payload: dict[str, Any], *, exclude: str | None = None) -> None:
        room = dict(self._rooms.get(room_id, {}))
        dead: list[str] = []
        for profile_id, ws in room.items():
            if exclude and profile_id == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 — socket gone
                dead.append(profile_id)
        for profile_id in dead:
            await self.disconnect(room_id, profile_id)

    async def send_personal(self, room_id: str, profile_id: str, payload: dict[str, Any]) -> None:
        ws = self._rooms.get(room_id, {}).get(profile_id)
        if ws is None:
            return
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001
            await self.disconnect(room_id, profile_id)

    def room_id_of(self, websocket: WebSocket) -> str | None:
        for room_id, members in self._rooms.items():
            for ws in members.values():
                if ws is websocket:
                    return room_id
        return None


manager = ConnectionManager()
