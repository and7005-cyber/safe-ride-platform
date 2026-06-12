from fastapi import APIRouter, Depends, Query

from app.api._helpers import safe_call
from app.core.auth import require_role
from app.core.errors import NotFoundError
from app.dao.parent_live_dao import ParentLiveDao

router = APIRouter(prefix="/api/parent-portal", tags=["parent-portal"])
dao = ParentLiveDao()
parent_only = require_role("parent")


@router.get("/children")
def children(user: dict = Depends(parent_only)):
    return safe_call(lambda: dao.list_children(user["id"]))


@router.get("/track")
def track(student_id: str = Query(...), user: dict = Depends(parent_only)):
    def run():
        result = dao.get_track(user["id"], student_id)
        if result is None:
            raise NotFoundError("Child not found for this parent")
        return result

    return safe_call(run)


@router.get("/alerts")
def alerts(user: dict = Depends(parent_only)):
    return safe_call(lambda: dao.list_alerts(user["id"]))


@router.get("/profile")
def profile(user: dict = Depends(parent_only)):
    return safe_call(lambda: dao.get_profile(user["id"]))
