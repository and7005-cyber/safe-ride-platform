from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.dao.incident_dao import IncidentDao
from app.dao.push_dao import PushDao
from app.services.push_service import PushService

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
dao = IncidentDao()
push_dao = PushDao()
push_service = PushService()
admin_only = require_role("admin")
driver_only = require_role("driver")


class DriverIncidentPayload(BaseModel):
    type: str
    description: str | None = None


# Incident rows can carry a named child (student-stamped absence incidents),
# so the list and unread-count endpoints are admin-only: their sole frontend
# consumer is the admin Alerts page, and a parent or driver token must never
# read another family's absence by name.
@router.get("")
def list_incidents(user: dict = Depends(admin_only)):
    return safe_call(dao.list_incidents)


@router.get("/unread-count")
def unread_count(user: dict = Depends(admin_only)):
    return safe_call(lambda: {"count": dao.unacknowledged_count()})


@router.get("/today-count")
def today_count(user: dict = Depends(get_current_user)):
    return safe_call(lambda: {"count": dao.today_count()})


@router.post("/driver")
def report_incident(
    payload: DriverIncidentPayload,
    background_tasks: BackgroundTasks,
    user: dict = Depends(driver_only),
):
    def create() -> dict:
        run = push_dao.active_run_for_driver(user["id"])
        return dao.create_driver_incident(
            user["id"],
            payload.type,
            payload.description or "",
            run_id=str(run["id"]) if run else None,
            run_type=run.get("type") if run else None,
        )

    incident = safe_call(create)
    background_tasks.add_task(push_service.notify_incident, incident)
    return incident


@router.post("/{incident_id}/acknowledge")
def acknowledge(incident_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.acknowledge(incident_id, user["id"]))


@router.delete("/{incident_id}")
def delete_incident(incident_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_incident(incident_id), {"ok": True})[1])
