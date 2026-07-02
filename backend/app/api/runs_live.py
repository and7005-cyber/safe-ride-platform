import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.dao.incident_dao import IncidentDao
from app.dao.run_dao import RunDao
from app.services.push_service import PushService

logger = logging.getLogger("saferide.runs")

router = APIRouter(prefix="/api/runs", tags=["runs"])
dao = RunDao()
incident_dao = IncidentDao()
push_service = PushService()
admin_only = require_role("admin")
driver_only = require_role("driver")


class RunPayload(BaseModel):
    bus_id: str | None = None
    route_id: str | None = None
    school_id: str | None = None
    type: str | None = "morning"
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    status: str | None = "in-progress"
    total_stops: int | None = 0
    stops_completed: int | None = 0
    total_students: int | None = 0
    students_boarded: int | None = 0
    incidents: int | None = 0


class StartRunPayload(BaseModel):
    route_id: str


class RunIdPayload(BaseModel):
    run_id: str


class PositionPayload(BaseModel):
    lat: float
    lng: float


class BoardingPayload(BaseModel):
    student_id: str
    on_bus: bool


class StudentIdPayload(BaseModel):
    student_id: str


# Admin run CRUD -------------------------------------------------------------

@router.get("")
def list_runs(active: bool = False, user: dict = Depends(get_current_user)):
    """?active=true narrows to today's (Africa/Nairobi) non-completed runs —
    the dashboard's Active Runs card (R5)."""
    return safe_call(lambda: dao.list_runs(active=active))


@router.post("")
def create_run(payload: RunPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.create_run(payload.model_dump()))


@router.put("/{run_id}")
def update_run(run_id: str, payload: RunPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.update_run(run_id, payload.model_dump()))


@router.delete("/{run_id}")
def delete_run(run_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_run(run_id), {"ok": True})[1])


@router.get("/{run_id}/report")
def run_report(run_id: str, user: dict = Depends(admin_only)):
    """Post-run report (R14-R16): the run row + bus/route/driver names + the
    absent_students snapshot (approximate=true on the legacy fallback)."""
    return safe_call(lambda: dao.run_report(run_id))


# Driver run lifecycle -------------------------------------------------------

@router.get("/driver/context")
def driver_context(user: dict = Depends(driver_only)):
    return safe_call(lambda: dao.get_driver_context(user["id"]))


@router.post("/driver/start")
def start_run(
    payload: StartRunPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    run = safe_call(lambda: dao.start_run(user["id"], payload.route_id))
    background_tasks.add_task(push_service.notify_run_started, run)
    return run


@router.post("/driver/arrive")
def arrive(
    payload: RunIdPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    result = safe_call(lambda: dao.arrive_next_stop(user["id"], payload.run_id))
    if result.get("arrival_incident"):
        background_tasks.add_task(push_service.notify_reached_school, result["run"])
    # Arriving a stop means the next stop's children should get ready.
    background_tasks.add_task(push_service.notify_bus_approaching, result["run"])
    return result


@router.post("/driver/end")
def end_run(
    payload: RunIdPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    run = safe_call(lambda: dao.end_run(user["id"], payload.run_id))
    background_tasks.add_task(push_service.notify_run_ended, run)
    return run


@router.post("/driver/position")
def write_position(
    payload: PositionPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    # The run snapshot is captured at request time so the notification task
    # never races a subsequent arrive/end request re-reading run state.
    run = safe_call(lambda: dao.write_position(user["id"], payload.lat, payload.lng))
    background_tasks.add_task(push_service.notify_bus_position, run, payload.lat, payload.lng)
    return {"ok": True}


@router.post("/driver/boarding")
def toggle_boarding(
    payload: BoardingPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    # Morning-only and one-way: the DAO 409s afternoon runs (use /driver/
    # dropoff) and on_bus=false (un-boarding retracts a sent safety push).
    student, run = safe_call(
        lambda: dao.toggle_boarding(user["id"], payload.student_id, payload.on_bus)
    )
    background_tasks.add_task(push_service.notify_student_boarded, run, payload.student_id)
    return student


@router.post("/driver/dropoff")
def dropoff_student(
    payload: StudentIdPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    """Confirm a drop-off at a reached stop on the driver's active afternoon
    run (R32). The tap-time notification carries run_id + student_id so the
    dedup index suppresses retries."""
    student, run = safe_call(lambda: dao.dropoff_student(user["id"], payload.student_id))
    background_tasks.add_task(push_service.notify_student_dropped_off, student, run)
    return student


def _record_absent_incident(driver_id: str, student: dict, run: dict) -> None:
    """School-side channel for a driver-marked absence: a student-stamped
    incident on the admin Alerts page. Never a parent fan-out — the incident
    names the child, and ParentLiveDao.list_alerts already excludes
    student-stamped rows, so notify_incident must not be called here."""
    try:
        incident_dao.create_driver_incident(
            driver_id,
            "student",
            f"{student['name']} was marked absent by the driver at pickup on "
            f"{run.get('route_name') or 'their route'} ({run.get('bus_name') or 'bus'}).",
            run_id=str(run["id"]),
            run_type=run.get("type"),
            student_id=str(student["id"]),
        )
    except Exception:
        logger.exception("recording driver-absent incident failed")


@router.post("/driver/absent")
def mark_student_absent(
    payload: StudentIdPayload, background_tasks: BackgroundTasks, user: dict = Depends(driver_only)
):
    """Driver marks a roster student absent at the stop (R30). The DAO writes
    the absence row, the run_absences snapshot, the 'absent' status and the
    boarded recount in one transaction; the parent push and the admin-only
    incident fire post-commit."""
    student, run = safe_call(lambda: dao.mark_student_absent(user["id"], payload.student_id))
    background_tasks.add_task(push_service.notify_student_absent, student, run)
    # The parent push dedups on the notifications unique index; the incident
    # has no such index, so only a NEWLY recorded absence raises one.
    if run.get("newly_recorded"):
        background_tasks.add_task(_record_absent_incident, user["id"], student, run)
    return student
