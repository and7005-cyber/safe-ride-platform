"""Typed parent notifications: feed rows always, FCM / Web Push best-effort.

Every notification is recorded in live_notifications (the in-app feed parents
see even without push permission). When Firebase credentials are configured,
the same notification goes out through FCM to registered device tokens; when
VAPID keys are configured, raw Web Push subscriptions get it too. With neither
configured (local dev default) delivery is simulated via a log line.

Delivery is dispatched from routers via FastAPI BackgroundTasks and runs
synchronously within that task, so it completes inside the request lifecycle.
On Lambda this is required: a fire-and-forget background thread is frozen the
instant the handler returns, so its send never happens — the feed row lands
but the phone never rings. Every outbound call carries a timeout, and the
feed row is always written first, so a slow or failing provider only delays
the push, never the notification itself.

Notification types:
  run-started      morning run began — get the child ready for pickup
  student-boarded  driver marked the child as on the bus
  bus-approaching  bus arrived at the stop just before the child's stop
  reached-school   bus arrived at the school gate (morning, boarded students)
  on-way-home      afternoon run began — child is heading home
  dropped-off      driver confirmed the drop-off at the child's stop (tap-time)
  student-absent   driver marked the child absent at pickup (that child's parents only)
  incident         driver reported an issue on the child's bus
  ride-cancelled   a parent cancelled the child's ride (that child's linked parents only)
  admin-notice     office broadcast to a route (one copy per parent with a child assigned)
  route-updated    a fleet-plan apply (U6) or a manual live-route edit (U13)
                   changed the child's stop/time/bus
  route-unassigned a fleet-plan apply (U6) or a manual live-route edit (U13)
                   left the child without a route for a leg
  boarding-corrected the driver withdrew a boarding mark (GPS plan U9); retracts
                   student-boarded and claims nothing about where the child is
  absence-corrected the driver withdrew an absent mark (GPS plan U10, reworded
                   neutrally); retracts student-absent only, never absent-call-now
  absent-call-now  the child was marked absent away from the stop and nothing
                   corroborated it (GPS plan U10/R18): call the office now if the
                   child should be on the bus. Additional to student-absent, its
                   own type, at most once per child per run (capped on the
                   exception ledger, not only here), never retracted

Rows persist the run's period as run_type ('morning'/'afternoon') so the
parent feed can filter by period even after the run itself is deleted
(run_id is ON DELETE SET NULL).
"""

import ipaddress
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
from app.core.db import UNSET, get_connection
from app.dao.push_dao import PushDao

logger = logging.getLogger("saferide.push")

NOTIFICATIONS_URL = "/parent/alerts"
SEND_TIMEOUT_SECONDS = 10
WEB_PUSH_TTL_SECONDS = 3600

INCIDENT_TITLES = {
    "breakdown": "Vehicle breakdown",
    "accident": "Road accident",
    "student": "Student issue",
    "traffic": "Traffic delay",
    "other": "Notice from the bus",
}

# Web-push services reject payloads past ~4KB; the broadcast body is capped by
# the endpoint (500 chars) and the composed title is bounded here — a very long
# route name must not push the payload over the edge.
BROADCAST_TITLE_MAX_CHARS = 120


