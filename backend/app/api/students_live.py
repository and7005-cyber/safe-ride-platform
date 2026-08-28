from fastapi import APIRouter, BackgroundTasks, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user, require_role
from app.core.db import get_connection
from app.core.errors import BadRequestError, NotFoundError
from app.core.validation import clean_email, clean_phone
from app.dao.absence_dao import AbsenceDao
from app.dao.audit_dao import record_audit
from app.dao.push_dao import PushDao
from app.dao.student_live_dao import StudentLiveDao
from app.services import geo_service
from app.services.push_service import notify_route_changes
from app.services.slot_in_service import propose_slot_ins

router = APIRouter(prefix="/api/students", tags=["students"])
dao = StudentLiveDao()
absence_dao = AbsenceDao()
push_dao = PushDao()
admin_only = require_role("admin")

# U13 (origin R15) roster wiring: every path below that regenerates routes
# dispatches notify_route_changes post-commit with seed_only=True — missing
# communicated baselines are seeded SILENTLY, existing ones untouched, no
# feed rows. Deliberate deviation from the edit paths' full fan-out: the
# roster interaction itself is the communication for a child the admin is
# editing by hand, and the shipped apply-suite contract pins roster mutations
# silent — drift they cause is notified by the NEXT edit or apply, measured
# against the baselines seeded here.


def _clean_student(data: dict, *, geocode_fallback: bool = True) -> dict:
    """Enforce the two-parent contact contract (R9–R10) and best-effort geocode
    a typed address so a student lands on a real route stop without needing a
    manual map pin (#4, #13).

    Every student needs a Parent 1 name, at least one phone across the two
    parent slots, and at least one email across the two slots — the emails
    drive parent-account linking (R11), the phones the emergency contact list.
    ``parent_phone2`` is Parent 2's phone (the pre-existing "second phone"
    column, reused rather than renamed).

    Single saves allow the free OSM fallback (so addresses get coordinates even
    with no maps key); bulk upload passes ``geocode_fallback=False`` to avoid
    hammering the free service's rate limit row-by-row.
    """
    if not str(data.get("parent_name") or "").strip():
        raise BadRequestError("Parent 1 name is required")
    data["parent_name"] = str(data["parent_name"]).strip()
    data["parent2_name"] = str(data.get("parent2_name") or "").strip() or None
    data["parent_phone"] = clean_phone(data.get("parent_phone"), field="parent 1 phone")
    data["parent_phone2"] = clean_phone(data.get("parent_phone2"), field="parent 2 phone")
    data["parent_email"] = clean_email(data.get("parent_email"), field="parent 1 email")
    data["parent2_email"] = clean_email(data.get("parent2_email"), field="parent 2 email")
    if not data["parent_phone"] and not data["parent_phone2"]:
        raise BadRequestError("At least one parent phone number is required")
    if not data["parent_email"] and not data["parent2_email"]:
        raise BadRequestError("At least one parent email is required")
    if data.get("home_address") and (data.get("home_lat") is None or data.get("home_lng") is None):
        hit = geo_service.geocode(data["home_address"], allow_fallback=geocode_fallback)
        if hit:
            data["home_lat"], data["home_lng"] = hit["lat"], hit["lng"]
            # U12 stability-rule input: WHO resolved it. A fallback provider is
            # the triage tiers' "ambiguous" (see _triage_row) and must not move
            # an already-placed pin — _pin_stability consumes and removes this.
            data["_geo_provider"] = hit["provider"]
    return data


# Providers whose geocode counts as CONFIDENT — the same set _triage_row
# treats as 'resolved' (anything else is ambiguous, U10/R18).
CONFIDENT_GEO_PROVIDERS = ("google", "mapbox")


def _drop_geo_marker(data: dict) -> dict:
    """Paths without a prior pin (create, bulk insert) discard the U12
    stability marker — there is nothing placed to preserve."""
    data.pop("_geo_provider", None)
    return data


