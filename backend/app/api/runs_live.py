import logging
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from pydantic import BaseModel

from app.api._helpers import map_error, safe_call
from app.core.auth import get_current_user
from app.core.errors import ActionReplayed, ClosureRefusedError
from app.core.permissions import require_driver_scope, require_school, require_staff
from app.core.scope import STAFF_ROLES, SchoolScope
from app.dao.exception_dao import ExceptionDao
from app.dao.idempotency_dao import ActionEnvelope, parse_key
from app.dao.incident_dao import IncidentDao
from app.dao.position_dao import PositionDao
from app.dao.run_dao import RunDao
from app.services.push_service import PushService

logger = logging.getLogger("saferide.runs")

router = APIRouter(prefix="/api/runs", tags=["runs"])
dao = RunDao()
incident_dao = IncidentDao()
exception_dao = ExceptionDao()
position_dao = PositionDao()
push_service = PushService()
# The runs list serves two surfaces (U7): staff read their school's runs; a
# driver token resolves to their own school and the DAO narrows to their
# bus's runs. Everything mutating below is require_staff or driver-scoped.
require_staff_or_driver = require_school(*STAFF_ROLES, "driver")

T = TypeVar("T")

# The driver's action envelope (GPS plan U7; client half U6): every one of
# the seven action routes reads this header. Absent, blank or not a UUID
# means "no key" — the action runs normally and stores nothing — never a 4xx.
IdempotencyKeyHeader = Header(default=None, alias="Idempotency-Key")


class RunPayload(BaseModel):
    bus_id: str | None = None
    route_id: str | None = None
    # Accepted and IGNORED (U7): the run's school comes from the scope.
    school_id: str | None = None
    type: str | None = "morning"
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    # No default: create coalesces a missing status to 'in-progress', while
    # update must be able to tell "omitted" from "explicitly set". Defaulting
    # here meant every edit that left the status out arrived as an explicit
    # 'in-progress' and silently reopened a finished run (U7/R16).
    status: str | None = None
    total_stops: int | None = 0
    stops_completed: int | None = 0
    total_students: int | None = 0
    students_boarded: int | None = 0
    incidents: int | None = 0


class EnvelopeFields(BaseModel):
    """What every driver action body may carry beside its own fields (GPS
    plan U7, client half U6). Both are ``Any`` on purpose: the fix is
    validated leniently inside the transaction — a malformed or out-of-range
    value becomes reason ``invalid`` on the trail row, never a 422 (R40) —
    and the device id is diagnostic only, stored as an opaque string when it
    is one and dropped otherwise. Older clients send neither."""

    fix: Any = None
    device_id: Any = None


class StartRunPayload(EnvelopeFields):
    route_id: str


class RunIdPayload(EnvelopeFields):
    run_id: str


class ArrivePayload(RunIdPayload):
    # The stop this tap meant to reach — stops_completed + 1 as the client last
    # saw it (GPS plan U7/R33). A tap whose expectation has passed is a
    # recorded no-op; absent (older clients), every tap is "arrive at next".
    expected_stop_order: int | None = None


class BoardingPayload(EnvelopeFields):
    student_id: str
    on_bus: bool
    # The bypassed-stop prompt this tap came through, when it did (GPS plan
    # U3/R15): the server records the tap on that exception's ledger as its
    # resolution. Optional, and never a reason to refuse the tap.
    event_id: str | None = None


class StudentIdPayload(EnvelopeFields):
    student_id: str
    # Same courtesy for drop-off (GPS plan U3); reverse takes it up in U9 for
    # the prompt's undo, and contacted ignores it.
    event_id: str | None = None


class AbsentPayload(EnvelopeFields):
    student_id: str
    # Default false: a driver sees one run. Claiming the whole day from a single
    # stop was the old behaviour, and it struck the child off the other run's
    # roster too — so the bus never stopped for them (U8/R17).
    whole_day: bool = False
    event_id: str | None = None


class PromptAnswerPayload(BaseModel):
    answer: str


class HandoverPayload(EnvelopeFields):
    student_id: str
    note: str


def _envelope(key_header: str | None, payload: EnvelopeFields) -> ActionEnvelope:
    return ActionEnvelope(key=parse_key(key_header), fix=payload.fix, device_id=payload.device_id)


