import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.rate_limit import SlidingWindowLimiter
from app.core.validation import clean_phone
from app.dao.fleet_dao import FleetDao
from app.dao.push_dao import PushDao
from app.services import geo_service
from app.services.push_service import PushService

router = APIRouter(prefix="/api/fleet", tags=["fleet"])
dao = FleetDao()
push_dao = PushDao()
push_service = PushService()
admin_only = require_role("admin")

# Broadcast blast protection (U8): per-admin, in-process best-effort (per
# Lambda container, auth.py pattern) — enough to stop a stuck retry loop from
# spamming every parent on a route twelve times over.
broadcast_limiter = SlidingWindowLimiter(max_attempts=12, window_seconds=3600)
_BROADCAST_LIMIT_MESSAGE = "Too many broadcasts this hour. Please try again later."


class BusPayload(BaseModel):
    name: str
    plate_number: str | None = None
    driver_id: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    capacity: int | None = 45
    status: str | None = "idle"
    # Overnight depot (U7/R12-R14): the bus starts its FIRST morning trip here
    # and ends its LAST afternoon trip here. Enters geometry as a boundary leg,
    # never a stop row. Set via the same PlacePicker.
    depot_lat: float | None = None
    depot_lng: float | None = None
    depot_address: str | None = None
    depot_provenance: str | None = None


class SchoolPayload(BaseModel):
    name: str
    address: str | None = None
    phone: str | None = None
    lat: float | None = None
    lng: float | None = None
    # Bell times (Africa/Nairobi HH:MM, U4): the school-level default gate
    # anchor — morning arrival, afternoon departure. A route's gate_anchor
    # overrides these; null here inherits the system default.
    morning_bell: str | None = None
    afternoon_bell: str | None = None


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
    # Route-level gate anchor override (HH:MM, U4): null inherits the school
    # bell for the direction, else the system default. The schedule is solved
    # backwards from this gate time.
    gate_anchor: str | None = None
    # Ordinal of this trip within the bus's period (U6/R19): 1 = first wave.
    # A bus may run several trips per period, each a distinct (bus, type,
    # trip_index). Defaults to 1 (single-trip, the previous behavior).
    trip_index: int | None = 1
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
    # stops_recalculated: false = an affected auto route's rebuild fell back
    # instead of recomputing geometry (U6/R10) — the sibling cancel_stop shape.
    return safe_call(
        lambda: {
            "ok": True,
            "stops_recalculated": dao.set_student_pickup_time(student_id, payload.pickup_time),
        }
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


# Route broadcast (U8) ---------------------------------------------------------

BROADCAST_BODY_MAX_CHARS = 500


class BroadcastPayload(BaseModel):
    body: str


def _clean_broadcast_body(raw: str) -> str:
    """Server-side body validation (R23): strip C0 control characters except
    newline, trim, reject empty/whitespace-only, cap at 500 characters.

    The cleaned text is stored RAW by design — no HTML/markdown stripping.
    The sole rendering surface is the parent feed's text-only React path,
    which is what keeps admin free text inert; any future consumer of
    live_notifications.body must re-verify that invariant before rendering
    it any other way."""
    body = "".join(ch for ch in raw if ch == "\n" or ord(ch) >= 32).strip()
    if not body:
        raise BadRequestError("Message body must not be empty")
    if len(body) > BROADCAST_BODY_MAX_CHARS:
        raise BadRequestError(
            f"Message body must be {BROADCAST_BODY_MAX_CHARS} characters or fewer"
        )
    return body


@router.post("/routes/{route_id}/broadcast")
def broadcast_to_route(
    route_id: str,
    payload: BroadcastPayload,
    background_tasks: BackgroundTasks,
    user: dict = Depends(admin_only),
):
    """Free-text message to every parent with a child assigned to the route
    (U8: R20, R21, R23; AE5).

    Recipients come from assignments (live_student_routes →
    live_parent_students), never students.bus_id, distinct per parent
    (siblings on the route = one copy). The recipient set is resolved HERE,
    synchronously — the fan-out runs in BackgroundTasks, so the response's
    `recipients` count must be computed before dispatch to be the truth.
    Zero recipients is never a silent 200: no assigned students → 409, and
    students assigned but zero linked parent accounts → a DISTINCT 409."""
    broadcast_limiter.check(str(user["id"]), _BROADCAST_LIMIT_MESSAGE)

    def run() -> tuple[dict, str, list[str]]:
        body = _clean_broadcast_body(payload.body)
        context = push_dao.route_broadcast_context(route_id)
        if context is None:
            raise NotFoundError("Route not found")
        if context["student_count"] == 0:
            raise ConflictError(
                "No students are assigned to this route — there is nobody to message."
            )
        parent_ids = push_dao.parents_of_route(route_id)
        if not parent_ids:
            raise ConflictError(
                "None of this route's students have a linked parent account — "
                "the message would reach nobody. Link parent emails on the "
                "students first."
            )
        return context["route"], body, parent_ids

    route, body, parent_ids = safe_call(run)
    background_tasks.add_task(push_service.notify_admin_broadcast, route, body, parent_ids)
    return {"ok": True, "recipients": len(parent_ids)}


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
    # Gate anchor override (HH:MM, U4): the preview solves the schedule backwards
    # from this gate time exactly as the saved route does — one authority. Null
    # inherits the school bell for the direction, else the system default.
    gate_anchor: str | None = None


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
        school_bell = None
        if payload.school_id:
            row = next((s for s in dao.list_schools() if str(s["id"]) == payload.school_id), None)
            if row:
                school_bell = row.get("afternoon_bell") if is_afternoon else row.get("morning_bell")
                if row.get("lat") is not None and row.get("lng") is not None:
                    school = {"lat": row["lat"], "lng": row["lng"], "label": row["name"], "is_school": True}
        # One authority (U4): route override -> school bell -> system default.
        # The preview solves against this gate time exactly as the saved route.
        anchor_hhmm = payload.gate_anchor or school_bell or default_anchor

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
            # Backward-solve from the gate anchor, same authority as the saved
            # route (U4): morning solves the departure so the last-stop (gate)
            # ETA hits the anchor; afternoon anchors the leading gate departure
            # directly. Never anchors on a student pickup time (the retired model).
            if is_afternoon:
                departure = geo_service.next_departure(anchor_hhmm, default=default_anchor)
                geom = geo_service.route_geometry(seq, departure=departure)
            else:
                anchor_dt = geo_service.next_departure(anchor_hhmm, default=default_anchor)
                departure, geom, _converged = geo_service.solve_morning_departure(seq, anchor_dt)
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