def _pin_stability(student_id: str, data: dict) -> dict:
    """U12 address-change stability rule (origin: 'an address change for a
    placed child keeps the existing stop until confidently re-resolved').

    An address edit whose re-geocode came back ambiguous (fallback provider)
    or failed (no coordinates at all) must NOT drop or move an existing pin:
    the student keeps their stale coordinates and provenance, so the
    regeneration that follows keeps the existing stop exactly where families
    were told it is. The pin moves only on a CONFIDENT re-resolution
    (google/mapbox — the same set the bulk triage calls 'resolved') or an
    explicit client pin (payload coordinates, e.g. the PlacePicker's picked
    pin, which arrives with no geocode marker). A student with no existing
    pin is untouched — U10's triage owns first-time resolution."""
    provider = data.pop("_geo_provider", None)
    has_new = data.get("home_lat") is not None and data.get("home_lng") is not None
    if has_new and (provider is None or provider in CONFIDENT_GEO_PROVIDERS):
        return data
    with get_connection() as conn:
        row = conn.execute(
            "select home_lat, home_lng, provenance from live_students where id = %s",
            (student_id,),
        ).fetchone()
    if row and row["home_lat"] is not None and row["home_lng"] is not None:
        data["home_lat"], data["home_lng"] = row["home_lat"], row["home_lng"]
        data["provenance"] = row["provenance"]
    return data


class StudentPayload(BaseModel):
    name: str
    grade: str | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_phone2: str | None = None  # Parent 2's phone (reused column)
    parent_email: str | None = None
    parent2_name: str | None = None
    parent2_email: str | None = None
    home_address: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    # How the home coordinates were resolved (U4/R11): typed/picked/imported/
    # legacy. 'picked' is the operator's deliberate pin — a later address edit or
    # background re-geocode must not overwrite it (guarded in PlacePicker/geocode).
    provenance: str | None = None
    pickup_time: str | None = None
    # Still accepted for backwards compatibility, but PUT ignores it: admin
    # edits must never reset a live status (R7).
    status: str | None = "at-school"
    bus_id: str | None = None
    school_id: str | None = None
    route_ids: list[str] = []


class BulkRow(BaseModel):
    name: str
    grade: str | None = None
    parent_name: str | None = None
    parent_phone: str | None = None
    parent_phone2: str | None = None  # Parent 2's phone (reused column)
    parent_email: str | None = None
    parent2_name: str | None = None
    parent2_email: str | None = None
    home_address: str | None = None
    home_lat: float | None = None
    home_lng: float | None = None
    pickup_time: str | None = None
    # Retired assignment column (U10/R17): still parsed so old files upload,
    # but it assigns nothing — route membership is a planning output. A row
    # carrying it imports the student and gets an informational note.
    route_name: str | None = None
    # Set when a row's home was repaired via the PlacePicker (U8); a raw CSV
    # row leaves this null and the DAO stamps 'imported'. A confirmed
    # ambiguous proposal ships as plain coordinates (still 'imported'); only
    # a hand-placed pin carries 'picked'.
    provenance: str | None = None
    # U10 duplicate resolution: 'skip' leaves the existing student untouched,
    # 'update' overwrites their contacts/address (re-triaged). A duplicate row
    # with neither choice errors — nothing is ever silently doubled.
    duplicate_action: str | None = None


class BulkPayload(BaseModel):
    # The upload dialog is scoped to one school (U10): every committed row is
    # stamped with this id — school-less students are invisible to the
    # school-scoped draft basis and the pin map. Optional at the MODEL level
    # only: a stale cached admin tab (backend deployed, CloudFront invalidation
    # still propagating) posts the pre-U10 shape, and a required field would
    # answer it with FastAPI's array-422, which the client degrades to a
    # generic message. _require_school turns the skew window into one clear,
    # actionable 400 instead; behavior with the field present is unchanged.
    school_id: str | None = None
    students: list[BulkRow]


def _require_school(payload: BulkPayload) -> str:
    """The bulk school scope, or the one readable stale-tab error (see
    BulkPayload.school_id). A malformed request, not a lookup miss — 400, not
    the 404 reserved for a school_id that matches no school."""
    if not payload.school_id:
        raise BadRequestError(
            "School is required — refresh the page to load the updated upload dialog"
        )
    return payload.school_id


def _bulk_name_key(name: str | None) -> str:
    """The duplicates dict's key: the same lower/strip normalization the
    batched DAO lookup uses. A blank name yields "" — never a dict key, so
    blank rows keep matching nothing (the old per-row behavior)."""
    return str(name or "").strip().lower()


@router.get("")
def list_students(user: dict = Depends(admin_only)):
    # Admin-only: rows carry parent contact PII and home coordinates, and the
    # email slots gate parent-account linking (R11). Drivers get their roster
    # via /api/runs/driver/context; parents via /api/parent-portal/children.
    return safe_call(dao.list_students)


