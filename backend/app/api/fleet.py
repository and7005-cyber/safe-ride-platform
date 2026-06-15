from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.core.validation import clean_phone
from app.dao.fleet_dao import FleetDao
from app.services import geo_service

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
    data = payload.model_dump()
    data["driver_phone"] = clean_phone(data.get("driver_phone"), field="driver phone")
    return safe_call(lambda: dao.create_bus(data))


@router.put("/buses/{bus_id}")
def update_bus(bus_id: str, payload: BusPayload, user: dict = Depends(admin_only)):
    data = payload.model_dump()
    data["driver_phone"] = clean_phone(data.get("driver_phone"), field="driver phone")
    return safe_call(lambda: dao.update_bus(bus_id, data))


@router.delete("/buses/{bus_id}")
def delete_bus(bus_id: str, user: dict = Depends(admin_only)):
    return safe_call(lambda: (dao.delete_bus(bus_id), {"ok": True})[1])


# Schools --------------------------------------------------------------------

@router.get("/schools")
def list_schools(user: dict = Depends(get_current_user)):
    return safe_call(dao.list_schools)


@router.post("/schools")
def create_school(payload: SchoolPayload, user: dict = Depends(admin_only)):
    data = payload.model_dump()
    data["phone"] = clean_phone(data.get("phone"), field="school phone", allow_landline=True)
    return safe_call(lambda: dao.create_school(data))


@router.put("/schools/{school_id}")
def update_school(school_id: str, payload: SchoolPayload, user: dict = Depends(admin_only)):
    data = payload.model_dump()
    data["phone"] = clean_phone(data.get("phone"), field="school phone", allow_landline=True)
    return safe_call(lambda: dao.update_school(school_id, data))


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


# Stop-level edits (#1) ------------------------------------------------------

class StopTimePayload(BaseModel):
    pickup_time: str | None = None


@router.put("/routes/{route_id}/stops/{student_id}")
def set_stop_time(
    route_id: str, student_id: str, payload: StopTimePayload, user: dict = Depends(admin_only)
):
    return safe_call(
        lambda: (dao.set_student_pickup_time(student_id, payload.pickup_time), {"ok": True})[1]
    )


@router.delete("/routes/{route_id}/stops/{student_id}")
def cancel_stop(route_id: str, student_id: str, user: dict = Depends(admin_only)):
    return safe_call(
        lambda: (dao.remove_student_from_route(route_id, student_id), {"ok": True})[1]
    )


# Geocoding & route planning (#4, #9) ----------------------------------------

class GeocodePayload(BaseModel):
    address: str


class PlanStop(BaseModel):
    label: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    pickup_time: str | None = None


class RouteOptionsPayload(BaseModel):
    stops: list[PlanStop]
    type: str | None = "morning"
    school_id: str | None = None


@router.post("/geocode")
def geocode_address(payload: GeocodePayload, user: dict = Depends(admin_only)):
    hit = geo_service.geocode(payload.address, allow_fallback=True)
    if not hit:
        return {"found": False}
    return {"found": True, **hit}


@router.post("/route-options")
def route_options(payload: RouteOptionsPayload, user: dict = Depends(admin_only)):
    """Geocode a list of addresses + pickup times and return ordered route
    options (optimised by distance, and chronological by pickup time)."""

    def run() -> dict:
        is_afternoon = payload.type == "afternoon"
        school = None
        if payload.school_id:
            row = next((s for s in dao.list_schools() if str(s["id"]) == payload.school_id), None)
            if row and row.get("lat") is not None and row.get("lng") is not None:
                school = {"lat": row["lat"], "lng": row["lng"], "label": row["name"], "is_school": True}

        located: list[dict] = []
        unresolved: list[str] = []
        for st in payload.stops:
            lat, lng = st.lat, st.lng
            label = st.label or st.address or "Stop"
            if (lat is None or lng is None) and st.address:
                hit = geo_service.geocode(st.address, allow_fallback=True)
                if hit:
                    lat, lng = hit["lat"], hit["lng"]
                    label = st.label or hit.get("label") or label
            if lat is None or lng is None:
                unresolved.append(label)
                continue
            located.append({"label": label, "lat": lat, "lng": lng, "pickup_time": st.pickup_time})

        # Optimised-by-distance option, anchored on the school.
        opt = geo_service.optimize_route(located, start=school)
        nn_ordered = opt["ordered"]
        if is_afternoon:
            distance_stops = ([school] if school else []) + nn_ordered
        else:
            distance_stops = list(reversed(nn_ordered)) + ([school] if school else [])

        # Chronological-by-pickup-time option (reversed for afternoon).
        by_time = sorted(located, key=lambda p: p.get("pickup_time") or "99:99")
        if is_afternoon:
            time_stops = ([school] if school else []) + list(reversed(by_time))
        else:
            time_stops = by_time + ([school] if school else [])

        def numbered(stops: list[dict]) -> list[dict]:
            return [{"seq": i + 1, **s} for i, s in enumerate(stops) if s]

        return {
            "provider": opt["provider"],
            "type": payload.type,
            "unresolved": unresolved,
            "options": [
                {"strategy": "Optimised (shortest path)", "stops": numbered(distance_stops)},
                {"strategy": "By pickup time", "stops": numbered(time_stops)},
            ],
        }

    return safe_call(run)
