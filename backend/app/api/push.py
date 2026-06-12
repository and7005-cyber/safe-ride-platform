from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user
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
