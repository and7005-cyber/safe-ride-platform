import datetime as dt
import re

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field, field_validator

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def _validate_hhmm(v: str | None) -> str | None:
    """Reject a malformed gate anchor / bell time up front (the DB columns are
    free-text): a value without a valid HH:MM shape would otherwise reach the
    turnaround-feasibility math and 500. Empty -> None (inherit)."""
    if v is None or v.strip() == "":
        return None
    if not _HHMM_RE.match(v.strip()):
        raise ValueError("time must be HH:MM (00:00–23:59)")
    return v.strip()

from app.api._helpers import safe_call
from app.core.auth import get_current_user
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.permissions import require_director, require_staff
from app.core.rate_limit import SlidingWindowLimiter
from app.core.scope import SchoolScope
from app.core.validation import clean_phone
from app.dao.fleet_dao import FleetDao
from app.dao.push_dao import PushDao
from app.dao.school_thresholds import KNOB_BOUNDS, PER_SCHOOL_KNOBS
from app.services import geo_service
from app.services.push_service import PushService, notify_route_changes

router = APIRouter(prefix="/api/fleet", tags=["fleet"])
dao = FleetDao()
push_dao = PushDao()
push_service = PushService()

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
    # status is accepted and ignored (U9): bus status is derived at read time
    # from the bus's current run and no path writes the column. The field stays
    # on the payload so a client still sending it does not 422 mid-rollout.
    status: str | None = None
    # Office-set availability — 'in-service' | 'out-of-service'. Not derivable:
    # whether a bus is in the workshop is not a function of its runs. null means
    # "leave as-is", which is what a depot-only save from the fleet map sends.
    availability: str | None = None
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

    _v_bells = field_validator("morning_bell", "afternoon_bell")(_validate_hhmm)

    # Per-school tracking knobs (GPS plan U11: R26, R31, R38), bounded here so
    # a value outside the range is a 422 before any SQL — migration 016's
    # column CHECKs are never the first line of defence. Three-way semantics
    # the router preserves with exclude_unset: a knob OMITTED from the body
    # keeps its stored value (an older Settings page cannot wipe them), an
    # explicit null clears it to the system default, a number stores it.
    custody_threshold_m: int | None = Field(
        default=None, ge=KNOB_BOUNDS["custody_threshold_m"][0], le=KNOB_BOUNDS["custody_threshold_m"][1]
    )
    vicinity_radius_m: int | None = Field(
        default=None, ge=KNOB_BOUNDS["vicinity_radius_m"][0], le=KNOB_BOUNDS["vicinity_radius_m"][1]
    )
    fix_accuracy_cap_m: int | None = Field(
        default=None, ge=KNOB_BOUNDS["fix_accuracy_cap_m"][0], le=KNOB_BOUNDS["fix_accuracy_cap_m"][1]
    )
    position_retention_days: int | None = Field(
        default=None,
        ge=KNOB_BOUNDS["position_retention_days"][0],
        le=KNOB_BOUNDS["position_retention_days"][1],
    )
    ping_interval_s: int | None = Field(
        default=None, ge=KNOB_BOUNDS["ping_interval_s"][0], le=KNOB_BOUNDS["ping_interval_s"][1]
    )

    def tracking_fields(self) -> dict:
        """The knobs this body actually named (set explicitly, null included);
        an omitted knob is not here, so the DAO leaves it alone."""
        return self.model_dump(include=set(PER_SCHOOL_KNOBS), exclude_unset=True)

    def base_fields(self) -> dict:
        return self.model_dump(exclude=set(PER_SCHOOL_KNOBS))


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

    _v_gate_anchor = field_validator("gate_anchor")(_validate_hhmm)
    # Planner persistence (R17/R18): a saved option carries its own ordered
    # stops plus the road polyline and totals. Presence of `stops` marks the
    # route custom (custom_stops = true) and skips student-based regeneration.
    stops: list[RouteStopPayload] | None = None
    polyline: str | None = None
    total_distance_m: int | None = None
    total_duration_s: int | None = None


# Buses (U6: school-scoped) --------------------------------------------------

@router.get("/buses")
def list_buses(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: dao.list_buses(scope))