@router.post("")
def create_student(
    payload: StudentPayload, background_tasks: BackgroundTasks,
    user: dict = Depends(admin_only),
):
    # The response carries stops_recalculated (U6/R10): false when an affected
    # auto route fell back to the preserved/pickup-time order instead of
    # recomputing geometry (the durable signal is live_routes.last_recalc_degraded).
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    result = safe_call(lambda: dao.create_student(_drop_geo_marker(_clean_student(data)), route_ids))
    # U13: seed the new child's (and the touched routes' co-riders') missing
    # baselines silently — see the module-level roster-wiring note.
    background_tasks.add_task(
        notify_route_changes, student_ids=[str(result["id"])], seed_only=True
    )
    # U12: a plannable enrolment with no route for a required leg, at a school
    # with applied plan routes, gets a slot-in proposal. Best-effort in the
    # background — a failed generation never fails the enrolment.
    background_tasks.add_task(propose_slot_ins, [str(result["id"])])
    return result


@router.put("/{student_id}")
def update_student(
    student_id: str, payload: StudentPayload, background_tasks: BackgroundTasks,
    user: dict = Depends(admin_only),
):
    # Carries stops_recalculated like create (U6/R10).
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    # U13: capture the routes the student may be about to LEAVE — the update
    # rewrites the links this expansion would otherwise follow.
    prior_route_ids = safe_call(lambda: push_dao.routes_of_students([student_id]))
    # U12 stability rule: an ambiguous/failed re-geocode keeps the placed pin.
    result = safe_call(
        lambda: dao.update_student(
            student_id, _pin_stability(student_id, _clean_student(data)), route_ids
        )
    )
    if result is not None:
        background_tasks.add_task(
            notify_route_changes, route_ids=prior_route_ids,
            student_ids=[student_id], seed_only=True,
        )
        # U12: a confidently re-resolved address change (or any edit that
        # leaves a required leg routeless) refreshes the slot-in proposals.
        background_tasks.add_task(propose_slot_ins, [student_id])
    return result


@router.delete("/{student_id}")
def delete_student(
    student_id: str, background_tasks: BackgroundTasks, user: dict = Depends(admin_only)
):
    # U13: pre-capture the routes the cascade is about to unlink; the deleted
    # child's own baselines cascade away with the student row, so only the
    # co-riders' missing baselines are seeded (silently).
    prior_route_ids = safe_call(lambda: push_dao.routes_of_students([student_id]))
    result = safe_call(lambda: (dao.delete_student(student_id), {"ok": True})[1])
    if prior_route_ids:
        background_tasks.add_task(
            notify_route_changes, route_ids=prior_route_ids, seed_only=True
        )
    return result


# The one per-row wording for the retired assignment column (U10/R17); the
# validate table and the commit response both surface it verbatim.
ROUTE_COLUMN_NOTE = "route column ignored — routes come from the fleet plan"


def _triage_row(row: BulkRow) -> dict:
    """Geocode one bulk row and classify it (U8/R15): resolved (a confident
    provider result or coords already supplied), ambiguous (only the low-
    confidence fallback resolved it — the operator should confirm the proposed
    pin), or failed (no coordinates — the operator places the pin by hand)."""
    lat, lng, provider, label, status = row.home_lat, row.home_lng, None, None, "resolved"
    if lat is None or lng is None:
        hit = geo_service.geocode(row.home_address, allow_fallback=True) if row.home_address else None
        if hit:
            lat, lng, provider, label = hit["lat"], hit["lng"], hit["provider"], hit.get("label")
            status = "resolved" if provider in ("google", "mapbox") else "ambiguous"
        else:
            status = "failed"
    return {
        "index": None, "name": row.name, "address": row.home_address,
        "lat": lat, "lng": lng, "provider": provider, "label": label, "status": status,
        # R17: the column is informational-only now — no resolution lookup, the
        # same note whether or not a route by that name exists.
        "route_name": row.route_name,
        "route_note": ROUTE_COLUMN_NOTE if row.route_name else None,
    }


@router.post("/bulk/validate")
def bulk_validate(payload: BulkPayload, user: dict = Depends(admin_only)):
    """Import-time triage (U10/R16-R18): geocode every row and return its tier
    (resolved / ambiguous / failed) + provider + proposed pin, flag duplicates
    of existing students at this school by (name, school), and note the retired
    route column — WITHOUT inserting anything. The client renders the triage
    table (ambiguous rows confirm the proposed pin in one click, failed rows
    open the map picker, duplicates choose skip-or-update) and only then POSTs
    /bulk to commit."""
    def run() -> dict:
        school_id = _require_school(payload)
        # ONE batched duplicate lookup for the whole upload — the per-row
        # query was O(rows) round-trips per request.
        duplicates = dao.find_bulk_duplicates(
            [row.name for row in payload.students], school_id
        )
        rows = []
        for index, row in enumerate(payload.students):
            rows.append({
                **_triage_row(row),
                "index": index,
                "duplicate_of": duplicates.get(_bulk_name_key(row.name)),
            })
        return {"rows": rows}

    return safe_call(run)


