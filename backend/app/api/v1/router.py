"""Aggregate v1 router."""
from fastapi import APIRouter

from app.api.v1 import (
    auth,
    chat,
    moderation,
    notifications,
    profiles,
    reviews,
    trips,
    uploads,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(profiles.router)
api_router.include_router(trips.router)
api_router.include_router(chat.router)
api_router.include_router(reviews.router)
api_router.include_router(notifications.router)
api_router.include_router(moderation.router)
api_router.include_router(moderation.admin_router)
api_router.include_router(uploads.router)
