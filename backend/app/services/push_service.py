"""Typed parent notifications: feed rows always, FCM / Web Push best-effort.

Every notification is recorded in live_notifications (the in-app feed parents
see even without push permission). When Firebase credentials are configured,
the same notification goes out through FCM to registered device tokens; when
VAPID keys are configured, raw Web Push subscriptions get it too. With neither
configured (local dev default) delivery is simulated via a log line.

Notification types:
  run-started      morning run began — get the child ready for pickup
  student-boarded  driver marked the child as on the bus
  bus-approaching  bus GPS is within BUS_APPROACHING_RADIUS_M of the child's stop
  reached-school   bus arrived at the school gate (morning)
  on-way-home      afternoon run began — child is heading home
  dropped-off      afternoon run ended — child dropped at their stop
  incident         driver reported an issue on the child's bus
"""

import json
import logging
import math
from typing import Any

from app.core.config import get_settings
from app.dao.push_dao import PushDao

logger = logging.getLogger("saferide.push")

NOTIFICATIONS_URL = "/parent/alerts"

INCIDENT_TITLES = {
    "breakdown": "Vehicle breakdown",
    "accident": "Road accident",
    "student": "Student issue",
    "traffic": "Traffic delay",
    "other": "Notice from the bus",
}


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters."""
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


class PushService:
    def __init__(self, dao: PushDao | None = None) -> None:
        self.dao = dao or PushDao()
        self._firebase_app: Any = None

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
                    )
        except Exception:
            logger.exception("notify_run_started failed")

    def notify_student_boarded(self, driver_id: str, student_id: str) -> None:
        try:
            run = self.dao_active_run(driver_id)
            if not run:
                return
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
                )
        except Exception:
            logger.exception("notify_student_boarded failed")

    def notify_reached_school(self, run: dict) -> None:
        """Morning arrival at the school gate (or morning run end)."""
        try:
            if run.get("type") != "morning":
                return
            students = self.dao.students_on_run(str(run["id"]))
            for link in self.dao.parents_of_students([s["id"] for s in students]):
                self._notify(
                    link["parent_id"],
                    type="reached-school",
                    title="Arrived at school",
                    body=f"{link['student_name']} has reached school safely.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                )
        except Exception:
            logger.exception("notify_reached_school failed")

    def notify_run_ended(self, run: dict) -> None:
        """Afternoon: every non-absent student on the roster was dropped off."""
        try:
            if run.get("type") == "morning":
                self.notify_reached_school(run)
                return
            students = self.dao.students_on_run(str(run["id"]))
            for link in self.dao.parents_of_students([s["id"] for s in students]):
                self._notify(
                    link["parent_id"],
                    type="dropped-off",
                    title="Dropped off",
                    body=f"{link['student_name']} has been dropped off at their stop.",
                    student_id=link["student_id"],
                    run_id=str(run["id"]),
                    bus_id=run.get("bus_id"),
                )
        except Exception:
            logger.exception("notify_run_ended failed")

    def notify_incident(self, incident: dict) -> None:
        try:
            if incident.get("type") == "arrival" or not incident.get("bus_id"):
                return
            title = INCIDENT_TITLES.get(incident.get("type", ""), "Notice from the bus")
            body = incident.get("description") or f"Reported on {incident.get('bus_name') or 'the bus'}."
            for link in self.dao.parents_of_bus(str(incident["bus_id"])):
                self._notify(
                    link["parent_id"],
                    type="incident",
                    title=title,
                    body=body,
                    student_id=link["student_id"],
                    run_id=None,  # incidents are not deduped: each report matters
                    bus_id=incident.get("bus_id"),
                )
        except Exception:
            logger.exception("notify_incident failed")

    def notify_bus_position(self, driver_id: str, lat: float, lng: float) -> None:
        """Bus-approaching alerts for upcoming stops within the radius."""
        try:
            run = self.dao_active_run(driver_id)
            if not run:
                return
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
                )
        except Exception:
            logger.exception("notify_bus_position failed")

    # Internals ----------------------------------------------------------------

    def dao_active_run(self, driver_id: str) -> dict | None:
        return self.dao.active_run_for_driver(driver_id)

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
    ) -> None:
        row = self.dao.insert_notification(
            str(parent_id),
            type,
            title,
            body,
            student_id=str(student_id) if student_id else None,
            run_id=run_id,
            bus_id=str(bus_id) if bus_id else None,
        )
        if row is None:
            return  # run-scoped dedup suppressed a repeat
        self.send_to_user(str(parent_id), title, body, type)

    def send_to_user(self, user_id: str, title: str, body: str, type: str) -> None:
        """Best-effort delivery through every configured channel."""
        delivered = 0
        delivered += self._send_fcm(user_id, title, body, type)
        delivered += self._send_web_push(user_id, title, body, type)
        if delivered == 0:
            logger.info("push (simulated) -> user=%s type=%s title=%r", user_id, type, title)

    # FCM ----------------------------------------------------------------------

    def _firebase(self) -> Any:
        if self._firebase_app is not None:
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
                self._firebase_app = firebase_admin.initialize_app(cred, name="saferide")
        except Exception:
            logger.exception("Firebase initialization failed; FCM disabled")
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
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub["endpoint"],
                        "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                    },
                    data=payload,
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_subject},
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