@router.post("/bulk")
def bulk_upload(
    payload: BulkPayload, background_tasks: BackgroundTasks,
    user: dict = Depends(admin_only),
):
    # Touched student/route ids collected by run() — read after safe_call for
    # the U13 seed dispatch. Freshly inserted students have no routes yet (no
    # stop, nothing to seed), but a duplicate-update moves an existing child's
    # home on their live routes, so their (and their co-riders') missing
    # baselines are seeded silently like every roster path.
    touched_students: list[str] = []
    touched_routes: list[str] = []

    def run() -> dict:
        inserted = 0
        updated = 0
        skipped = 0
        parent_assignments = 0
        notes: list[str] = []
        errors: list[str] = []
        school_id = _require_school(payload)
        if not dao.school_exists(school_id):
            raise NotFoundError("School not found — pick the school this upload belongs to")
        # Duplicates re-detected server-side at commit (never trusted from the
        # client): same (name, school) as an existing student. ONE batched
        # lookup for the whole upload (the per-row query was O(rows) round-
        # trips); the dict then grows with each insert so a second same-named
        # NEW row in the same file is still flagged against the first — the
        # exact behavior the per-row re-query had, and nothing is ever
        # silently doubled.
        duplicates = dao.find_bulk_duplicates(
            [row.name for row in payload.students], school_id
        )
        for index, row in enumerate(payload.students):
            label = row.name or f"row {index + 1}"
            if not row.name or not row.grade or not row.parent_name:
                errors.append(f"{label}: missing required field (name, grade, parent name)")
                continue
            if not row.parent_phone and not row.parent_phone2:
                errors.append(f"{label}: at least one parent phone is required")
                continue
            if not row.parent_email and not row.parent2_email:
                errors.append(f"{label}: at least one parent email is required")
                continue
            if row.duplicate_action not in (None, "skip", "update"):
                errors.append(f"{label}: duplicate_action must be 'skip' or 'update'")
                continue
            # R17: the retired column never assigns — the student still imports,
            # and the row gets the informational note instead of a link.
            if row.route_name:
                notes.append(f"{label}: {ROUTE_COLUMN_NOTE}")
            try:
                data = _drop_geo_marker(_clean_student(row.model_dump(), geocode_fallback=True))
                data["school_id"] = school_id
                name_key = _bulk_name_key(row.name)
                duplicate = duplicates.get(name_key)
                if duplicate is not None:
                    if row.duplicate_action == "skip":
                        skipped += 1
                        continue
                    if row.duplicate_action == "update":
                        result = dao.update_bulk_student(str(duplicate["id"]), data)
                        updated += 1
                        parent_assignments += result["parent_links"]
                        touched_students.append(str(duplicate["id"]))
                        touched_routes.extend(result["route_ids"])
                        continue
                    errors.append(
                        f"{label}: matches an existing student at this school "
                        "— choose skip or update"
                    )
                    continue
                result = dao.insert_bulk_student(data)
                inserted += 1
                parent_assignments += result["parent_links"]
                touched_students.append(str(result["id"]))
                # The fresh insert is now the school's existing student by this
                # name: register it so a later same-named row in this file is
                # flagged, in the same duplicate_of shape the lookup returns.
                if name_key:
                    duplicates.setdefault(name_key, {
                        "id": result["id"],
                        "name": data["name"],
                        "home_address": data.get("home_address"),
                    })
            except Exception as exc:  # noqa: BLE001 - surfaced per-row to the client
                errors.append(f"{label}: {exc}")
        # Duplicate updates moved home pins on live routes: regenerate each
        # affected route ONCE for the whole upload (Lambda burst guard, U8).
        dao.regenerate_routes(touched_routes)
        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "parentAssignments": parent_assignments,
            "notes": notes,
            "errors": errors,
        }

    result = safe_call(run)
    if touched_students:
        # U13: ONE dispatch for the whole upload (the same burst-guard shape
        # as the single regeneration above), seed-only like every roster path.
        background_tasks.add_task(
            notify_route_changes,
            route_ids=sorted(set(touched_routes)),
            student_ids=sorted(set(touched_students)),
            seed_only=True,
        )
        # U12: one slot-in generation pass for the whole upload (burst-guard
        # shape) — plannable rows lacking a required leg at a school with an
        # applied plan get proposals; everything else no-ops.
        background_tasks.add_task(propose_slot_ins, sorted(set(touched_students)))
    return result