def _tap(action: Callable[[], T]) -> tuple[T | Any, bool]:
    """``safe_call`` for the envelope actions: ``(result, replayed)``.

    A key seen before with the same fingerprint raises ``ActionReplayed``
    from inside the DAO with the stored body; the router returns that body
    as-is and dispatches nothing — the push, the office alert and the purge
    already happened for the tap that first carried the key (R33).
    """
    try:
        return action(), False
    except ActionReplayed as replay:
        return replay.body, True
    except Exception as error:
        raise map_error(error) from error


# Admin run CRUD (U7: school-scoped) ------------------------------------------

@router.get("")
def list_runs(active: bool = False, scope: SchoolScope = Depends(require_staff_or_driver)):
    """?active=true narrows to today's (Africa/Nairobi) non-completed runs —
    the dashboard's Active Runs card (R5). Scoped to the active school; a
    driver sees only their own bus's runs."""
    return safe_call(lambda: dao.list_runs(scope, active=active))


@router.post("")
def create_run(
    payload: RunPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    return safe_call(lambda: dao.create_run(scope, payload.model_dump(), actor=user))


@router.put("/{run_id}")
def update_run(
    run_id: str,
    payload: RunPayload,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    return safe_call(lambda: dao.update_run(scope, run_id, payload.model_dump(), actor=user))


@router.delete("/{run_id}")
def delete_run(
    run_id: str,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    # AE27 lives in the DAO: a coordinator deletes only a non-completed run
    # (open-run cleanup); a completed run's delete requires the director.
    return safe_call(lambda: (dao.delete_run(scope, run_id, actor=user), {"ok": True})[1])


@router.post("/{run_id}/force-close")
def force_close_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    """Close a run no driver can resolve (U6/R12-R15).

    The arrival notification is not decided here: force_close_run resolves it
    inside the transaction that reads the gate arrival and the confirmed
    boardings, and hands back `boarded_student_ids` — empty when there is no
    evidence to notify on. notify_run_ended then sends to exactly that set,
    so there is one decision point rather than two that can disagree.
    """
    result = safe_call(lambda: dao.force_close_run(scope, run_id, actor=user))
    # Prompts the close marked unanswered (GPS plan U3): no call-now follows
    # from a close, so nothing consumes this yet; kept off the response.
    result.pop("auto_resolved", None)
    background_tasks.add_task(push_service.notify_run_ended, result, scope=scope)
    outstanding = [c["student_name"] for c in result.get("unaccounted") or []]
    background_tasks.add_task(
        _record_lifecycle_alert, scope, str(result["id"]), "force-closed",
        (
            "Unaccounted, and owed a phone call: " + ", ".join(outstanding) + "."
            if outstanding
            else "Every child was already accounted for."
        ),
    )
    return result


@router.post("/{run_id}/contacted")
def record_parent_contact(
    run_id: str, payload: StudentIdPayload, scope: SchoolScope = Depends(require_staff)
):
    """Record that the office phoned an unaccounted child's parents (R14).

    No automated message goes to these families — nobody knows where the child
    is, and a push saying so is worse than a call. This is the app tracking that
    the call happened instead of pretending it did.
    """
    return safe_call(lambda: dao.record_parent_contact(scope, run_id, payload.student_id))


@router.get("/{run_id}/report")
def run_report(run_id: str, scope: SchoolScope = Depends(require_staff)):
    """Post-run report (R14-R16): the run row + bus/route/driver names + the
    absent_students snapshot (approximate=true on the legacy fallback) + the
    run's stop exceptions with derived status (GPS plan U2)."""
    return safe_call(lambda: dao.run_report(scope, run_id))


@router.get("/{run_id}/exceptions")
def list_run_exceptions(run_id: str, scope: SchoolScope = Depends(require_staff)):
    """A run's stop exceptions (GPS plan U2/R21): kind, stop, the children
    still without an outcome, derived open/resolved status, review state and
    the prompt/response ledger. Staff of the active school only; a run of
    another school is 404 through RLS. Parents have no route to this (R22)."""
    return safe_call(lambda: exception_dao.list_for_run(scope, run_id))


@router.post("/{run_id}/exceptions/{exception_id}/review")
def review_run_exception(
    run_id: str,
    exception_id: str,
    scope: SchoolScope = Depends(require_staff),
    user: dict = Depends(get_current_user),
):
    """Mark an exception reviewed (R21): director or coordinator, the provider
    stepped in as director. Idempotent; the first review writes one
    exception-reviewed audit row carrying the exception id only."""
    return safe_call(
        lambda: exception_dao.mark_reviewed(scope, run_id, exception_id, actor=user)
    )


# Driver run lifecycle (U7: driver-scoped — school derived, header refused) ----

@router.get("/driver/context")
def driver_context(scope: SchoolScope = Depends(require_driver_scope)):
    """The driver's bus, routes, active run, roster, blocking set and — GPS
    plan U3/R34 — `pending_prompts`: the open run's pending prompts, safety
    kinds first, each with its event id, copy inputs and allowed answers.
    Polled every 5 s, so a reload or a second device re-shows them."""
    return safe_call(lambda: dao.get_driver_context(scope))


@router.post("/driver/prompts/{event_id}/shown")
def acknowledge_prompt_shown(event_id: str, scope: SchoolScope = Depends(require_driver_scope)):
    """The card mounted (GPS plan U3): stamp shown_at once, first stamp wins.
    The event must belong to this driver's run (403 otherwise; another
    school's event is 404 through RLS)."""
    return safe_call(lambda: exception_dao.acknowledge_shown(scope, event_id))


@router.post("/driver/prompts/{event_id}/respond")
def respond_to_prompt(
    event_id: str, payload: PromptAnswerPayload,
    scope: SchoolScope = Depends(require_driver_scope),
):
    """Record the driver's answer to a prompt (GPS plan U3/R23, R34).

    Same answer again: 200, unchanged. A different answer after one was
    recorded: 409 `prompt-already-answered`; any answer after Arrive, End Run
    or force-close closed it: 409 `prompt-resolved` — both carry the recorded
    state in `detail`, and the client drops the card and refreshes on either.
    """
    return safe_call(lambda: exception_dao.respond(scope, event_id, payload.answer))


@router.post("/driver/start")
def start_run(
    payload: StartRunPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    """Start a run (GPS plan U7 envelope). The fix is recorded on the trail
    and the served position stays at the school checkpoint (R36); the run is
    stamped with this session. After the response is built, one bounded
    retention pass runs for the school (R12) — skipped, never failed, when
    another pass holds the lock or the batch times out."""
    run, replayed = _tap(
        lambda: dao.start_run(scope, payload.route_id, envelope=_envelope(idempotency_key, payload))
    )
    if replayed:
        return run
    background_tasks.add_task(push_service.notify_run_started, run, scope=scope)
    background_tasks.add_task(_record_lifecycle_alert, scope, str(run["id"]), "run-started")
    background_tasks.add_task(_purge_after_start, scope)
    return run


@router.post("/driver/arrive")
def arrive(
    payload: ArrivePayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    """Arrive at the next stop. Response ``{run, arrival_incident, prompts,
    noop}``. With ``expected_stop_order`` behind the run's progress the tap is
    a recorded no-op (GPS plan U7/R33): ``noop`` true, the current run, no
    incident, no prompts, and nothing dispatched from here."""
    result, replayed = _tap(
        lambda: dao.arrive_next_stop(
            scope, payload.run_id, expected_stop_order=payload.expected_stop_order,
            envelope=_envelope(idempotency_key, payload),
        )
    )
    if replayed:
        return result
    # Prompts this Arrive closed as unanswered (GPS plan U3): U10 decides the
    # call-now notice from them here; until then they stay off the response.
    result.pop("auto_resolved", None)
    if result.get("noop"):
        return result
    if result.get("arrival_incident"):
        background_tasks.add_task(push_service.notify_reached_school, result["run"], scope=scope)
    # Arriving a stop means the next stop's children should get ready.
    background_tasks.add_task(push_service.notify_bus_approaching, result["run"], scope=scope)
    # A newly raised bypassed-stop exception reaches the office through the
    # lifecycle feed (GPS plan U2/R15, R21): post-commit, DAO-direct, never the
    # parent fan-out, and marked lifecycle so the parent alerts reader excludes
    # it. Raised only when the exception row itself was inserted, which is the
    # dedup on (run, stop order): a catch-up Arrive or a reopen finds the row
    # already there and stays quiet. The text names the stop and the children,
    # never a coordinate.
    bypassed = result.pop("bypassed_stop", None)
    if bypassed:
        names = ", ".join(s["name"] for s in bypassed["students"])
        background_tasks.add_task(
            _record_lifecycle_alert, scope, str(result["run"]["id"]), "stop-bypassed",
            f"Stop {bypassed['stop_order']} ({bypassed['stop_name']}): "
            f"no record yet for {names}.",
        )
    return result


@router.post("/driver/end")
def end_run(
    payload: RunIdPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    try:
        run = dao.end_run(scope, payload.run_id, envelope=_envelope(idempotency_key, payload))
    except ActionReplayed as replay:
        # The same tap again (GPS plan U7/R33): the stored body, no fan-out.
        return replay.body
    except ClosureRefusedError as refusal:
        # Recorded before the 409 leaves, not as a background task: FastAPI
        # returns the error response through its own handler, which carries no
        # background tasks — the alert would be silently dropped on exactly the
        # path the office most needs to hear about.
        _record_closure_refusal(scope, refusal)
        raise map_error(refusal) from refusal
    except Exception as error:
        raise map_error(error) from error
    run.pop("auto_resolved", None)  # GPS plan U3: closed prompts, no call-now at End Run
    background_tasks.add_task(push_service.notify_run_ended, run, scope=scope)
    background_tasks.add_task(_record_lifecycle_alert, scope, str(run["id"]), "run-completed")
    return run


@router.post("/driver/boarding")
def toggle_boarding(
    payload: BoardingPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    # Morning-only and one-way: the DAO 409s afternoon runs (use /driver/
    # dropoff) and on_bus=false (un-boarding retracts a sent safety push).
    outcome, replayed = _tap(
        lambda: dao.toggle_boarding(
            scope, payload.student_id, payload.on_bus, event_id=payload.event_id,
            envelope=_envelope(idempotency_key, payload),
        )
    )
    if replayed:
        return outcome
    student, run = outcome
    background_tasks.add_task(
        push_service.notify_student_boarded, run, payload.student_id, scope=scope
    )
    return student


@router.post("/driver/dropoff")
def dropoff_student(
    payload: StudentIdPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    """Confirm a drop-off at a reached stop on the driver's active afternoon
    run (R32). The tap-time notification carries run_id + student_id so the
    dedup index suppresses retries."""
    outcome, replayed = _tap(
        lambda: dao.dropoff_student(
            scope, payload.student_id, event_id=payload.event_id,
            envelope=_envelope(idempotency_key, payload),
        )
    )
    if replayed:
        return outcome
    student, run = outcome
    background_tasks.add_task(
        push_service.notify_student_dropped_off, student, run, scope=scope
    )
    return student


@router.post("/driver/handover")
def record_handover(
    payload: HandoverPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    """Record a hand-over away from the child's stop (U4/R12).

    A breakdown, a closed road, a guardian collecting at the roadside. Without
    this the driver's only release for a child who left the bus off-route is
    marking them absent, which tells the family the child was never on the bus
    home — false, and the class of claim this work removes.

    The parent is told their child left the bus, with the driver's note, so the
    message matches what happened rather than the route's expectation.
    """
    outcome, replayed = _tap(
        lambda: dao.record_handover(
            scope, payload.student_id, payload.note,
            envelope=_envelope(idempotency_key, payload),
        )
    )
    if replayed:
        return outcome
    student, run = outcome
    background_tasks.add_task(
        push_service.notify_student_handover, student, run, payload.note, scope=scope
    )
    background_tasks.add_task(
        _record_lifecycle_alert, scope, str(run["id"]), "handover-recorded",
        f"{student['name']} — driver's note: {payload.note}",
    )
    return student


@router.post("/driver/reverse")
def reverse_own_action(
    payload: StudentIdPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
):
    """Undo this driver's own drop-off, hand-over or absence mark (U5/R10).

    Deliberately a separate endpoint from /driver/boarding: that one's rejection
    of un-boarding is a stale-client concurrency guard, and relaxing it would
    regress that protection while appearing to change only UX.

    The affected parents always get an explicit correction. Retracting a
    statement silently would be worse than the mis-tap.
    """
    student, run, reversed_what = safe_call(
        lambda: dao.reverse_own_action(scope, payload.student_id)
    )
    background_tasks.add_task(
        push_service.notify_correction, student, run, reversed_what, scope=scope
    )
    retracted = "absence mark" if reversed_what == "absence" else "drop-off confirmation"
    background_tasks.add_task(
        _record_lifecycle_alert, scope, str(run["id"]), "action-reversed",
        f"{student['name']} — the {retracted} was retracted.",
    )
    return student


def _record_lifecycle_alert(
    scope: SchoolScope, run_id: str, incident_type: str, detail: str | None = None
) -> None:
    """Office-only run-lifecycle alert (U16/R29-R30).

    Dispatched DAO-direct, never through push_service.notify_incident — that
    path fans out bus-wide to parents, and the office has no run lifecycle in
    its feed today at all: completion shows only on the Runs page, which nobody
    watches during a route.

    Wrapped like the other admin-only alert helpers so a failing alert never
    breaks the driver's request.
    """
    try:
        incident_dao.create_lifecycle_incident(scope, run_id, incident_type, detail)
    except Exception:
        logger.exception("recording %s lifecycle alert failed", incident_type)


def _purge_after_start(scope: SchoolScope) -> None:
    """One bounded retention pass for the school after a Start Run (GPS plan
    U7/R12), wrapped like the lifecycle-alert helper: locked, timed out or
    failed means skipped and logged — never an error to the driver. The log
    line carries the school id and counts only; the statements bind ids and
    intervals, never a coordinate."""
    try:
        outcome = position_dao.purge_after_start(scope)
    except Exception as error:  # noqa: BLE001 — the driver's response is already built
        logger.warning(
            "gps purge skipped (school=%s): %s %s",
            scope.school_id, type(error).__name__, getattr(error, "sqlstate", "") or "",
        )
        return
    if outcome is None:
        logger.info("gps purge skipped (school=%s): another pass holds the lock", scope.school_id)
        return
    logger.info(
        "gps purge pass (school=%s retention_days=%s): trail=%s exceptions=%s events=%s keys=%s",
        scope.school_id, outcome["retention_days"], outcome["trail_rows_deleted"],
        outcome["exceptions_nulled"], outcome["events_nulled"], outcome["keys_deleted"],
    )


def _record_closure_refusal(scope: SchoolScope, refusal: ClosureRefusedError) -> None:
    """Office alert for a refused closure (U11/R29).

    Deduped on the blocking set rather than the run: a driver tapping End four
    times against the same unresolved children is one situation and should read
    as one alert, while a run still stuck after partial progress is a different
    situation the office has not been told about yet. Keying on the run alone
    would report the first refusal and then stay quiet as it got worse.
    """
    names = ", ".join(sorted(b["name"] for b in refusal.blocking))
    try:
        incident_dao.create_lifecycle_incident(
            scope, refusal.run_id, "closure-refused", f"Waiting on: {names}.", dedup=True
        )
    except Exception:
        logger.exception("recording closure-refused alert failed")


def _record_absent_incident(scope: SchoolScope, student: dict, run: dict) -> None:
    """School-side channel for a driver-marked absence: a student-stamped
    incident on the admin Alerts page. Never a parent fan-out — the incident
    names the child, and ParentLiveDao.list_alerts already excludes
    student-stamped rows, so notify_incident must not be called here."""
    try:
        incident_dao.create_driver_incident(
            scope,
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
    payload: AbsentPayload, background_tasks: BackgroundTasks,
    scope: SchoolScope = Depends(require_driver_scope),
    idempotency_key: str | None = IdempotencyKeyHeader,
):
    """Driver marks a roster student absent at the stop (U8/R17). The DAO writes
    the absence row scoped to this run's period, the run_absences snapshot, the
    'absent' status and the boarded recount in one transaction; the parent push
    and the admin-only incident fire post-commit.

    whole_day is the driver saying they know the child is out all day — which
    they can only know from something a parent told them, so it is a separate
    confirmation rather than the default.
    """
    outcome, replayed = _tap(
        lambda: dao.mark_student_absent(
            scope, payload.student_id, whole_day=payload.whole_day,
            event_id=payload.event_id, envelope=_envelope(idempotency_key, payload),
        )
    )
    if replayed:
        return outcome
    student, run = outcome
    background_tasks.add_task(
        push_service.notify_student_absent, student, run, scope=scope
    )
    # The parent push dedups on the notifications unique index; the incident
    # has no such index, so only a NEWLY recorded absence raises one.
    if run.get("newly_recorded"):
        background_tasks.add_task(_record_absent_incident, scope, student, run)
    return student
