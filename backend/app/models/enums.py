"""Domain enums (spec-aligned). Stored as VARCHAR + CHECK so SQLite works too."""
import enum

from sqlalchemy import Enum as SAEnum


class BudgetType(str, enum.Enum):
    BUDGET = "BUDGET"
    MODERATE = "MODERATE"
    LUXURY = "LUXURY"


class TripStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ApplicationStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class Gender(str, enum.Enum):
    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"


class TargetGender(str, enum.Enum):
    """Who a trip organiser is looking for."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    ANY = "ANY"


class UserRole(str, enum.Enum):
    USER = "USER"
    ADMIN = "ADMIN"


class NotificationType(str, enum.Enum):
    """Why a notification was created.

    Kept as an enum (VARCHAR + CHECK) rather than a free string so the set of
    things that can reach a user is auditable at the schema level.
    """

    APPLICATION_RECEIVED = "APPLICATION_RECEIVED"
    APPLICATION_ACCEPTED = "APPLICATION_ACCEPTED"
    APPLICATION_REJECTED = "APPLICATION_REJECTED"
    NEW_MESSAGE = "NEW_MESSAGE"
    REVIEW_RECEIVED = "REVIEW_RECEIVED"


class AuditAction(str, enum.Enum):
    """Security-relevant actions worth a permanent, append-only record.

    Scoped to exactly the four categories the Security Spec names — blocking,
    reporting, erasure, and administrator action. Deliberately *not* a generic
    "log everything" event table: an audit trail that records ordinary content
    edits is both a privacy liability (it accumulates PII) and useless, because
    nobody can find the four events that matter inside it.
    """

    # Blocking (§3.2 anti-harassment)
    USER_BLOCKED = "USER_BLOCKED"
    USER_UNBLOCKED = "USER_UNBLOCKED"
    # Reporting (§3.2 abuse handling)
    REPORT_SUBMITTED = "REPORT_SUBMITTED"
    REPORT_STATUS_CHANGED = "REPORT_STATUS_CHANGED"
    # Right to erasure (§2.1 PDPO)
    ACCOUNT_DELETED = "ACCOUNT_DELETED"
    ACCOUNT_ANONYMIZED = "ACCOUNT_ANONYMIZED"
    # Administrator access to moderation surfaces
    ADMIN_QUEUE_VIEWED = "ADMIN_QUEUE_VIEWED"


def enum_col(enum_cls: type[enum.Enum], name: str) -> SAEnum:
    """Portable Enum column: VARCHAR + CHECK constraint (no native PG enum).

    `length` is derived from the longest member rather than hardcoded. A fixed
    length is a latent failure that only fires when someone adds a member:
    SQLAlchemy raises `ValueError: length must be larger or equal than the length
    of the longest enum value` at *mapper configuration* time, which surfaces as
    the app failing to import — an obscure way to learn that a string is one
    character too long.

    The floor of 20 keeps every pre-existing column at exactly the width it was
    created with, so widening the helper does not silently alter the schema of
    the enums that already fit.
    """
    longest = max(len(member.value) for member in enum_cls)
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=max(20, longest),
        values_callable=lambda cls: [e.value for e in cls],
        validate_strings=True,
    )