def is_safe_push_endpoint(url: str) -> bool:
    """Web push endpoints must be public HTTPS origins (SSRF guard)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True  # hostname, not an IP literal
    return False if address.is_private or address.is_loopback or address.is_link_local else address.is_global


class PushService:
    def __init__(self, dao: PushDao | None = None) -> None:
        self.dao = dao or PushDao()
        self._firebase_app: Any = None
        self._firebase_failed = False

    # Event entry points (called from routers via BackgroundTasks) ------------
    # Every entry point takes the dispatching request's scope explicitly (U7)
    # and threads it into each DAO read/write, so the background task never
    # leans on the context-var fallback the strict seam now refuses for
    # school-scoped requests. Feed rows stamp the run/route/plan school where
    # one is derivable.

    def notify_run_started(self, run: dict, scope: object = UNSET) -> None:
        """Morning: 'bus on the way'. Afternoon: 'child on the way home'."""
        try:
            bus = self._bus_label(run.get("bus_id"), scope)
            students = self.dao.students_on_run(str(run["id"]), scope=scope)
            for link in self.dao.parents_of_students(
                [s["id"] for s in students], scope=scope
            ):
                if run.get("type") == "afternoon":
                    self._notify(
                        link["parent_id"],
                        type="on-way-home",
                        title="On the way home",
                        body=f"{link['student_name']}'s bus {bus} has started the trip home.",
                        student_id=link["student_id"],
                        run_id=str(run["id"]),
                        bus_id=run.get("bus_id"),
                        run_type=run.get("type"),
                        school_id=run.get("school_id"),
                        scope=scope,
                    )
                else:
                    self._notify(
                        link["parent_id"],
                        type="run-started",
                        title="Bus on the way",
                        body=f"{bus} has started the morning pickup run. Get {link['student_name']} ready.",
                        student_id=link["student_id"],
                        run_id=str(run["id"]),
                        bus_id=run.get("bus_id"),
                        run_type=run.get("type"),
                        school_id=run.get("school_id"),
                        scope=scope,
                    )
        except Exception:
            logger.exception("notify_run_started failed")

    def notify_student_boarded(self, run: dict, student_id: str, scope: object = UNSET) -> None:
        try:
            bus = self._bus_label(run.get("bus_id"), scope)
            for link in self.dao.parents_of_students([student_id], scope=scope):
                self._notify(
                    link["parent_id"],
                    type="student-boarded",
                    title="Boarded the bus",
                    body=f"{link['student_name']} has boarded {bus}.",
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_student_boarded failed")

    def notify_student_dropped_off(self, student: dict, run: dict, scope: object = UNSET) -> None:
        """Driver confirmed the drop-off at the child's stop (afternoon,
        tap-time) — tell that child's linked parents. Run-scoped
        (run_id + student_id set) so a retried tap is dedup-suppressed."""
        try:
            student_id = str(student["id"])
            for link in self.dao.parents_of_students([student_id], scope=scope):
                self._notify(
                    link["parent_id"],
                    type="dropped-off",
                    title="Dropped off",
                    body=f"{link['student_name']} has arrived at their stop and left the bus.",
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_student_dropped_off failed")

    def notify_student_handover(
        self, student: dict, run: dict, note: str, scope: object = UNSET
    ) -> None:
        """Driver handed the child over away from their stop (U4/R12).

        Reuses the 'dropped-off' type on purpose: from the family's side this is
        the same event — the child left the bus — and the type carries the
        dedup key and the parent-feed label already. What changes is the body,
        which says where, because the route's own stop would be the wrong answer.
        """
        try:
            student_id = str(student["id"])
            for link in self.dao.parents_of_students([student_id], scope=scope):
                self._notify(
                    link["parent_id"],
                    type="dropped-off",
                    title="Left the bus",
                    body=(
                        f"{link['student_name']} left the bus away from their usual stop. "
                        f"Driver's note: {note}"
                    ),
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_student_handover failed")

    def notify_correction(
        self, student: dict, run: dict, reversed_what: str, scope: object = UNSET
    ) -> None:
        """The driver corrected their own mis-tap (U5/R10).

        Its own notification type, because the dedup index keys on
        (user, run, student, type): reusing 'dropped-off' or 'student-absent'
        would be suppressed as a duplicate of the very message being corrected,
        and the family would keep the false one.
        """
        try:
            student_id = str(student["id"])
            # Retract the message being corrected first. The dedup index is
            # unique on (user, run, student, type), so leaving it would suppress
            # the driver's genuine second confirmation as a duplicate — the
            # family would keep the false message and never get the true one.
            superseded = {
                "absence": ["student-absent"],
                "boarding": ["student-boarded"],
            }.get(reversed_what, ["dropped-off"])
            self.dao.retract_notifications(str(run["id"]), student_id, superseded, scope=scope)

            if reversed_what == "absence":
                # Neutral by design (GPS plan U10/R35): the mark is withdrawn
                # and the driver will record what happens; the message claims
                # nothing about where the child is. It used to say "They are
                # on the bus" — a claim manufactured from a tap that may have
                # been made kilometres from the stop.
                type_ = "absence-corrected"
                title = "Correction: absent mark withdrawn"
                tail = (
                    "was marked absent by mistake; that mark has been withdrawn. "
                    "The driver will record what happens at the stop."
                )
            elif reversed_what == "boarding":
                # Neutral by design (GPS plan U9/R35): the mark is withdrawn
                # and the driver will record what happens; the message claims
                # nothing about where the child is.
                type_ = "boarding-corrected"
                title = "Correction: boarding withdrawn"
                tail = (
                    "was marked as boarded by mistake; that mark has been withdrawn. "
                    "The driver will record what happens at the stop."
                )
            else:
                type_ = "dropoff-corrected"
                title = "Correction: not dropped off"
                tail = (
                    "was marked as dropped off by mistake. They are still on the bus — "
                    "the driver will confirm when they get off."
                )
            for link in self.dao.parents_of_students([student_id], scope=scope):
                self._notify(
                    link["parent_id"],
                    type=type_,
                    title=title,
                    body=f"{link['student_name']} {tail}",
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_correction failed")

    def notify_student_absent(
        self, student: dict, run: dict, reason: str | None = None,
        scope: object = UNSET,
    ) -> None:
        """Driver marked the child absent at pickup — tell that child's linked
        parents and nobody else. Run-scoped (run_id + student_id set) so a
        repeat mark within the same run is dedup-suppressed. The school-side
        channel is a student-stamped incident inserted by the caller, never a
        parent fan-out.

        The body states the period covered and claims nothing beyond it
        (U8/R21). It used to say "will not board the bus today" off a single
        run — so a parent whose child missed the morning pickup was told they
        were not coming home either, which the driver had no way of knowing and
        which was often simply wrong.
        """
        try:
            student_id = str(student["id"])
            period = run.get("absence_period") or run.get("type") or "day"
            name_slot = "{name}"
            if period == "morning":
                template = (
                    f"{name_slot} was not at the stop for the morning pickup, so they are "
                    "not riding to school. The trip home is unaffected."
                )
            elif period == "afternoon":
                template = (
                    f"{name_slot} did not board the bus home this afternoon."
                )
            else:
                template = f"{name_slot} is marked absent for the whole day and will not travel."
            for link in self.dao.parents_of_students([student_id], scope=scope):
                body = template.format(name=link["student_name"])
                if reason:
                    body = f"{body} Reason: {reason}"
                self._notify(
                    link["parent_id"],
                    type="student-absent",
                    title="Marked absent",
                    body=body,
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_student_absent failed")

    def notify_absent_call_now(self, student: dict, run: dict, scope: object = UNSET) -> bool:
        """The loudest parent message (GPS plan U10/R18): the driver marked the
        child absent away from the stop and nothing corroborated it — a
        skipped stop is indistinguishable from a no-show until someone asks.
        Additional to ``student-absent``, its own type, that child's linked
        parents only, run-scoped so the dedup index makes a retried drain a
        no-op; the exception ledger caps it at once per child per run before
        it ever reaches here. Never retracted by an undo. The body names no
        stop and no distance: it asks for a phone call, nothing else.

        Unlike its siblings this reports its outcome: True when every linked
        parent's feed row landed (or was already there), False when the
        fan-out failed — the outbox drain stamps the event sent only on True,
        so a failed send stays due and is retried on the next action or poll.
        """
        try:
            student_id = str(student["id"])
            for link in self.dao.parents_of_students([student_id], scope=scope):
                name = link["student_name"]
                self._notify(
                    link["parent_id"],
                    type="absent-call-now",
                    title="Call the office now",
                    body=(
                        f"{name} was marked absent away from their stop. If {name} should "
                        "be on the bus, please call the school office now."
                    ),
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
            return True
        except Exception:
            logger.exception("notify_absent_call_now failed")
            return False

    def notify_ride_cancelled(self, student: dict, scope: str) -> None:
        """A parent cancelled the child's ride (U5) — confirm to EVERY linked
        parent of that child (both co-parents see it, whoever submitted) and
        nobody else. run_id stays NULL: cancellations precede any run, and
        the run-scoped dedup exemption means every emission is a real row —
        the caller only fires this on an actual set_scope transition.
        run_type maps from the recorded scope ('morning'/'afternoon';
        whole-day → NULL) so the feed's period filter surfaces the row under
        the period the parent cancelled.

        Failure isolation is per recipient: one co-parent's failing insert or
        send must not cost the other their confirmation."""
        try:
            student_id = str(student["id"])
            phrases = {
                "morning": "morning pickup today has been cancelled",
                "afternoon": "afternoon ride today has been cancelled",
                "day": "rides for the rest of today have been cancelled",
            }
            phrase = phrases.get(scope, "ride today has been cancelled")
            delivered = intended = 0
            # Parent-portal dispatch (no request scope until U11); the row
            # still stamps the child's school where the caller supplied it.
            for link in self.dao.parents_of_students([student_id]):
                intended += 1
                try:
                    self._notify(
                        link["parent_id"],
                        type="ride-cancelled",
                        title="Ride cancelled",
                        body=f"{link['student_name']}'s {phrase} by a parent on the account.",
                        student_id=student_id,
                        run_id=None,
                        bus_id=student.get("bus_id"),
                        run_type=scope if scope in ("morning", "afternoon") else None,
                        school_id=student.get("school_id"),
                    )
                    delivered += 1
                except Exception:
                    logger.exception(
                        "ride-cancelled confirmation failed for parent %s", link["parent_id"]
                    )
            if delivered < intended:
                logger.warning(
                    "ride-cancelled fan-out for student %s reached %d of %d linked parents",
                    student_id, delivered, intended,
                )
        except Exception:
            logger.exception("notify_ride_cancelled failed")

    def notify_reached_school(self, run: dict, scope: object = UNSET) -> None:
        """Morning arrival at the school gate (or morning run end).

        Only parents of students actually on the bus are told their child
        reached school — a child who missed the bus must never generate a
        false safety assertion.
        """
        try:
            if run.get("type") != "morning":
                return
            for link in self._boarded_links(run, scope):
                self._notify(
                    link["parent_id"],
                    type="reached-school",
                    title="Arrived at school",
                    body=f"{link['student_name']} has reached school safely.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_reached_school failed")

    def notify_run_ended(self, run: dict, scope: object = UNSET) -> None:
        """Morning: reached-school for boarded students. Afternoon: nothing —
        confirmed drop-offs were notified at tap time by
        notify_student_dropped_off, and students the driver never confirmed
        must not get a false 'dropped off' assertion when the end-run sweep
        normalizes their status."""
        try:
            if run.get("type") == "morning":
                self.notify_reached_school(run, scope)
        except Exception:
            logger.exception("notify_run_ended failed")

    def notify_incident(self, incident: dict, scope: object = UNSET) -> None:
        try:
            # Student-stamped incidents are child-specific (driver-absent
            # reports, parent cancellations): admin Alerts page only. A
            # bus-wide fan-out would tell every family on the bus about a
            # named child. Callers already keep these DAO-direct; this
            # early-return holds the line if one ever slips through —
            # type-agnostic on purpose (defense in depth, U5).
            if incident.get("student_id"):
                return
            if incident.get("type") == "arrival" or not incident.get("bus_id"):
                return
            # Run-lifecycle rows are the office's operational feed (U16). Same
            # defense-in-depth as the student_id guard: callers keep these
            # DAO-direct, and this holds the line if one ever slips through.
            if incident.get("lifecycle"):
                return
            title = INCIDENT_TITLES.get(incident.get("type", ""), "Notice from the bus")
            body = incident.get("description") or f"Reported on {incident.get('bus_name') or 'the bus'}."
            # One notification per parent, however many children they have on
            # the bus — the message never references a specific child.
            notified: set[str] = set()
            for link in self.dao.parents_of_bus(str(incident["bus_id"]), scope=scope):
                parent_id = str(link["parent_id"])
                if parent_id in notified:
                    continue
                notified.add(parent_id)
                self._notify(
                    parent_id,
                    type="incident",
                    title=title,
                    body=body,
                    student_id=link["student_id"],
                    run_id=None,  # incidents are not deduped: each report matters
                    bus_id=incident.get("bus_id"),
                    run_type=incident.get("run_type"),
                    school_id=incident.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_incident failed")

    def notify_admin_broadcast(
        self, route: dict, body: str, parent_ids: list[str], scope: object = UNSET
    ) -> None:
        """Admin route broadcast (U8: R20, R21, R23; AE5): one 'admin-notice'
        feed row + push per DISTINCT parent with a child assigned to the route.

        The recipient set is resolved by the endpoint (synchronously, from
        assignments — never bus_id) and passed in: this runs in
        BackgroundTasks, and the response's recipient count must be what was
        actually fanned out, not a hope. The set is deduped again here
        (notify_incident's per-parent pattern) so a duplicated id can never
        double-send. run_id stays NULL — every send is a real row (R23 bans
        run-scoped dedup: two identical sends are two rows) — and run_type
        stays NULL (R22: the notice is period-agnostic). body arrives
        validated and stored as-is; the title is length-bounded because
        web-push services reject ~4KB payloads.

        Failure isolation is per recipient: one failing insert or send must
        not cut off the rest of the route's parents."""
        try:
            title = f"School notice — {route.get('name') or 'your route'}"
            if len(title) > BROADCAST_TITLE_MAX_CHARS:
                title = title[: BROADCAST_TITLE_MAX_CHARS - 1] + "…"
            notified: set[str] = set()
            delivered = 0
            for parent_id in parent_ids:
                parent_id = str(parent_id)
                if parent_id in notified:
                    continue
                notified.add(parent_id)
                try:
                    self._notify(
                        parent_id,
                        type="admin-notice",
                        title=title,
                        body=body,
                        student_id=None,
                        run_id=None,
                        bus_id=route.get("bus_id"),
                        run_type=None,
                        school_id=route.get("school_id"),
                        scope=scope,
                    )
                    delivered += 1
                except Exception:
                    logger.exception("admin broadcast failed for parent %s", parent_id)
            if delivered < len(notified):
                logger.warning(
                    "admin broadcast for route %s reached %d of %d recipients",
                    route.get("id"), delivered, len(notified),
                )
        except Exception:
            logger.exception("notify_admin_broadcast failed")

    def notify_bus_approaching(self, run: dict, scope: object = UNSET) -> None:
        """Stop-based 'bus-approaching': the instant the driver arrives at a
        stop, alert the parents whose child's stop is the *next* one. No GPS —
        the run's stops_completed (already advanced by arrive_next_stop) tells
        us which stop is coming up. Run-scoped dedup means a parent is alerted
        at most once per run."""
        try:
            next_order = (run.get("stops_completed") or 0) + 1
            bus = self._bus_label(run.get("bus_id"), scope)
            students = [
                s for s in self.dao.students_at_stop(str(run["id"]), next_order, scope=scope)
                if s["student_status"] not in ("absent", "unaccounted")
            ]
            for link in self.dao.parents_of_students(
                [s["student_id"] for s in students], scope=scope
            ):
                self._notify(
                    link["parent_id"],
                    type="bus-approaching",
                    title="Bus approaching",
                    body=f"{bus} is approaching {link['student_name']}'s stop — it's the next stop.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                    school_id=run.get("school_id"),
                    scope=scope,
                )
        except Exception:
            logger.exception("notify_bus_approaching failed")

    def deliver_plan_feed_rows(self, rows: list[dict]) -> dict:
        """Awaited push delivery for feed rows the fleet-plan apply transaction
        ALREADY wrote (U6) — delivery only, never insertion: the feed rows are
        the product truth and committed with the apply, so re-inserting here
        would double them (and the notified-family count counts feed rows,
        not deliveries). Runs synchronously inside the request — awaited,
        never a detached thread (the Lambda freeze rule) — with per-recipient
        isolation: one family's failing send must not cost the rest theirs.
        One summary line per burst on this logger (saferide.push).

        Each row needs ``user_id``/``title``/``body``/``type``. Returns
        ``{sent, failed, simulated, elapsed_ms}``; ``simulated`` is True when
        no push channel is configured (local dev default) and every delivery
        was a log line."""
        t0 = time.monotonic()
        settings = get_settings()
        fcm_enabled = bool(settings.firebase_service_account_json.strip()) and not self._firebase_failed
        webpush_enabled = bool(settings.vapid_private_key and settings.vapid_public_key)
        simulated = not fcm_enabled and not webpush_enabled
        sent = failed = 0
        for row in rows:
            try:
                self.send_to_user(str(row["user_id"]), row["title"], row["body"], row["type"])
                sent += 1
            except Exception:
                failed += 1
                logger.exception("plan-apply push failed for user %s", row.get("user_id"))
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "plan-apply push fan-out: sent=%d failed=%d simulated=%s elapsed_ms=%d",
            sent, failed, simulated, elapsed_ms,
        )
        return {"sent": sent, "failed": failed, "simulated": simulated, "elapsed_ms": elapsed_ms}

    # Internals ----------------------------------------------------------------

    def _boarded_links(self, run: dict, scope: object = UNSET) -> list[dict]:
        """Parent links for children the driver actually observed boarding.

        end_run supplies run["boarded_student_ids"] from participation —
        confirmed boardings only. A presumed afternoon board is not evidence a
        child rode, and asserting arrival for one would be a false safety claim.

        The fallback covers callers with no snapshot (a gate arrival mid-run)
        and reads the derived status, not the raw column: after U2 the column
        no longer tracks who boarded this run.
        """
        boarded_ids = run.get("boarded_student_ids")
        if boarded_ids is None:
            students = self.dao.students_on_run(str(run["id"]), scope=scope)
            boarded_ids = [s["id"] for s in students if s.get("display_status") == "on-bus"]
        return self.dao.parents_of_students(list(boarded_ids), scope=scope)

    def _bus_label(self, bus_id: str | None, scope: object = UNSET) -> str:
        if not bus_id:
            return "The school bus"
        return self.dao.bus_name(str(bus_id), scope=scope) or "The school bus"

    def _notify(
        self,
        parent_id: str,
        *,
        type: str,
        title: str,
        body: str,
        student_id: str | None,
        run_id: str | None,
        bus_id: str | None,
        run_type: str | None = None,
        school_id: str | None = None,
        scope: object = UNSET,
    ) -> None:
        row = self.dao.insert_notification(
            str(parent_id),
            type,
            title,
            body,
            student_id=str(student_id) if student_id else None,
            run_id=run_id,
            bus_id=str(bus_id) if bus_id else None,
            run_type=run_type,
            school_id=str(school_id) if school_id else None,
            scope=scope,
        )
        if row is None:
            return  # run-scoped dedup suppressed a repeat
        self.send_to_user(str(parent_id), title, body, type)

    def send_to_user(self, user_id: str, title: str, body: str, type: str) -> None:
        """Best-effort delivery through every configured channel.

        Synchronous by design: this is invoked from a BackgroundTask that
        Mangum awaits within the Lambda invocation. Offloading to a background
        thread would let Lambda freeze the send before it runs. Each channel
        carries its own timeout, so a slow provider bounds the delay."""
        settings = get_settings()
        fcm_enabled = bool(settings.firebase_service_account_json.strip()) and not self._firebase_failed
        webpush_enabled = bool(settings.vapid_private_key and settings.vapid_public_key)
        if not fcm_enabled and not webpush_enabled:
            logger.info("push (simulated) -> user=%s type=%s title=%r", user_id, type, title)
            return
        self._deliver(user_id, title, body, type)

    def _deliver(self, user_id: str, title: str, body: str, type: str) -> None:
        try:
            delivered = self._send_fcm(user_id, title, body, type)
            delivered += self._send_web_push(user_id, title, body, type)
            if delivered == 0:
                logger.info("push (no devices) -> user=%s type=%s", user_id, type)
        except Exception:
            logger.exception("push delivery failed for user %s", user_id)

    # FCM ----------------------------------------------------------------------

    def _firebase(self) -> Any:
        if self._firebase_app is not None or self._firebase_failed:
            return self._firebase_app
        raw = get_settings().firebase_service_account_json.strip()
        if not raw:
            return None
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(json.loads(raw) if raw.startswith("{") else raw)
            try:
                self._firebase_app = firebase_admin.get_app("saferide")
            except ValueError:
                self._firebase_app = firebase_admin.initialize_app(
                    cred, {"httpTimeout": SEND_TIMEOUT_SECONDS}, name="saferide"
                )
        except Exception:
            # Bad credentials will not heal on retry: disable FCM for this
            # process and say so once instead of stack-tracing every send.
            logger.exception("Firebase initialization failed; FCM disabled for this process")
            self._firebase_failed = True
            self._firebase_app = None
        return self._firebase_app

    def _send_fcm(self, user_id: str, title: str, body: str, type: str) -> int:
        app = self._firebase()
        if app is None:
            return 0
        tokens = self.dao.fcm_tokens_for_users([user_id])
        if not tokens:
            return 0
        from firebase_admin import messaging

        sent = 0
        for row in tokens:
            message = messaging.Message(
                token=row["token"],
                webpush=messaging.WebpushConfig(
                    notification=messaging.WebpushNotification(
                        title=title, body=body, icon="/icons/icon-192.png"
                    ),
                    data={"url": NOTIFICATIONS_URL, "type": type},
                    fcm_options=messaging.WebpushFCMOptions(link=NOTIFICATIONS_URL),
                ),
            )
            try:
                messaging.send(message, app=app)
                sent += 1
            except messaging.UnregisteredError:
                self.dao.delete_fcm_token(row["token"])
            except Exception:
                logger.exception("FCM send failed for user %s", user_id)
        return sent

    # Raw Web Push ---------------------------------------------------------------

    def _send_web_push(self, user_id: str, title: str, body: str, type: str) -> int:
        settings = get_settings()
        if not settings.vapid_private_key or not settings.vapid_public_key:
            return 0
        subscriptions = self.dao.web_push_subscriptions_for_users([user_id])
        if not subscriptions:
            return 0
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            logger.warning("pywebpush not installed; web push disabled")
            return 0

        payload = json.dumps(
            {"title": title, "body": body, "url": NOTIFICATIONS_URL, "type": type}
        )
        sent = 0
        for sub in subscriptions:
            if not is_safe_push_endpoint(sub["endpoint"]):
                self.dao.delete_web_push_subscription(sub["endpoint"])
                continue
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload,
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject},
                    ttl=WEB_PUSH_TTL_SECONDS,
                    timeout=SEND_TIMEOUT_SECONDS,
                )
                sent += 1
            except WebPushException as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in (404, 410):
                    self.dao.delete_web_push_subscription(sub["endpoint"])
                else:
                    logger.warning("web push failed for user %s: %s", user_id, error)
            except Exception:
                logger.exception("web push failed for user %s", user_id)
        return sent


