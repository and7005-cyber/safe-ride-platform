from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.dao.fleet_dao import FleetDao

router = APIRouter(prefix="/api/fleet", tags=["fleet"])
dao = FleetDao()
admin_only = require_role("admin")


class BusPayload(BaseModel):
    name: str
    plate_number: str | None = None
    driver_id: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    capacity: int | None = 45
    status: str | None = "idle"


class SchoolPayload(BaseModel):
    name: str
    address: str | None = None
    phone: str | None = None
    lat: float | None = None
    lng: float | None = None


class RoutePayload(BaseModel):
    name: str
    type: str | None = "morning"
    bus_id: str | None = None
    school_id: str | None = None


# Buses ----------------------------------------------------------------------

@router.get("/buses")
def list_buses(user: dict = Depends(get_current_user)):
    return safe_call(dao.list_buses)


@router.post("/buses")
def create_bus(payload: BusPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.create_bus(payload.model_dump()))


@router.put("/buses/{bus_id}")
def update_bus(bus_id: str, payload: BusPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.update_bus(bus_id, payload.model_dump()))


@router.delete("/buses/{bus_id}")
def delete_bus(bus_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_bus(bus_id), {"ok": True})[1])


# Schools --------------------------------------------------------------------

@router.get("/schools")
def list_schools(user: dict = Depends(get_current_user)):
    return safe_call(dao.list_schools)


@router.post("/schools")
def create_school(payload: SchoolPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.create_school(payload.model_dump()))


@router.put("/schools/{school_id}")
def update_school(school_id: str, payload: SchoolPayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.update_school(school_id, payload.model_dump()))


@router.delete("/schools/{school_id}")
def delete_school(school_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_school(school_id), {"ok": True})[1])


# Routes ---------------------------------------------------------------------

@router.get("/routes")
def list_routes(user: dict = Depends(get_current_user)):
    return safe_call(dao.list_routes)


@router.post("/routes")
def create_route(payload: RoutePayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.create_route(payload.model_dump()))


@router.put("/routes/{route_id}")
def update_route(route_id: str, payload: RoutePayload, user: dict = Depends(admin_only)):
    return safe_call(lambda: dao.update_route(route_id, payload.model_dump()))


@router.delete("/routes/{route_id}")
def delete_route(route_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_route(route_id), {"ok": True})[1])
