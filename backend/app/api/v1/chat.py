"""Chat REST router — room management and message history (§3.1, §3.2)."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.core.deps import CurrentProfile, DbSession
from app.core.rate_limit import WRITE_RATE, limit
from app.models.chat import ChatMessage, ChatRoom, ChatRoomMember
from app.models.enums import ApplicationStatus
from app.models.profile import Profile
from app.models.trip import TripApplication, TripPost
from app.schemas.chat import (
    MessageCreate,
    MessageOut,
    PaginatedMessages,
    RoomCreate,
    RoomMemberOut,
    RoomOut,
)
from app.schemas.trip import ProfileSummary
from app.services import content_filter, moderation
from app.services.pii import is_phone_like

router = APIRouter(prefix="/chat", tags=["chat"])


def _room_out(room: ChatRoom) -> RoomOut:
    out = RoomOut.model_validate(room)
    members: list[RoomMemberOut] = []
    for m in room.members:
        member = RoomMemberOut.model_validate(m)
        if getattr(m, "profile", None) is not None:
            member.profile = ProfileSummary.model_validate(m.profile)
        members.append(member)
    out.members = members
    return out


async def require_membership(db, room_id: uuid.UUID, profile_id: uuid.UUID) -> ChatRoom:
    """§3.1 — the caller must be an explicit member of the room."""
    room = await db.get(ChatRoom, room_id)
    if room is None or not any(m.profile_id == profile_id for m in room.members):
        # 404 for non-members so room existence is not leaked.
        raise HTTPException(status_code=404, detail="Room not found")
    return room


@router.post("/rooms", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
@limit(WRITE_RATE)
async def create_room(
    payload: RoomCreate, request: Request, profile: CurrentProfile, db: DbSession
):
    if payload.room_type.upper() == "DIRECT":
        if payload.other_profile_id is None:
            raise HTTPException(
                status_code=400, detail="other_profile_id is required for DIRECT rooms"
            )
        if payload.other_profile_id == profile.id:
            raise HTTPException(status_code=400, detail="Cannot open a room with yourself")
        if await db.get(Profile, payload.other_profile_id) is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        try:
            await moderation.ensure_not_blocked(db, profile.id, payload.other_profile_id)
        except PermissionError:
            raise HTTPException(
                status_code=403, detail="You cannot interact with this user"
            ) from None

        candidates = (
            await db.execute(
                select(ChatRoom)
                .join(ChatRoomMember, ChatRoomMember.room_id == ChatRoom.id)
                .where(ChatRoom.room_type == "DIRECT", ChatRoomMember.profile_id == profile.id)
            )
        ).scalars().unique().all()
        for room in candidates:
            if {m.profile_id for m in room.members} == {profile.id, payload.other_profile_id}:
                return _room_out(room)

        room = ChatRoom(room_type="DIRECT", title=payload.title)
        db.add(room)
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add_all(
            [
                ChatRoomMember(room_id=room.id, profile_id=profile.id, joined_at=now),
                ChatRoomMember(
                    room_id=room.id, profile_id=payload.other_profile_id, joined_at=now
                ),
            ]
        )
        await db.commit()
        await db.refresh(room)
        return _room_out(room)

    # TRIP group room
    if payload.trip_post_id is None:
        raise HTTPException(
            status_code=400, detail="trip_post_id is required for TRIP rooms"
        )

    post = await db.get(TripPost, payload.trip_post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Trip not found")
    # Only the organiser may open the group, otherwise any user could create
    # rooms attached to someone else's trip and invite themselves in.
    if post.creator_id != profile.id:
        raise HTTPException(status_code=403, detail="Only the trip organiser can open the group")

    # Reuse an existing group rather than creating a second one for the same trip.
    existing_group = (
        await db.execute(
            select(ChatRoom).where(
                ChatRoom.room_type == "TRIP", ChatRoom.trip_post_id == payload.trip_post_id
            )
        )
    ).scalars().unique().first()
    if existing_group is not None:
        return _room_out(existing_group)

    room = ChatRoom(
        room_type="TRIP",
        trip_post_id=payload.trip_post_id,
        title=payload.title or post.title,
    )
    db.add(room)
    await db.flush()

    # Seed the group with the organiser AND everyone whose application was
    # accepted. Without the accepted applicants the "group" would have exactly
    # one member and the feature would be pointless.
    now = datetime.now(timezone.utc)
    accepted_ids = (
        await db.execute(
            select(TripApplication.applicant_id).where(
                TripApplication.trip_post_id == payload.trip_post_id,
                TripApplication.status == ApplicationStatus.ACCEPTED,
            )
        )
    ).scalars().all()

    member_ids = list(dict.fromkeys([profile.id, *accepted_ids]))
    db.add_all(
        [
            ChatRoomMember(room_id=room.id, profile_id=mid, joined_at=now)
            for mid in member_ids
        ]
    )
    await db.commit()
    await db.refresh(room)
    return _room_out(room)


@router.get("/rooms", response_model=list[RoomOut])
async def list_rooms(profile: CurrentProfile, db: DbSession):
    rooms = (
        await db.execute(
            select(ChatRoom)
            .join(ChatRoomMember, ChatRoomMember.room_id == ChatRoom.id)
            .where(ChatRoomMember.profile_id == profile.id)
            .order_by(ChatRoom.created_at.desc())
        )
    ).scalars().unique().all()
    return [_room_out(r) for r in rooms]


@router.get("/rooms/{room_id}/messages", response_model=PaginatedMessages)
async def list_messages(
    room_id: uuid.UUID,
    profile: CurrentProfile,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    await require_membership(db, room_id, profile.id)
    base = select(ChatMessage).where(ChatMessage.room_id == room_id)
    total = await db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await db.execute(
            base.order_by(ChatMessage.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return PaginatedMessages(
        items=[MessageOut.model_validate(m) for m in reversed(rows)],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.post(
    "/rooms/{room_id}/messages", response_model=MessageOut, status_code=status.HTTP_201_CREATED
)
@limit(WRITE_RATE)
async def send_message(
    room_id: uuid.UUID,
    payload: MessageCreate,
    request: Request,
    profile: CurrentProfile,
    db: DbSession,
):
    room = await require_membership(db, room_id, profile.id)

    # Anti-harassment: a blocked peer must not be reachable even via REST.
    for member in room.members:
        if member.profile_id != profile.id:
            try:
                await moderation.ensure_not_blocked(db, profile.id, member.profile_id)
            except PermissionError:
                raise HTTPException(
                    status_code=403, detail="You cannot interact with this user"
                ) from None

    await content_filter.enforce(payload.content, field="chat", label="訊息")
    message = ChatMessage(room_id=room_id, sender_id=profile.id, content=payload.content)
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return MessageOut.model_validate(message)


@router.get("/safety/scan")
async def scan_message(text: str, profile: CurrentProfile):
    """Client helper: warn before a user leaks a phone number in chat (§2.2)."""
    return {"contains_phone_like": is_phone_like(text)}