@router.post("/buses")
def create_bus(
    payload: BusPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # Any stray school_id in the payload is dropped by the model (extra keys
    # ignored); the DAO stamps scope.school_id (R28's bus-side twin).
    data = payload.model_dump()
    data["driver_phone"] = clean_phone(data.get("driver_phone"), field="driver phone")
    return safe_call(lambda: dao.create_bus(scope, data, actor=user))


@router.put("/buses/{bus_id}")
def update_bus(
    bus_id: str,
    payload: BusPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    data = payload.model_dump()
    data["driver_phone"] = clean_phone(data.get("driver_phone"), field="driver phone")
    return safe_call(lambda: dao.update_bus(scope, bus_id, data, actor=user))


@router.delete("/buses/{bus_id}")
def delete_bus(
    bus_id: str,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    return safe_call(lambda: (dao.delete_bus(scope, bus_id, actor=user), {"ok": True})[1])


# Schools (U6: the school record is the active school's settings, R4) ---------
# POST /schools and DELETE /schools/{id} are gone: creation moves to the
# provider console (U10) and deletion is out of scope this version (R23).

@router.get("/schools")
def list_schools(scope: SchoolScope = Depends(require_staff)):
    # Compatibility window (Release 4): a ONE-element list — the active
    # school — because six pickers in the shipped frontend expect a list.
    return safe_call(lambda: [dao.get_school(scope)])


@router.get("/school")
def get_school(scope: SchoolScope = Depends(require_staff)):
    """The active school's settings row (same shape as one list element),
    including the five per-school tracking knobs as stored (null = default)
    and ``tracking_defaults``, the system default for each (GPS plan U11)."""
    return safe_call(lambda: dao.get_school(scope))


@router.put("/schools/{school_id}")
def update_school(
    school_id: str,
    payload: SchoolPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    """Staff only (director or coordinator; a driver or parent answers 403
    from the scope dependency). The base fields are written as sent; the
    tracking knobs only as named (GPS plan U11 — see SchoolPayload)."""
    def run():
        if school_id != scope.school_id:
            # Another school's settings do not exist for this caller (R3).
            raise NotFoundError("School not found")
        data = payload.base_fields()
        data["phone"] = clean_phone(data.get("phone"), field="school phone", allow_landline=True)
        return dao.update_school(scope, data, actor=user, tracking=payload.tracking_fields())

    return safe_call(run)


# Routes (U7: school-scoped; delete stays director-only) ----------------------

@router.get("/routes")
def list_routes(scope: SchoolScope = Depends(require_staff)):
    return safe_call(lambda: dao.list_routes(scope))


@router.post("/routes")
def create_route(
    payload: RoutePayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # The payload's school_id is ignored — the scope stamps the school (U7);
    # a foreign bus_id answers 404 (AE25) inside the DAO.
    return safe_call(lambda: dao.create_route(scope, payload.model_dump(), actor=user))


@router.put("/routes/{route_id}")
def update_route(
    route_id: str, payload: RoutePayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # U13 (origin R15): a route edit — a bus (re)assignment above all — can
    # change what every family on the route was told. Post-commit fan-out vs
    # the communicated baselines, via BackgroundTasks (the broadcast pattern).
    # create_route needs no fan-out: a brand-new route has no members yet.
    result = safe_call(
        lambda: dao.update_route(scope, route_id, payload.model_dump(), actor=user)
    )
    if result is not None:
        background_tasks.add_task(notify_route_changes, route_ids=[route_id], scope=scope)
    return result


@router.delete("/routes/{route_id}")
def delete_route(
    route_id: str, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    # U13: capture the members BEFORE the delete — the cascade severs the
    # links the fan-out would otherwise follow. Every member's baseline for
    # this route's leg then reads as removed: route-unassigned + baseline
    # delete.
    members = safe_call(lambda: push_dao.students_of_routes([route_id], scope=scope))
    result = safe_call(
        lambda: (dao.delete_route(scope, route_id, actor=user), {"ok": True})[1]
    )
    if members:
        background_tasks.add_task(notify_route_changes, student_ids=members, scope=scope)
    return result


# Stop-level edits (#1) ------------------------------------------------------

class StopTimePayload(BaseModel):
    pickup_time: str | None = None


@router.put("/routes/{route_id}/stops/{student_id}")
def set_stop_time(
    route_id: str, student_id: str, payload: StopTimePayload,
    background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # stops_recalculated: false = an affected auto route's rebuild fell back
    # instead of recomputing geometry (U6/R10) — the sibling cancel_stop shape.
    result = safe_call(
        lambda: {
            "ok": True,
            "stops_recalculated": dao.set_student_pickup_time(
                scope, route_id, student_id, payload.pickup_time, actor=user
            ),
        }
    )
    # U13 (origin R15): a pickup-time edit regenerates EVERY route the student
    # rides — the fan-out expands from the student to those routes' members
    # and diffs each against the communicated baselines, post-commit.
    background_tasks.add_task(notify_route_changes, student_ids=[student_id], scope=scope)
    return result


@router.delete("/routes/{route_id}/stops/{student_id}")
def cancel_stop(
    route_id: str, student_id: str, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # stops_recalculated: false = the rebuild fell back instead of recomputing
    # geometry; the durable last_recalc_degraded flag rides the route payload
    # (U6/R10).
    result = safe_call(
        lambda: {
            "ok": True,
            "stops_recalculated": dao.remove_student_from_route(
                scope, route_id, student_id, actor=user
            ),
        }
    )
    # U13: the student id travels explicitly — the link was just severed, so
    # route expansion alone would miss them. Their baseline for this leg now
    # reads as removed: route-unassigned + baseline delete; the route's other
    # members are diffed for reshuffle drift.
    background_tasks.add_task(
        notify_route_changes, route_ids=[route_id], student_ids=[student_id], scope=scope
    )
    return result


# Manual ordering (U7) ---------------------------------------------------------

class StopOrderPayload(BaseModel):
    # The FULL ordered list of the route's location-group keys — the
    # `group_key` each non-gate stop row carries in the routes payload,
    # echoed back verbatim in the admin's chosen order.
    order: list[str]


@router.put("/routes/{route_id}/stop-order")
def set_stop_order(
    route_id: str, payload: StopOrderPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    """Persist the admin's manual stop order and flip the route to manual mode
    (R11). Set-equality validated server-side: missing, extra, duplicate or
    foreign keys → 400; planner-saved (custom) routes → 409. The U8
    hard-constraint guard never fires here by construction — a reorder keeps
    the same children and stop count, so it can neither introduce nor worsen
    a capacity or stop-cap violation (the caps apply to additions, which flow
    through the assignment paths' regeneration)."""
    result = safe_call(
        lambda: (
            dao.set_route_stop_order(scope, route_id, payload.order, actor=user),
            {"ok": True},
        )[1]
    )
    # U13 (origin R15): a reorder keeps each stop's own time, but the members'
    # live truth may already have drifted from what was last communicated —
    # diff every member vs their baseline post-commit.
    background_tasks.add_task(notify_route_changes, route_ids=[route_id], scope=scope)
    return result


@router.post("/routes/{route_id}/recalculate")
def recalculate_route(
    route_id: str, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    """Explicit return to automatic ordering (R11; fleet-plan U8): clears
    manual mode AND plan order — the one release action after which the
    optimiser may re-order, so the client confirms plan-ordered routes behind
    copy warning the applied plan's order is discarded — and regenerates
    immediately. stops_recalculated: false = the rebuild fell back (degraded)
    instead of computing geometry. Custom routes → 409."""
    result = safe_call(
        lambda: {
            "ok": True,
            "stops_recalculated": dao.recalculate_route(scope, route_id, actor=user),
        }
    )
    # U13 (origin R15): the recompute may move every member's time — notify
    # only the >= 5-minute movers vs their communicated baselines.
    background_tasks.add_task(notify_route_changes, route_ids=[route_id], scope=scope)
    return result


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
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    """Free-text message to every parent with a child assigned to the route
    (U8: R20, R21, R23; AE5). U7: another school's route answers 404, and the
    recipient set is accepted links on enabled accounts only.

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
        context = push_dao.route_broadcast_context(scope, route_id)
        if context is None:
            raise NotFoundError("Route not found")
        if context["student_count"] == 0:
            raise ConflictError(
                "No students are assigned to this route — there is nobody to message."
            )
        parent_ids = push_dao.parents_of_route(scope, route_id)
        if not parent_ids:
            raise ConflictError(
                "None of this route's students have a linked parent account — "
                "the message would reach nobody. Link parent emails on the "
                "students first."
            )
        # The broadcast-sent audit rides the dispatch decision (U7): every
        # guard passed and this exact recipient count is what goes out.
        dao.record_broadcast(scope, route_id, user, len(parent_ids))
        return context["route"], body, parent_ids

    route, body, parent_ids = safe_call(run)
    background_tasks.add_task(
        push_service.notify_admin_broadcast, route, body, parent_ids, scope=scope
    )
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

    _v_gate_anchor = field_validator("gate_anchor")(_validate_hhmm)


@router.post("/geocode")
def geocode_address(payload: GeocodePayload, scope: SchoolScope = Depends(require_staff)):
    hit = geo_service.geocode(payload.address, allow_fallback=True)
    if not hit:
        return {"found": False}
    return {"found": True, **hit}


@router.post("/reverse-geocode")
def reverse_geocode_point(
    payload: ReverseGeocodePayload, scope: SchoolScope = Depends(require_staff)
):
    """Resolve a picked map pin to an editable address string (R8). Best-effort:
    ``{"found": False}`` when there is no key, no result, or the lookup fails."""
    return geo_service.reverse_geocode(payload.lat, payload.lng)


@router.get("/places/suggest")
def places_suggest(q: str = "", scope: SchoolScope = Depends(require_staff)):
    """Nairobi-biased address autocomplete (Places API New, server-side)."""
    return {"suggestions": geo_service.places_autocomplete(q)}


@router.get("/places/details")
def places_details(place_id: str, scope: SchoolScope = Depends(require_staff)):
    """Resolve a Places place_id to coordinates for a selected suggestion."""
    hit = geo_service.place_details(place_id)
    if not hit:
        return {"found": False}
    return {"found": True, **hit}


@router.post("/route-options")
def route_options(
    payload: RouteOptionsPayload, scope: SchoolScope = Depends(require_staff)
):
    """Geocode addresses + pickup times and return route options enriched with
    the real road polyline, total distance/time, and traffic-aware per-stop
    ETAs (via the Google Routes API, with an offline straight-line fallback).
    U7: the preview's school is the ACTIVE school from the scope — the
    payload's school_id is accepted and ignored (the stray-school-id rule)."""

    def run() -> dict:
        is_afternoon = payload.type == "afternoon"
        default_anchor = "15:30" if is_afternoon else "07:00"

        school = None
        school_bell = None
        row = dao.get_school(scope)
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
