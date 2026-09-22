"""Trip Mate ORM models — import all so `Base.metadata` is fully populated."""
from app.models.audit import AuditLog
from app.models.chat import ChatMessage, ChatRoom, ChatRoomMember
from app.models.enums import (
    ApplicationStatus,
    AuditAction,
    BudgetType,
    Gender,
    NotificationType,
    TargetGender,
    TripStatus,
    UserRole,
)
from app.models.moderation import Block, Report
from app.models.notification import Notification
from app.models.profile import Profile, TravelHistory
from app.models.review import Review
from app.models.trip import TripApplication, TripPost, TripPostTag
from app.models.user import User

__all__ = [
    "User",
    "Profile",
    "TravelHistory",
    "TripPost",
    "TripApplication",
    "TripPostTag",
    "ChatRoom",
    "ChatRoomMember",
    "ChatMessage",
    "Review",
    "Block",
    "Report",
    "Notification",
    "AuditLog",
    "BudgetType",
    "TripStatus",
    "ApplicationStatus",
    "Gender",
    "TargetGender",
    "UserRole",
    "NotificationType",
    "AuditAction",
]
