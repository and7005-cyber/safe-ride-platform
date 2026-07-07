import datetime as dt

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


class RouteStopPayload(BaseModel):
    label: str
    lat: float | None = None
    lng: float | None = None
    pickup_time: str | None = None
    is_school: bool = False


class RoutePayload(BaseModel):
    name: str
    type: str | None = "morning"
    bus_id: str | None = None
    school_id: str | None = None
    # Planner persistence (R17/R18): a saved option carries its own ordered
    # stops plus the road polyline and totals. Presence of `stops` marks the
    # route custom (custom_stops = true) and skips student-based regeneration.
    stops: list[RouteStopPayload] | None = None
    polyline: str | None = None
    total_distance_m: int | None = None
    total_duration_s: int | None = None


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
    # stops_recalculated: false = the rebuild fell back instead of recomputing
    # geometry; the durable last_recalc_degraded flag rides the route payload
    # (U6/R10).
    return safe_call(
        lambda: {
            "ok": True,
            "stops_recalculated": dao.remove_student_from_route(route_id, student_id),
        }
    )


# Manual ordering (U7) ---------------------------------------------------------

class StopOrderPayload(BaseModel):
    # The FULL ordered list of the route's location-group keys — the
    # `group_key` each non-gate stop row carries in the routes payload,
    # echoed back verbatim in the admin's chosen order.
    order: list[str]


@router.put("/routes/{route_id}/stop-order")
def set_stop_order(route_id: str, payload: StopOrderPayload, user: dict = Depends(admin_only)):
    """Persist the admin's manual stop order and flip the route to manual mode
    (R11). Set-equality validated server-side: missing, extra, duplicate or
    foreign keys → 400; planner-saved (custom) routes → 409."""
    return safe_call(
        lambda: (dao.set_route_stop_order(route_id, payload.order), {"ok": True})[1]
    )


@router.post("/routes/{route_id}/recalculate")
def recalculate_route(route_id: str, user: dict = Depends(admin_only)):
    """Explicit return to automatic ordering (R11): clears manual mode and
    regenerates immediately. stops_recalculated: false = the rebuild fell back
    (degraded) instead of computing geometry. Custom routes → 409."""
    return safe_call(
        lambda: {"ok": True, "stops_recalculated": dao.recalculate_route(route_id)}
    )


# Geocoding & route planning (#4, #9) ----------------------------------------

class GeocodePayload(BaseModel):
    address: str


class ReverseGeocodePayload(BaseModel):
    lat: float
    lng: float


class PlanStop(BaseModel):
    label: str | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    pickup_time: str | None = None
    is_school: bool = False


class RouteOptionsPayload(BaseModel):
    stops: list[PlanStop]
    type: str | None = "morning"
    school_id: str | None = None
    # When true the stops are used in the exact order given (drag-to-reorder):
    # no re-optimisation, just road geometry + ETAs for that sequence.
    preserve_order: bool = False


@router.post("/geocode")
def geocode_address(payload: GeocodePayload, user: dict = Depends(admin_only)):
    hit = geo_service.geocode(payload.address, allow_fallback=True)
    if not hit:
        return {"found": False}
    return {"found": True, **hit}


@router.post("/reverse-geocode")
def reverse_geocode_point(payload: ReverseGeocodePayload, user: dict = Depends(admin_only)):
    """Resolve a picked map pin to an editable address string (R8). Best-effort:
    ``{"found": False}`` when there is no key, no result, or the lookup fails."""
    return geo_service.reverse_geocode(payload.lat, payload.lng)


@router.get("/places/suggest")
def places_suggest(q: str = "", user: dict = Depends(admin_only)):
    """Nairobi-biased address autocomplete (Places API New, server-side)."""
    return {"suggestions": geo_service.places_autocomplete(q)}


@router.get("/places/details")
def places_details(place_id: str, user: dict = Depends(admin_only)):
    """Resolve a Places place_id to coordinates for a selected suggestion."""
    hit = geo_service.place_details(place_id)
    if not hit:
        return {"found": False}
    return {"found": True, **hit}


@router.post("/route-options")
def route_options(payload: RouteOptionsPayload, user: dict = Depends(admin_only)):
    """Geocode addresses + pickup times and return route options enriched with
    the real road polyline, total distance/time, and traffic-aware per-stop
    ETAs (via the Google Routes API, with an offline straight-line fallback)."""

    def run() -> dict:
        is_afternoon = payload.type == "afternoon"
        default_anchor = "15:30" if is_afternoon else "07:00"

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
            located.append(
                {"label": label, "lat": lat, "lng": lng, "pickup_time": st.pickup_time, "is_school": bool(st.is_school)}
            )

        def build_option(strategy: str, sequence: list[dict]) -> dict:
            seq = [s for s in sequence if s]
            anchor = next((s.get("pickup_time") for s in seq if s.get("pickup_time")), None)
            departure = geo_service.next_departure(anchor, default=default_anchor)
            geom = geo_service.route_geometry(seq, departure=departure)
            legs = geom["legs"]
            stops_out: list[dict] = []
            cumulative = 0
            for i, s in enumerate(seq):
                if i > 0 and i - 1 < len(legs):
                    leg = legs[i - 1]
                    cumulative += leg.get("duration_s") or 0
                    leg_distance = leg.get("distance_m")
                    leg_duration = leg.get("duration_s")
                else:
                    leg_distance = leg_duration = None
                eta = (departure + dt.timedelta(seconds=cumulative)).strftime("%H:%M")
                stops_out.append(
                    {
                        "seq": i + 1,
                        "label": s["label"],
                        "lat": s["lat"],
                        "lng": s["lng"],
                        "pickup_time": s.get("pickup_time"),
                        "is_school": bool(s.get("is_school")),
                        "eta": eta,
                        "leg_distance_m": leg_distance,
                        "leg_duration_s": leg_duration,
                    }
                )
            return {
                "strategy": strategy,
                "polyline": geom["polyline"],
                "provider": geom["provider"],
                "total_distance_m": geom["total_distance_m"],
                "total_duration_s": geom["total_duration_s"],
                "stops": stops_out,
            }

        # Drag-to-reorder: caller already fixed the order (school included inline).
        if payload.preserve_order:
            option = build_option("Custom order", located)
            return {
                "provider": option["provider"],
                "type": payload.type,
                "unresolved": unresolved,
                "options": [option],
            }

        students = [s for s in located if not s["is_school"]]

        # Option A — efficient road order (Routes API waypoint optimiser).
        ordered = geo_service.optimized_order(students, school)
        if is_afternoon:
            seq_a = ([school] if school else []) + ordered
        else:
            seq_a = ordered + ([school] if school else [])

        # Option B — chronological by pickup time (reversed for afternoon).
        by_time = sorted(students, key=lambda p: p.get("pickup_time") or "99:99")
        if is_afternoon:
            seq_b = ([school] if school else []) + list(reversed(by_time))
        else:
            seq_b = by_time + ([school] if school else [])

        option_a = build_option("Optimised (traffic-aware)", seq_a)
        option_b = build_option("By pickup time", seq_b)

        return {
            "provider": option_a["provider"],
            "type": payload.type,
            "unresolved": unresolved,
            "options": [option_a, option_b],
        }

    return safe_call(run)