# --- manual live-route edit fan-out (fleet-plan U13) ---------------------------
# Origin R15 requires parent notification for ANY live-route change — "an
# applied plan or a manual edit". U6/U7 cover apply/restore through
# diff_plan_vs_live; this module-level helper closes the manual-edit half:
# after a mutating endpoint's transaction commits, it diffs the LIVE stop
# truth per (student, leg) against the same live_communicated_stops baseline
# the plan diff reads, with the same thresholds, and reuses the same feed-row
# insert (PushDao.insert_plan_notification), baseline writer
# (FleetPlanDao._write_baselines) and awaited delivery
# (PushService.deliver_plan_feed_rows).


def notify_route_changes(
    route_ids: list[str] | None = None,
    student_ids: list[str] | None = None,
    *,
    seed_only: bool = False,
    scope: object = UNSET,
) -> dict | None:
    """R15 fan-out for a manual live-route edit (U13).

    Dispatched via BackgroundTasks from the mutating endpoint (the broadcast
    endpoint's house pattern), so it runs AFTER the mutation's transaction
    committed, in its own short transaction: feed rows and baseline writes
    commit as one atom, then push delivery is awaited post-commit through
    ``deliver_plan_feed_rows`` (delivery only — the feed rows are already the
    product truth).

    Affected students = ``student_ids`` + members of ``route_ids`` + members
    of the given students' current routes (a pickup-time edit or a roster
    change reshuffles co-riders too). Callers removing students or deleting
    routes pass the pre-mutation ids, since the mutation severs the links this
    expansion would otherwise follow.

    Per affected (student, leg), against ``live_communicated_stops`` — the
    diff_plan_vs_live category semantics on live truth (same
    NOTIFY_MOVE_THRESHOLD_MIN, same coordinate epsilon, same NULL-baseline
    rule):

    * no baseline, stop present  -> first communication: notify and seed.
    * time moved >= 5 minutes    -> route-updated (baseline NULL/malformed
      time counts as moved when a real time is now told).
    * stop place changed         -> route-updated.
    * bus changed vs baseline    -> route-updated.
    * baseline for a leg with NO live stop -> route-unassigned, baseline
      deleted (a later re-assignment is a first communication again).

    Baselines move ONLY on send (the R15 anti-accumulation rule): silent
    sub-threshold drift keeps the communicated values.

    ``plan_audit_id`` stays NULL on every row: 011's plan-dedup partial
    unique keys on (user, student, type, plan_audit_id), and Postgres treats
    NULLs as distinct, so manual-edit rows never collide with apply rows or
    with each other — repeated manual edits legitimately re-notify. Repeat
    suppression is the BASELINE's job: an edit that moves nothing against the
    freshly written baseline produces no row at all.

    ``seed_only=True`` — the roster paths (student create/update/delete,
    bulk upload): missing baselines are seeded SILENTLY (no feed rows, no
    updates to existing baselines, no deletions). Deliberate U13 deviation:
    the roster interaction itself is the communication for a child the admin
    is editing by hand, and the shipped apply-suite contract
    (test_fleet_plan_apply's exact-feed assertions) pins roster mutations
    silent — drift they cause is notified by the NEXT edit or apply, measured
    against the baseline seeded here.

    ``scope`` is the dispatching request's scope (U7): the mutation ran
    inside a school-scoped request, and this post-commit task must open its
    connection through the same explicit scope — an unthreaded dispatch
    inside a SchoolScope request fails loudly under the strict seam (and is
    swallowed here per the best-effort contract, returning None with nothing
    written, never a silent partial success).

    Best-effort by design (background context): any failure is logged, never
    raised into the caller's response path.
    """
    # Lazy import — fleet_plan_dao imports PushService at module load, so a
    # top-level import here would be circular. The constants/helpers are the
    # SAME objects the plan diff uses: thresholds cannot drift.
    from app.dao.fleet_plan_dao import (
        DIFF_BUS_CHANGE,
        DIFF_FIRST_COMMUNICATION,
        DIFF_LEG_REMOVED,
        DIFF_PLACE_CHANGE,
        DIFF_TIME_MOVE,
        FleetPlanDao,
        NOTIFY_MOVE_THRESHOLD_MIN,
        _floats_differ,
        _hhmm_to_minutes,
    )

    try:
        push_dao = PushDao()
        feed_rows: list[dict] = []
        notified: list[dict] = []
        with get_connection(scope) as conn:
            # 1. Resolve the affected student set.
            affected = {str(s) for s in (student_ids or [])}
            rids = {str(r) for r in (route_ids or [])}
            if affected:
                rids |= {
                    str(r["route_id"])
                    for r in conn.execute(
                        "select distinct route_id from live_student_routes "
                        "where student_id = any(%s::uuid[])",
                        (sorted(affected),),
                    ).fetchall()
                }
            if rids:
                affected |= {
                    str(r["student_id"])
                    for r in conn.execute(
                        "select distinct student_id from live_student_routes "
                        "where route_id = any(%s::uuid[])",
                        (sorted(rids),),
                    ).fetchall()
                }
            if not affected:
                return None
            sids = sorted(affected)

            # 2. Current live truth per (student, leg): membership bus +
            # the student's own stop row (regeneration writes one row per
            # student; 009's unique means at most one route per leg).
            current: dict[tuple[str, str], dict] = {}
            for r in conn.execute(
                "select sr.student_id, sr.route_type, r.bus_id, "
                "b.name as bus_name, st.name as student_name, "
                "rs.id as stop_id, rs.name as stop_name, rs.lat, rs.lng, "
                "rs.scheduled_time "
                "from live_student_routes sr "
                "join live_routes r on r.id = sr.route_id "
                "join live_students st on st.id = sr.student_id "
                "left join live_buses b on b.id = r.bus_id "
                "left join live_route_stops rs "
                "on rs.route_id = sr.route_id and rs.student_id = sr.student_id "
                "where sr.student_id = any(%s::uuid[])",
                (sids,),
            ).fetchall():
                current[(str(r["student_id"]), r["route_type"])] = dict(r)

            baselines: dict[tuple[str, str], dict] = {}
            for b in conn.execute(
                "select c.student_id, c.route_type, c.stop_name, c.stop_lat, "
                "c.stop_lng, c.scheduled_time, c.bus_id, st.name as student_name "
                "from live_communicated_stops c "
                "join live_students st on st.id = c.student_id "
                "where c.student_id = any(%s::uuid[])",
                (sids,),
            ).fetchall():
                baselines[(str(b["student_id"]), b["route_type"])] = dict(b)

            # 3. Diff live vs baseline.
            for (sid, leg), cur in sorted(current.items()):
                if cur["stop_id"] is None:
                    # Membership without a stop row (a planner-saved custom
                    # route — its stops are not student-linked): neither a
                    # placement to compare nor evidence of removal. Leave any
                    # baseline alone.
                    continue
                base = baselines.get((sid, leg))
                if base is None:
                    categories = [DIFF_FIRST_COMMUNICATION]
                else:
                    if seed_only:
                        continue  # roster paths never touch existing baselines
                    categories = []
                    old_min = _hhmm_to_minutes(base["scheduled_time"])
                    new_min = _hhmm_to_minutes(cur["scheduled_time"])
                    # Plan-diff rule, adapted for live stops that may carry NO
                    # time (a never-computed route): a NULL/malformed baseline
                    # time counts as moved only when a REAL time is now told —
                    # None -> None must not re-notify on every later edit.
                    if new_min is not None and (
                        old_min is None
                        or abs(new_min - old_min) >= NOTIFY_MOVE_THRESHOLD_MIN
                    ):
                        categories.append(DIFF_TIME_MOVE)
                    # Place: coordinates first (the plan diff's epsilon).
                    # Names are compared ONLY when both sides are wholly
                    # coordinate-less (address-keyed stops): apply seeds
                    # baseline names from the plan document's sibling-joined
                    # stop names while live rows carry address labels, so a
                    # name comparison alongside coordinates would
                    # false-positive every family on the first post-apply
                    # manual edit.
                    if _floats_differ(base["stop_lat"], cur["lat"]) or _floats_differ(
                        base["stop_lng"], cur["lng"]
                    ):
                        categories.append(DIFF_PLACE_CHANGE)
                    elif (
                        base["stop_lat"] is None
                        and base["stop_lng"] is None
                        and cur["lat"] is None
                        and cur["lng"] is None
                        and (base["stop_name"] or "") != (cur["stop_name"] or "")
                    ):
                        categories.append(DIFF_PLACE_CHANGE)
                    # Bus: baseline vs current membership bus — both known,
                    # mirroring the plan diff's known-live-bus guard.
                    if (
                        base["bus_id"] is not None
                        and cur["bus_id"] is not None
                        and str(base["bus_id"]) != str(cur["bus_id"])
                    ):
                        categories.append(DIFF_BUS_CHANGE)
                    if not categories:
                        continue
                notified.append({
                    "student_id": sid,
                    "student_name": cur["student_name"],
                    "leg": leg,
                    "categories": categories,
                    "current": {
                        "stop_name": cur["stop_name"],
                        "lat": cur["lat"],
                        "lng": cur["lng"],
                        "scheduled_time": cur["scheduled_time"],
                        "bus_id": str(cur["bus_id"]) if cur["bus_id"] is not None else None,
                        "bus_name": cur["bus_name"],
                    },
                })

            # Baselines for legs with no live stop left: the child was
            # unassigned — route-unassigned, baseline deleted (never in
            # seed_only mode: roster paths only ever ADD coverage).
            if not seed_only:
                for (sid, leg), base in sorted(baselines.items()):
                    if (sid, leg) in current:
                        continue
                    notified.append({
                        "student_id": sid,
                        "student_name": base["student_name"],
                        "leg": leg,
                        "categories": [DIFF_LEG_REMOVED],
                        "current": None,
                    })

            # 4. Feed rows on THIS connection (rollback takes them too), then
            # baseline maintenance through the SHARED writer: upsert placed,
            # delete removed — only for notified rows, so the baseline moves
            # exactly when a notification is sent. Seed-only rows count as
            # sent: their communication is the roster interaction itself.
            if notified and not seed_only:
                # Recipient rule (U7): accepted links on enabled accounts only.
                families: dict[str, list[str]] = {}
                for pr in conn.execute(
                    "select ps.parent_id, ps.student_id from live_parent_students ps "
                    "join app_users u on u.id = ps.parent_id "
                    "where ps.student_id = any(%s::uuid[]) "
                    "and ps.status = 'accepted' and u.disabled_at is null",
                    (sorted({r["student_id"] for r in notified}),),
                ).fetchall():
                    families.setdefault(str(pr["student_id"]), []).append(
                        str(pr["parent_id"])
                    )
                feed_rows = _write_manual_edit_feed_rows(
                    conn, push_dao, notified, families,
                    school_id=getattr(scope, "school_id", None),
                )
            if notified:
                FleetPlanDao._write_baselines(conn, notified)

        # 5. Post-commit: awaited push delivery + the one-line fan-out summary
        # (deliver_plan_feed_rows logs its own line on this logger).
        push_summary = None
        if feed_rows:
            push_summary = PushService(push_dao).deliver_plan_feed_rows(feed_rows)
        logger.info(
            "manual-edit fan-out: students=%d notified_rows=%d feed_rows=%d seed_only=%s",
            len(sids), len(notified), len(feed_rows), seed_only,
        )
        return {"rows": len(notified), "feed_rows": len(feed_rows), "push": push_summary}
    except Exception:
        logger.exception("notify_route_changes failed")
        return None