# Aggregate pin map with audited access (U11/R19) ----------------------------

def _pin_map(school_id: str, actor: dict) -> dict:
    """Every student pin for one school in ONE audited read (R19).

    One screen showing every child's home is a higher-value target than any
    single record, so access itself is the audited event: the read and its
    'pin-map-viewed' live_admin_audit row share a transaction — the response
    and the row commit or vanish together, and a served pin map without its
    audit row is impossible. The insert mirrors fleet_plan_dao's apply/restore
    writers column for column (actor name/email denormalized so the row
    outlives the account); it lives here rather than a DAO per the U11 file
    scope — a second consumer should extract the shared helper.

    Students split by triage state: 'placed' (real coordinates — a map marker)
    vs 'unresolved' (no usable coordinates — listed by name beside the map so
    the operator can open each one and place the pin). This endpoint is the
    aggregate's ONLY source; the frontend must not assemble it from the
    regular students list, which would bypass the audit.
    """
    with get_connection() as conn:
        if conn.execute(
            "select 1 from live_schools where id = %s", (school_id,)
        ).fetchone() is None:
            raise NotFoundError("School not found — pick the school whose pins to view")
        rows = conn.execute(
            "select id, name, home_address, home_lat, home_lng, provenance "
            "from live_students where school_id = %s order by name asc",
            (school_id,),
        ).fetchall()
        placed: list[dict] = []
        unresolved: list[dict] = []
        for row in rows:
            has_pin = row["home_lat"] is not None and row["home_lng"] is not None
            pin = {
                "id": str(row["id"]),
                "name": row["name"],
                "address": row["home_address"],
                # Coerced to float: the columns are numeric and a raw Decimal
                # serializes as a JSON string, which no map marker can place.
                "lat": float(row["home_lat"]) if has_pin else None,
                "lng": float(row["home_lng"]) if has_pin else None,
                "provenance": row["provenance"],
                "state": "placed" if has_pin else "unresolved",
            }
            (placed if has_pin else unresolved).append(pin)
        record_audit(
            conn,
            action="pin-map-viewed",
            actor=actor,
            school_id=school_id,
            resource_type="school",
            resource_id=school_id,
            detail={"pin_count": len(placed), "unresolved_count": len(unresolved)},
        )
    return {"school_id": school_id, "placed": placed, "unresolved": unresolved}


@router.get("/pin-map")
def pin_map(school_id: str, user: dict = Depends(admin_only)):
    # Admin-only like the students list, but the aggregate view carries its own
    # audit trail on top (R19) — see _pin_map. Exactly one audit row per call.
    return safe_call(lambda: _pin_map(school_id, user))


# Absences (#7) --------------------------------------------------------------

class AbsencePayload(BaseModel):
    student_id: str
    date: str | None = None  # defaults to today (Africa/Nairobi)
    reason: str | None = None


@router.get("/absences")
def list_absences(date: str | None = None, user: dict = Depends(admin_only)):
    # Admin-only: named child absences are exactly the data the incidents
    # feed was locked down for; the sole consumer is the admin StudentsPage.
    # Rows carry scope ('day'/'morning'/'afternoon') and source
    # ('parent'/'driver'/'admin') so the UI can render partial parent
    # cancellations and gate its actions on provenance (U4).
    return safe_call(lambda: absence_dao.list_absences(date))


@router.post("/absences")
def mark_absent(payload: AbsencePayload, user: dict = Depends(admin_only)):
    # date=None defaults to today (Africa/Nairobi) inside the DAO. A
    # today-dated mark also sets the live status to 'absent' and appends the
    # run_absences snapshot of any active run carrying the student (R25b);
    # other dates are bookkeeping only. An admin mark is always whole-day:
    # the DAO escalates an existing partial parent cancellation to
    # scope='day', source='admin' (U4).
    return safe_call(
        lambda: absence_dao.mark_absent(payload.student_id, payload.date, payload.reason, user["id"])
    )


@router.delete("/absences/{absence_id}")
def clear_absence(absence_id: str, user: dict = Depends(admin_only)):
    # Clearing a today-dated absence 409s while the student is involved in an
    # active run of a type the absence covers ("End the run first"), else
    # resets an 'absent' status to 'at-school' when the row was whole-day.
    # Past/future-dated clears have no status side-effects.
    return safe_call(lambda: (absence_dao.clear_absence(absence_id), {"ok": True})[1])
