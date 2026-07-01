from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.dao.run_dao import RunDao
from app.services.push_service import PushService

router = APIRouter(prefix="/api/runs", tags=["runs"])
dao = RunDao()
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
    student, run = safe_call(
        lambda: dao.toggle_boarding(user["id"], payload.student_id, payload.on_bus)
    )
    if payload.on_bus:
        background_tasks.add_task(push_service.notify_student_boarded, run, payload.student_id)
    return student
