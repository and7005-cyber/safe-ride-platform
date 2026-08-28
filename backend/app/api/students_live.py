from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from app.api._helpers import safe_call
from app.core.auth import get_current_user
from app.core.errors import BadRequestError
from app.core.permissions import require_director, require_staff
from app.core.scope import SchoolScope
from app.core.validation import clean_email, clean_phone
from app.dao.absence_dao import AbsenceDao
from app.dao.push_dao import PushDao
from app.dao.student_live_dao import StudentLiveDao
from app.services import geo_service
from app.services.push_service import notify_route_changes
from app.services.slot_in_service import propose_slot_ins

router = APIRouter(prefix="/api/students", tags=["students"])
dao = StudentLiveDao()
absence_dao = AbsenceDao()
push_dao = PushDao()

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


def _pin_stability(scope: SchoolScope, student_id: str, data: dict) -> dict:
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
    pin is untouched — U10's triage owns first-time resolution.

    The prior-pin read goes through the scoped DAO (U6): a foreign student
    yields no row here and the update itself answers 404 right after."""
    provider = data.pop("_geo_provider", None)
    has_new = data.get("home_lat") is not None and data.get("home_lng") is not None
    if has_new and (provider is None or provider in CONFIDENT_GEO_PROVIDERS):
        return data
    row = dao.student_pin_snapshot(scope, student_id)
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
    # U6: the upload's school comes from the request scope now. The field is
    # kept so the shipped dialog's payload (which still sends it) parses, and
    # it is IGNORED — the Release 4 stray-school_id rule.
    school_id: str | None = None
    students: list[BulkRow]


def _bulk_name_key(name: str | None) -> str:
    """The duplicates dict's key: the same lower/strip normalization the
    batched DAO lookup uses. A blank name yields "" — never a dict key, so
    blank rows keep matching nothing (the old per-row behavior)."""
    return str(name or "").strip().lower()


@router.get("")
def list_students(scope: SchoolScope = Depends(require_staff)):
    # Staff-only: rows carry parent contact PII and home coordinates, and the
    # email slots gate parent-account linking (R11). Drivers get their roster
    # via /api/runs/driver/context; parents via /api/parent-portal/children.
    return safe_call(lambda: dao.list_students(scope))


@router.post("")
def create_student(
    payload: StudentPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # The response carries stops_recalculated (U6/R10): false when an affected
    # auto route fell back to the preserved/pickup-time order instead of
    # recomputing geometry (the durable signal is live_routes.last_recalc_degraded).
    data = payload.model_dump()
    route_ids = data.pop("route_ids")
    result = safe_call(
        lambda: dao.create_student(
            scope, _drop_geo_marker(_clean_student(data)), route_ids, actor=user
        )
    )
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
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
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
            scope, student_id,
            _pin_stability(scope, student_id, _clean_student(data)), route_ids,
            actor=user,
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
    student_id: str, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_director),
    user: dict = Depends(get_current_user),
):
    # U13: pre-capture the routes the cascade is about to unlink; the deleted
    # child's own baselines cascade away with the student row, so only the
    # co-riders' missing baselines are seeded (silently).
    prior_route_ids = safe_call(lambda: push_dao.routes_of_students([student_id]))
    result = safe_call(
        lambda: (dao.delete_student(scope, student_id, actor=user), {"ok": True})[1]
    )
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
def bulk_validate(payload: BulkPayload, scope: SchoolScope = Depends(require_staff)):
    """Import-time triage (U10/R16-R18): geocode every row and return its tier
    (resolved / ambiguous / failed) + provider + proposed pin, flag duplicates
    of existing students at this school by (name, school), and note the retired
    route column — WITHOUT inserting anything. The client renders the triage
    table (ambiguous rows confirm the proposed pin in one click, failed rows
    open the map picker, duplicates choose skip-or-update) and only then POSTs
    /bulk to commit. The school is the request scope (U6); the payload's
    school_id is ignored."""
    def run() -> dict:
        # ONE batched duplicate lookup for the whole upload — the per-row
        # query was O(rows) round-trips per request.
        duplicates = dao.find_bulk_duplicates(
            scope, [row.name for row in payload.students]
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
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
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
        # U6: the upload belongs to the request scope's school — the payload's
        # school_id (if any) is ignored, and the scope school always exists.
        school_id = scope.school_id
        # Duplicates re-detected server-side at commit (never trusted from the
        # client): same (name, school) as an existing student. ONE batched
        # lookup for the whole upload (the per-row query was O(rows) round-
        # trips); the dict then grows with each insert so a second same-named
        # NEW row in the same file is still flagged against the first — the
        # exact behavior the per-row re-query had, and nothing is ever
        # silently doubled.
        duplicates = dao.find_bulk_duplicates(
            scope, [row.name for row in payload.students]
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
                        result = dao.update_bulk_student(scope, str(duplicate["id"]), data)
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
                result = dao.insert_bulk_student(scope, data)
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
        dao.regenerate_routes(scope, touched_routes)
        if inserted or updated:
            # One 'students-imported' audit row per upload (U6/U9). The bulk
            # path is deliberately multi-transaction (per-row error capture),
            # so the summary row rides its own small transaction after the
            # loop — counts only, never names.
            dao.record_import(scope, user, {
                "inserted": inserted, "updated": updated,
                "skipped": skipped, "errors": len(errors),
            })
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

@router.get("/pin-map")
def pin_map(
    school_id: str | None = None,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # Staff-only like the students list, but the aggregate view carries its
    # own audit trail on top (R19) — see StudentLiveDao.pin_map: exactly one
    # 'pin-map-viewed' audit row per call, in the read's own transaction.
    # U6: the school IS the request scope; the legacy school_id query param
    # is accepted and ignored (the stray-school-id rule).
    return safe_call(lambda: dao.pin_map(scope, actor=user))


# Absences (#7) --------------------------------------------------------------

class AbsencePayload(BaseModel):
    student_id: str
    date: str | None = None  # defaults to today (Africa/Nairobi)
    reason: str | None = None


@router.get("/absences")
def list_absences(
    date: str | None = None, scope: SchoolScope = Depends(require_staff)
):
    # Staff-only: named child absences are exactly the data the incidents
    # feed was locked down for; the sole consumer is the admin StudentsPage.
    # Rows carry scope ('day'/'morning'/'afternoon') and source
    # ('parent'/'driver'/'admin') so the UI can render partial parent
    # cancellations and gate its actions on provenance (U4). Scoped through
    # the student's school (U6).
    return safe_call(lambda: absence_dao.list_absences(scope, date))


@router.post("/absences")
def mark_absent(
    payload: AbsencePayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # date=None defaults to today (Africa/Nairobi) inside the DAO. A
    # today-dated mark also sets the live status to 'absent' and appends the
    # run_absences snapshot of any active run carrying the student (R25b);
    # other dates are bookkeeping only. An admin mark is always whole-day:
    # the DAO escalates an existing partial parent cancellation to
    # scope='day', source='admin' (U4). Another school's student → 404 (U6).
    return safe_call(
        lambda: absence_dao.mark_absent(
            scope, payload.student_id, payload.date, payload.reason, actor=user
        )
    )


@router.delete("/absences/{absence_id}")
def clear_absence(
    absence_id: str,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # Clearing a today-dated absence 409s while the student is involved in an
    # active run of a type the absence covers ("End the run first"), else
    # resets an 'absent' status to 'at-school' when the row was whole-day.
    # Past/future-dated clears have no status side-effects. Mark AND clear
    # stay require_staff (U6 decision): both are operational day-to-day acts,
    # not history destruction — a coordinator clears a mistaken mark.
    return safe_call(
        lambda: (absence_dao.clear_absence(scope, absence_id, actor=user), {"ok": True})[1]
    )
