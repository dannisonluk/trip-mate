"""WebSocket chat endpoint (§3.1 handshake auth, §3.2 rate limiting).

Uses AsyncSession directly — no threadpool hopping, so the event loop is never
blocked by database IO (this was a known debt in the sync version).
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import JWTError
from sqlalchemy import select

from app.core.security import decode_token
from app.db.session import AsyncSessionLocal
from app.models.chat import ChatMessage, ChatRoom, ChatRoomMember
from app.models.profile import Profile
from app.models.user import User
from app.services import content_filter, moderation
from app.services import notifications as notifications_service
from app.services.pii import is_phone_like
from app.ws.manager import manager

router = APIRouter()

WS_CLOSE_POLICY_VIOLATION = 1008  # used for auth failures


async def _authenticate(token: str | None) -> tuple[str, str] | None:
    """Validate the JWT from ?token= and return (user_id, profile_id)."""
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except JWTError:
        return None

    user_id = payload.get("sub")
    profile_id = payload.get("profile_id")
    if not user_id or not profile_id:
        return None

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uuid.UUID(user_id))
        if user is None or not user.is_active or user.is_deleted:
            return None
    return user_id, profile_id


async def _authorize_room(room_id: uuid.UUID, profile_id: uuid.UUID) -> bool:
    """§3.1 — caller must be a member of the room."""
    async with AsyncSessionLocal() as db:
        room = await db.get(ChatRoom, room_id)
        if room is None:
            return False
        return any(m.profile_id == profile_id for m in room.members)


async def _persist_message(room_id: uuid.UUID, profile_id: uuid.UUID, content: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        # Re-check blocks at write time (§3.2): a block must take effect immediately,
        # including for connections that were opened before the block was applied.
        member_ids = (
            await db.execute(
                select(ChatRoomMember.profile_id).where(ChatRoomMember.room_id == room_id)
            )
        ).scalars().all()
        for other in member_ids:
            if other != profile_id and await moderation.is_blocked_between(db, profile_id, other):
                return None

        message = ChatMessage(room_id=room_id, sender_id=profile_id, content=content)
        db.add(message)

        # Notify the other members in the same transaction as the message, so a
        # notification can never reference a message that was not saved.
        # `notify_new_message` itself re-checks blocks and skips the sender.
        sender = await db.get(Profile, profile_id)
        room = await db.get(ChatRoom, room_id)
        if sender is not None:
            # A TRIP room is named after its trip, which is genuine user content
            # and stays as typed. Only the *absence* of a name needs translating,
            # and that is expressed as `room_label=None` + a different code rather
            # than by inventing a Chinese placeholder string here.
            room_label = (
                room.title
                if room is not None and room.room_type == "TRIP" and room.title
                else None
            )
            for recipient_id in member_ids:
                await notifications_service.notify_new_message(
                    db,
                    recipient_profile_id=recipient_id,
                    chat_room_id=room_id,
                    actor_profile_id=profile_id,
                    actor_nickname=sender.nickname,
                    room_label=room_label,
                    content=content,
                )

        await db.commit()
        await db.refresh(message)
        return {
            "id": str(message.id),
            "room_id": str(message.room_id),
            "sender_id": str(message.sender_id),
            "content": message.content,
            "created_at": message.created_at.isoformat(),
        }


@router.websocket("/ws/chat/{room_id}")
async def chat_socket(websocket: WebSocket, room_id: str, token: str | None = None):
    try:
        room_uuid = uuid.UUID(room_id)
    except ValueError:
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return

    auth = await _authenticate(token)
    if auth is None:
        # Reject the handshake BEFORE accepting — client receives code 1008.
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return

    _user_id, profile_id_str = auth
    profile_uuid = uuid.UUID(profile_id_str)

    if not await _authorize_room(room_uuid, profile_uuid):
        await websocket.close(code=WS_CLOSE_POLICY_VIOLATION)
        return

    await manager.connect(room_id, profile_id_str, websocket)
    await manager.broadcast(
        room_id,
        {
            "type": "presence",
            "event": "join",
            "profile_id": profile_id_str,
            # Awaited: the list is the union across replicas, so it is not
            # knowable from local state. Broadcasting the join *after* the
            # registry write is what makes the joiner's own name appear in the
            # frame others receive.
            "online": await manager.online_profiles(room_id),
        },
    )

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "ping":
                await websocket.send_json(
                    {"type": "pong", "ts": datetime.now(timezone.utc).isoformat()}
                )
                continue

            if msg_type == "typing":
                await manager.broadcast(
                    room_id,
                    {
                        "type": "typing",
                        "profile_id": profile_id_str,
                        "is_typing": bool(data.get("is_typing")),
                    },
                    exclude=profile_id_str,
                )
                continue

            if msg_type != "message":
                await websocket.send_json({"type": "error", "code": "unsupported_type"})
                continue

            # §3.2 — 2 messages/second per user, counted in the shared store so
            # the quota does not multiply with the replica count.
            if not await manager.allow_message(profile_id_str):
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "rate_limited",
                        "detail": "Slow down (max 2 msg/s).",
                    }
                )
                continue

            content = (data.get("content") or "").strip()
            if not content:
                await websocket.send_json({"type": "error", "code": "empty_message"})
                continue
            if len(content) > 4000:
                await websocket.send_json({"type": "error", "code": "too_long"})
                continue

            # Moderated before the row is built. `screen` rather than `enforce`
            # because an HTTPException is meaningless on a socket: this caller
            # needs the verdict and the structured reason so it can send an
            # error frame the UI can render in the reader's own language.
            _verdict, rejection = await content_filter.screen(
                content, field="chat", label="content_field.message"
            )
            if rejection is not None:
                # `code` is the frame discriminator the client switches on;
                # `detail` carries the structured reason, mirroring the HTTP
                # error shape so both paths resolve through one code path.
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": rejection.code,
                        "detail": rejection.as_detail(),
                    }
                )
                continue

            saved = await _persist_message(room_uuid, profile_uuid, content)
            if saved is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "blocked",
                        "detail": "Message could not be delivered.",
                    }
                )
                continue

            await manager.broadcast(room_id, {"type": "message", **saved})

            # Safety nudge: warn the sender if they just shared a phone-like string.
            #
            # `code` only — the client owns the sentence. Shipping the zh-HK text
            # here meant an English-locale user received Traditional Chinese, and
            # `check:i18n` could not see it because it only scans frontend
            # sources. See `docs/AUDIT-2026-09-26.md` (B6).
            if is_phone_like(content):
                await manager.send_personal(
                    room_id,
                    profile_id_str,
                    {"type": "safety_hint", "code": "possible_phone"},
                )

    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(room_id, profile_id_str)
        await manager.broadcast(
            room_id,
            {
                "type": "presence",
                "event": "leave",
                "profile_id": profile_id_str,
                "online": await manager.online_profiles(room_id),
            },
        )
