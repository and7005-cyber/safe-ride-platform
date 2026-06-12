import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.dao.push_dao import PushDao

router = APIRouter(prefix="/api/push", tags=["push"])
dao = PushDao()


class SubscribePayload(BaseModel):
    endpoint: str
    p256dh: str | None = None
    auth: str | None = None
    user_agent: str | None = None


class UnsubscribePayload(BaseModel):
    endpoint: str


class FcmTokenPayload(BaseModel):
    token: str
    user_agent: str | None = None


@router.get("/config")
def push_config():
    """Public client push configuration (no secrets)."""
    settings = get_settings()
    firebase = None
    raw = settings.firebase_web_config_json.strip()
    if raw:
        try:
            firebase = json.loads(raw)
        except ValueError:
            firebase = None
    return {
        "firebase": firebase,
        "firebaseVapidKey": settings.firebase_vapid_key or None,
        "vapidPublicKey": settings.vapid_public_key or None,
    }


@router.post("/subscribe")
def subscribe(payload: SubscribePayload, user: dict = Depends(get_current_user)):
    return safe_call(
        lambda: (
            dao.subscribe(user["id"], payload.endpoint, payload.p256dh, payload.auth, payload.user_agent),
            {"ok": True},
        )[1]
    )


@router.post("/unsubscribe")
def unsubscribe(payload: UnsubscribePayload, user: dict = Depends(get_current_user)):
    return safe_call(lambda: (dao.unsubscribe(user["id"], payload.endpoint), {"ok": True})[1])


@router.post("/fcm-token")
def register_fcm_token(payload: FcmTokenPayload, user: dict = Depends(get_current_user)):
    return safe_call(
        lambda: (dao.register_fcm_token(user["id"], payload.token, payload.user_agent), {"ok": True})[1]
    )


@router.post("/fcm-token/unregister")
def unregister_fcm_token(payload: FcmTokenPayload, user: dict = Depends(get_current_user)):
    return safe_call(
        lambda: (dao.unregister_fcm_token(user["id"], payload.token), {"ok": True})[1]
    )


@router.get("/notifications")
def list_notifications(user: dict = Depends(get_current_user)):
    return safe_call(lambda: dao.list_notifications(user["id"]))


@router.get("/notifications/unread-count")
def notifications_unread_count(user: dict = Depends(get_current_user)):
    return safe_call(lambda: {"count": dao.unread_count(user["id"])})


@router.post("/notifications/mark-read")
def mark_notifications_read(user: dict = Depends(get_current_user)):
    return safe_call(lambda: (dao.mark_notifications_read(user["id"]), {"ok": True})[1])