def _write_manual_edit_feed_rows(
    conn, push_dao: PushDao, rows: list[dict], families: dict[str, list[str]],
    school_id: str | None = None,
) -> list[dict]:
    """Feed rows for one manual-edit act, inserted on the CALLER's transaction
    connection — the U6 composer's grouping (one row per (family, student,
    type) per act: a both-legs change reads as one message) with live-world
    fallbacks for values a live stop may lack (a never-computed route has no
    scheduled_time yet).

    ``plan_audit_id`` is NULL — see notify_route_changes: the 011 partial
    unique treats NULL as distinct, so these rows never dedup against apply
    rows or each other; repeats are suppressed by the baseline, not the index.
    """
    from app.dao.fleet_plan_dao import _leg_label

    feed_rows: list[dict] = []
    by_student: dict[str, dict] = {}
    for row in rows:
        g = by_student.setdefault(
            row["student_id"],
            {"name": row["student_name"], "placed": [], "unplaced": []},
        )
        (g["placed"] if row["current"] is not None else g["unplaced"]).append(row)
    for sid in sorted(by_student):
        g = by_student[sid]
        family_ids = sorted(families.get(sid, []))
        if not family_ids:
            continue  # no linked account: baseline still maintained by caller
        if g["placed"]:
            parts = [
                f"{_leg_label(r['leg'])}: "
                f"{r['current']['stop_name'] or 'the assigned stop'} at "
                f"{r['current']['scheduled_time'] or 'a time to be confirmed'} on "
                f"{r['current']['bus_name'] or 'the school bus'}"
                for r in g["placed"]
            ]
            legs = {r["leg"] for r in g["placed"]}
            for pid in family_ids:
                inserted = push_dao.insert_plan_notification(
                    conn, pid,
                    type="route-updated", title="Route updated",
                    body=f"{g['name']} — " + "; ".join(parts) + ".",
                    student_id=sid,
                    bus_id=g["placed"][0]["current"]["bus_id"],
                    run_type=next(iter(legs)) if len(legs) == 1 else None,
                    plan_audit_id=None,
                    school_id=school_id,
                )
                if inserted:
                    feed_rows.append(inserted)
        if g["unplaced"]:
            parts = [
                f"the {_leg_label(r['leg']).lower()} was removed from the route"
                for r in g["unplaced"]
            ]
            legs = {r["leg"] for r in g["unplaced"]}
            for pid in family_ids:
                inserted = push_dao.insert_plan_notification(
                    conn, pid,
                    type="route-unassigned", title="Route change",
                    body=f"{g['name']} — " + "; ".join(parts) + ".",
                    student_id=sid, bus_id=None,
                    run_type=next(iter(legs)) if len(legs) == 1 else None,
                    plan_audit_id=None,
                    school_id=school_id,
                )
                if inserted:
                    feed_rows.append(inserted)
    return feed_rows
