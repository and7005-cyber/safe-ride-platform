from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user
from app.core.permissions import require_director, require_driver_scope, require_staff
from app.core.scope import SchoolScope
from app.dao.incident_dao import IncidentDao
from app.dao.push_dao import PushDao
from app.services.push_service import PushService

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
dao = IncidentDao()
push_dao = PushDao()
push_service = PushService()


class DriverIncidentPayload(BaseModel):
    type: str
    description: str | None = None


# Incident rows can carry a named child (student-stamped absence incidents),
# so the list and unread-count endpoints are staff-only and scoped to the
# active school (U7): their sole frontend consumer is the admin Alerts page,
# and a parent, driver or foreign-school token must never read another
# family's absence by name.
@router.get("")
def list_incidents(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: dao.list_incidents(scope))


@router.get("/unread-count")
def unread_count(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: {"count": dao.unacknowledged_count(scope)})


@router.get("/today-count")
def today_count(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: {"count": dao.today_count(scope)})


@router.post("/driver")
def report_incident(
    payload: DriverIncidentPayload,
    background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
):
    def create() -> dict:
        run = push_dao.active_run_for_driver(scope)
        return dao.create_driver_incident(
            scope,
            payload.type,
            payload.description or "",
            run_id=str(run["id"]) if run else None,
            run_type=run.get("type") if run else None,
        )

    incident = safe_call(create)
    background_tasks.add_task(push_service.notify_incident, incident, scope=scope)
    return incident


@router.post("/{incident_id}/acknowledge")
def acknowledge(
    incident_id: str,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    return safe_call(lambda: dao.acknowledge(scope, incident_id, user))


@router.delete("/{incident_id}")
def delete_incident(
    incident_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    # Director-only (R8): alerts are history and deleting one is destruction.
    return safe_call(
        lambda: (dao.delete_incident(scope, incident_id, actor=user), {"ok": True})[1]
    )
