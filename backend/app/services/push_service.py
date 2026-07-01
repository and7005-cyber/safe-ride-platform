"""Typed parent notifications: feed rows always, FCM / Web Push best-effort.

Every notification is recorded in live_notifications (the in-app feed parents
see even without push permission). When Firebase credentials are configured,
the same notification goes out through FCM to registered device tokens; when
VAPID keys are configured, raw Web Push subscriptions get it too. With neither
configured (local dev default) delivery is simulated via a log line.

Delivery runs on a small dedicated thread pool so request worker threads are
never tied up by push-service HTTP calls, and every outbound call carries a
timeout.

Notification types:
  run-started      morning run began — get the child ready for pickup
  student-boarded  driver marked the child as on the bus
  bus-approaching  bus arrived at the stop just before the child's stop
  reached-school   bus arrived at the school gate (morning, boarded students)
  on-way-home      afternoon run began — child is heading home
  dropped-off      driver confirmed the drop-off at the child's stop (tap-time)
  student-absent   driver marked the child absent at pickup (that child's parents only)
  incident         driver reported an issue on the child's bus

Rows persist the run's period as run_type ('morning'/'afternoon') so the
parent feed can filter by period even after the run itself is deleted
(run_id is ON DELETE SET NULL).
"""

import ipaddress
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlparse

from app.core.config import get_settings
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

