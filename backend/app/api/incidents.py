from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.dao.incident_dao import IncidentDao

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
dao = IncidentDao()
admin_only = require_role("admin")
driver_only = require_role("driver")


class DriverIncidentPayload(BaseModel):
    type: str
    description: str | None = None


@router.get("")
def list_incidents(user: dict = Depends(get_current_user)):
    return safe_call(dao.list_incidents)


@router.get("/unread-count")
def unread_count(user: dict = Depends(get_current_user)):
    return safe_call(lambda: {"count": dao.unacknowledged_count()})


@router.get("/today-count")
def today_count(user: dict = Depends(get_current_user)):
    return safe_call(lambda: {"count": dao.today_count()})


@router.post("/driver")
def report_incident(payload: DriverIncidentPayload, user: dict = Depends(driver_only)):
    return safe_call(
        lambda: dao.create_driver_incident(user["id"], payload.type, payload.description or "")
    )


@router.post("/{incident_id}/acknowledge")
def acknowledge(incident_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.acknowledge(incident_id, user["id"]))


@router.delete("/{incident_id}")
def delete_incident(incident_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_incident(incident_id), {"ok": True})[1])