# Shared across service instances: delivery must not monopolize the request
# thread pool, and a slow push provider must not back up driver requests.
_send_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="saferide-push")


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters."""
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


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

    def notify_run_started(self, run: dict) -> None:
        """Morning: 'bus on the way'. Afternoon: 'child on the way home'."""
        try:
            bus = self._bus_label(run.get("bus_id"))
            students = self.dao.students_on_run(str(run["id"]))
            for link in self.dao.parents_of_students([s["id"] for s in students]):
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
                    )
        except Exception:
            logger.exception("notify_run_started failed")

    def notify_student_boarded(self, run: dict, student_id: str) -> None:
        try:
            bus = self._bus_label(run.get("bus_id"))
            for link in self.dao.parents_of_students([student_id]):
                self._notify(
                    link["parent_id"],
                    type="student-boarded",
                    title="Boarded the bus",
                    body=f"{link['student_name']} has boarded {bus}.",
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                )
        except Exception:
            logger.exception("notify_student_boarded failed")

    def notify_student_dropped_off(self, student: dict, run: dict) -> None:
        """Driver confirmed the drop-off at the child's stop (afternoon,
        tap-time) — tell that child's linked parents. Run-scoped
        (run_id + student_id set) so a retried tap is dedup-suppressed."""
        try:
            student_id = str(student["id"])
            for link in self.dao.parents_of_students([student_id]):
                self._notify(
                    link["parent_id"],
                    type="dropped-off",
                    title="Dropped off",
                    body=f"{link['student_name']} has arrived at their stop and left the bus.",
                    student_id=student_id,
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                )
        except Exception:
            logger.exception("notify_student_dropped_off failed")

    def notify_student_absent(self, student: dict, run: dict, reason: str | None = None) -> None:
        """Driver marked the child absent at pickup — tell that child's linked
        parents and nobody else. Run-scoped (run_id + student_id set) so a
        repeat mark within the same run is dedup-suppressed. The school-side
        channel is a student-stamped incident inserted by the caller, never a
        parent fan-out."""
        try:
            student_id = str(student["id"])
            for link in self.dao.parents_of_students([student_id]):
                body = f"{link['student_name']} was marked absent at pickup and will not board the bus today."
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
                )
        except Exception:
            logger.exception("notify_student_absent failed")

    def notify_reached_school(self, run: dict) -> None:
        """Morning arrival at the school gate (or morning run end).

        Only parents of students actually on the bus are told their child
        reached school — a child who missed the bus must never generate a
        false safety assertion.
        """
        try:
            if run.get("type") != "morning":
                return
            for link in self._boarded_links(run):
                self._notify(
                    link["parent_id"],
                    type="reached-school",
                    title="Arrived at school",
                    body=f"{link['student_name']} has reached school safely.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                )
        except Exception:
            logger.exception("notify_reached_school failed")

    def notify_run_ended(self, run: dict) -> None:
        """Morning: reached-school for boarded students. Afternoon: nothing —
        confirmed drop-offs were notified at tap time by
        notify_student_dropped_off, and students the driver never confirmed
        must not get a false 'dropped off' assertion when the end-run sweep
        normalizes their status."""
        try:
            if run.get("type") == "morning":
                self.notify_reached_school(run)
        except Exception:
            logger.exception("notify_run_ended failed")

    def notify_incident(self, incident: dict) -> None:
        try:
            if incident.get("type") == "arrival" or not incident.get("bus_id"):
                return
            title = INCIDENT_TITLES.get(incident.get("type", ""), "Notice from the bus")
            body = incident.get("description") or f"Reported on {incident.get('bus_name') or 'the bus'}."
            # One notification per parent, however many children they have on
            # the bus — the message never references a specific child.
            notified: set[str] = set()
            for link in self.dao.parents_of_bus(str(incident["bus_id"])):
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
                )
        except Exception:
            logger.exception("notify_incident failed")

    def notify_bus_approaching(self, run: dict) -> None:
        """Stop-based 'bus-approaching': the instant the driver arrives at a
        stop, alert the parents whose child's stop is the *next* one. No GPS —
        the run's stops_completed (already advanced by arrive_next_stop) tells
        us which stop is coming up. Run-scoped dedup means a parent is alerted
        at most once per run."""
        try:
            next_order = (run.get("stops_completed") or 0) + 1
            bus = self._bus_label(run.get("bus_id"))
            students = [
                s for s in self.dao.students_at_stop(str(run["id"]), next_order)
                if s["student_status"] != "absent"
            ]
            for link in self.dao.parents_of_students([s["student_id"] for s in students]):
                self._notify(
                    link["parent_id"],
                    type="bus-approaching",
                    title="Bus approaching",
                    body=f"{bus} is approaching {link['student_name']}'s stop — it's the next stop.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                )
        except Exception:
            logger.exception("notify_bus_approaching failed")

    def notify_bus_position(self, run: dict, lat: float, lng: float) -> None:
        """Deprecated GPS-proximity variant (no longer wired — kept for API
        back-compat). Bus-approaching now fires from notify_bus_approaching."""
        try:
            radius = get_settings().bus_approaching_radius_m
            bus = self._bus_label(run.get("bus_id"))
            stops = self.dao.remaining_student_stops(str(run["id"]), run["stops_completed"])
            near = [
                s for s in stops
                if s["student_status"] != "absent"
                and haversine_m(lat, lng, float(s["lat"]), float(s["lng"])) <= radius
            ]
            for link in self.dao.parents_of_students([s["student_id"] for s in near]):
                self._notify(
                    link["parent_id"],
                    type="bus-approaching",
                    title="Bus approaching",
                    body=f"{bus} is approaching {link['student_name']}'s stop.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                    run_type=run.get("type"),
                )
        except Exception:
            logger.exception("notify_bus_position failed")

    # Internals ----------------------------------------------------------------

    def _boarded_links(self, run: dict) -> list[dict]:
        """Parent links for students who actually boarded this run.

        end_run snapshots the pre-sweep on-bus roster into
        run["boarded_student_ids"]; gate arrivals read live statuses.
        """
        boarded_ids = run.get("boarded_student_ids")
        if boarded_ids is None:
            students = self.dao.students_on_run(str(run["id"]))
            boarded_ids = [s["id"] for s in students if s.get("status") == "on-bus"]
        return self.dao.parents_of_students(list(boarded_ids))

    def _bus_label(self, bus_id: str | None) -> str:
        if not bus_id:
            return "The school bus"
        return self.dao.bus_name(str(bus_id)) or "The school bus"

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
        )
        if row is None:
            return  # run-scoped dedup suppressed a repeat
        self.send_to_user(str(parent_id), title, body, type)

    def send_to_user(self, user_id: str, title: str, body: str, type: str) -> None:
        """Queue best-effort delivery through every configured channel."""
        settings = get_settings()
        fcm_enabled = bool(settings.firebase_service_account_json.strip()) and not self._firebase_failed
        webpush_enabled = bool(settings.vapid_private_key and settings.vapid_public_key)
        if not fcm_enabled and not webpush_enabled:
            logger.info("push (simulated) -> user=%s type=%s title=%r", user_id, type, title)
            return
        _send_executor.submit(self._deliver, user_id, title, body, type)

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
