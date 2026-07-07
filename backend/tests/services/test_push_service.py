"""Push service fan-out tests against a fake DAO (no DB, no FCM)."""

import pytest

from app.services.push_service import PushService, haversine_m, is_safe_push_endpoint


class FakePushDao:
    def __init__(self) -> None:
        self.notifications: list[dict] = []
        self.parents: dict[str, list[dict]] = {}  # student_id -> [{parent_id, student_id, student_name}]
        self.bus_parents: list[dict] = []
        self.run_students: list[dict] = []
        self.stops: list[dict] = []
        self.dedup_keys: set[tuple] = set()

    def insert_notification(self, user_id, type, title, body, student_id=None, run_id=None,
                            bus_id=None, run_type=None):
        if run_id is not None and student_id is not None:
            key = (user_id, run_id, student_id, type)
            if key in self.dedup_keys:
                return None
            self.dedup_keys.add(key)
        row = {
            "user_id": user_id, "type": type, "title": title, "body": body,
            "student_id": student_id, "run_id": run_id, "bus_id": bus_id,
            "run_type": run_type,
        }
        self.notifications.append(row)
        return row

    def parents_of_students(self, student_ids):
        out = []
        for sid in student_ids:
            out.extend(self.parents.get(sid, []))
        return out

    def parents_of_bus(self, bus_id):
        return self.bus_parents

    def students_on_run(self, run_id, include_absent=False):
        if include_absent:
            return self.run_students
        return [s for s in self.run_students if s["status"] != "absent"]

    def remaining_student_stops(self, run_id, stops_completed):
        return [s for s in self.stops if s["stop_order"] > stops_completed]

    def students_at_stop(self, run_id, stop_order):
        return [s for s in self.stops if s["stop_order"] == stop_order]

    def bus_name(self, bus_id):
        return "Kifaru Bus"

    def fcm_tokens_for_users(self, user_ids):
        return []

    def web_push_subscriptions_for_users(self, user_ids):
        return []


@pytest.fixture
def dao() -> FakePushDao:
    return FakePushDao()


@pytest.fixture
def service(dao: FakePushDao) -> PushService:
    return PushService(dao=dao)


RUN = {"id": "run-1", "bus_id": "bus-1", "type": "morning", "stops_completed": 0}
AFTERNOON_RUN = {"id": "run-2", "bus_id": "bus-1", "type": "afternoon", "stops_completed": 0}


def link(parent: str, student: str, name: str) -> dict:
    return {"parent_id": parent, "student_id": student, "student_name": name}


def test_morning_run_start_sends_run_started(service: PushService, dao: FakePushDao) -> None:
    dao.run_students = [{"id": "s1", "name": "Leila", "status": "at-school"}]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_run_started(RUN)

    assert len(dao.notifications) == 1
    note = dao.notifications[0]
    assert note["type"] == "run-started"
    assert "Leila" in note["body"]
    assert "Kifaru Bus" in note["body"]


def test_afternoon_run_start_sends_on_way_home(service: PushService, dao: FakePushDao) -> None:
    dao.run_students = [{"id": "s1", "name": "Leila", "status": "on-bus"}]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_run_started(AFTERNOON_RUN)

    assert [n["type"] for n in dao.notifications] == ["on-way-home"]


def test_absent_students_are_not_notified(service: PushService, dao: FakePushDao) -> None:
    dao.run_students = [
        {"id": "s1", "name": "Leila", "status": "absent"},
        {"id": "s2", "name": "Baraka", "status": "at-school"},
    ]
    dao.parents = {
        "s1": [link("p1", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_run_started(RUN)

    assert len(dao.notifications) == 1
    assert dao.notifications[0]["user_id"] == "p2"


def test_boarding_notifies_each_parent_of_the_student(
    service: PushService, dao: FakePushDao
) -> None:
    dao.parents = {"s1": [link("p1", "s1", "Leila"), link("p9", "s1", "Leila")]}

    service.notify_student_boarded(RUN, "s1")

    assert [n["type"] for n in dao.notifications] == ["student-boarded", "student-boarded"]
    assert {n["user_id"] for n in dao.notifications} == {"p1", "p9"}


def test_reached_school_only_for_morning_runs(service: PushService, dao: FakePushDao) -> None:
    dao.run_students = [{"id": "s1", "name": "Leila", "status": "on-bus"}]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_reached_school(AFTERNOON_RUN)
    assert dao.notifications == []

    service.notify_reached_school(RUN)
    assert [n["type"] for n in dao.notifications] == ["reached-school"]


def test_reached_school_skips_students_who_never_boarded(
    service: PushService, dao: FakePushDao
) -> None:
    # Leila missed the bus (still at-school); no false safety assertion.
    dao.run_students = [
        {"id": "s1", "name": "Leila", "status": "at-school"},
        {"id": "s2", "name": "Baraka", "status": "on-bus"},
    ]
    dao.parents = {
        "s1": [link("p1", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_reached_school(RUN)

    assert len(dao.notifications) == 1
    assert dao.notifications[0]["user_id"] == "p2"


def test_reached_school_dedups_within_a_run(service: PushService, dao: FakePushDao) -> None:
    dao.run_students = [{"id": "s1", "name": "Leila", "status": "on-bus"}]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_reached_school(RUN)
    service.notify_reached_school(RUN)  # e.g. gate arrival, then run end

    assert len(dao.notifications) == 1


def test_afternoon_run_end_sends_nothing(service: PushService, dao: FakePushDao) -> None:
    # Confirmed drop-offs were notified at tap time; the end-run sweep
    # normalizes unconfirmed students' statuses but a student the driver never
    # confirmed must not get a false 'dropped off' push (AE12).
    run = {**AFTERNOON_RUN, "boarded_student_ids": ["s1"]}
    dao.run_students = [
        {"id": "s1", "name": "Leila", "status": "dropped-off"},
        {"id": "s2", "name": "Baraka", "status": "dropped-off"},  # swept, unconfirmed
    ]
    dao.parents = {
        "s1": [link("p1", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_run_ended(run)

    assert dao.notifications == []


def test_morning_run_end_falls_back_to_reached_school(
    service: PushService, dao: FakePushDao
) -> None:
    run = {**RUN, "boarded_student_ids": ["s1"]}
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_run_ended(run)

    assert [n["type"] for n in dao.notifications] == ["reached-school"]


def test_dropped_off_notifies_only_that_students_parents(
    service: PushService, dao: FakePushDao
) -> None:
    # Both of Leila's parents hear about her drop-off; Baraka's parent (same
    # bus) hears nothing.
    dao.parents = {
        "s1": [link("p1", "s1", "Leila"), link("p9", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_student_dropped_off({"id": "s1", "name": "Leila"}, AFTERNOON_RUN)

    assert [n["type"] for n in dao.notifications] == ["dropped-off", "dropped-off"]
    assert {n["user_id"] for n in dao.notifications} == {"p1", "p9"}
    for note in dao.notifications:
        assert "Leila" in note["body"]
        assert "stop" in note["body"]
        assert note["run_id"] == "run-2"
        assert note["student_id"] == "s1"


def test_dropped_off_dedups_within_a_run(service: PushService, dao: FakePushDao) -> None:
    # A retried tap (or the legacy end-run path) must not re-notify.
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_student_dropped_off({"id": "s1", "name": "Leila"}, AFTERNOON_RUN)
    service.notify_student_dropped_off({"id": "s1", "name": "Leila"}, AFTERNOON_RUN)

    assert len(dao.notifications) == 1


def test_dropped_off_carries_run_type(service: PushService, dao: FakePushDao) -> None:
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_student_dropped_off({"id": "s1", "name": "Leila"}, AFTERNOON_RUN)

    assert [n["run_type"] for n in dao.notifications] == ["afternoon"]


def test_student_absent_notifies_only_that_students_parents(
    service: PushService, dao: FakePushDao
) -> None:
    # Two parents linked to Leila; Baraka's parent rides the same bus but
    # must never learn about her absence.
    dao.parents = {
        "s1": [link("p1", "s1", "Leila"), link("p9", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_student_absent({"id": "s1", "name": "Leila"}, RUN)

    assert [n["type"] for n in dao.notifications] == ["student-absent", "student-absent"]
    assert {n["user_id"] for n in dao.notifications} == {"p1", "p9"}
    for note in dao.notifications:
        assert "Leila" in note["body"]
        assert "absent at pickup" in note["body"]
        assert note["run_id"] == "run-1"
        assert note["student_id"] == "s1"


def test_student_absent_dedups_within_a_run(service: PushService, dao: FakePushDao) -> None:
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_student_absent({"id": "s1", "name": "Leila"}, RUN)
    service.notify_student_absent({"id": "s1", "name": "Leila"}, RUN, reason="sick")

    assert len(dao.notifications) == 1


def test_student_absent_includes_reason_when_given(
    service: PushService, dao: FakePushDao
) -> None:
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_student_absent({"id": "s1", "name": "Leila"}, RUN, reason="Doctor visit")

    assert "Doctor visit" in dao.notifications[0]["body"]


def test_run_scoped_notifications_carry_run_type(service: PushService, dao: FakePushDao) -> None:
    dao.parents = {
        "s1": [link("p1", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
        "s3": [link("p3", "s3", "Wanjiru")],
    }
    dao.stops = [
        {"stop_order": 1, "student_id": "s2", "student_name": "Baraka",
         "student_status": "at-school"},
    ]

    service.notify_student_boarded(RUN, "s1")
    service.notify_bus_approaching(RUN)
    service.notify_student_absent({"id": "s3", "name": "Wanjiru"}, AFTERNOON_RUN)

    by_type = {n["type"]: n["run_type"] for n in dao.notifications}
    assert by_type == {
        "student-boarded": "morning",
        "bus-approaching": "morning",
        "student-absent": "afternoon",
    }


def test_deprecated_position_path_carries_run_type(
    service: PushService, dao: FakePushDao
) -> None:
    dao.stops = [
        {"stop_order": 1, "lat": -1.2921, "lng": 36.8219, "student_id": "s1",
         "student_name": "Leila", "student_status": "at-school"},
    ]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_bus_position(RUN, -1.2920, 36.8219)

    assert [n["run_type"] for n in dao.notifications] == ["morning"]


def test_incident_passes_through_run_type_without_run_dedup(
    service: PushService, dao: FakePushDao
) -> None:
    dao.bus_parents = [link("p1", "s1", "Leila")]

    service.notify_incident({
        "type": "breakdown", "bus_id": "bus-1", "run_type": "afternoon",
        "description": "Flat tire",
    })

    note = dao.notifications[0]
    assert note["run_id"] is None  # incidents keep repeat-report semantics
    assert note["run_type"] == "afternoon"


def test_incident_notifies_each_parent_once_without_run_dedup(
    service: PushService, dao: FakePushDao
) -> None:
    incident = {
        "type": "breakdown", "bus_id": "bus-1", "bus_name": "Kifaru Bus",
        "description": "Flat tire on Ngong Road",
    }
    # One parent with two children on the bus, plus another parent.
    dao.bus_parents = [
        link("p1", "s1", "Leila"),
        link("p1", "s2", "Baraka"),
        link("p2", "s3", "Wanjiru"),
    ]

    service.notify_incident(incident)
    assert [n["user_id"] for n in dao.notifications] == ["p1", "p2"]  # p1 once

    service.notify_incident(incident)  # a second report still notifies
    assert len(dao.notifications) == 4
    assert dao.notifications[0]["title"] == "Vehicle breakdown"
    assert "Flat tire" in dao.notifications[0]["body"]


def test_arrival_incidents_do_not_double_notify(service: PushService, dao: FakePushDao) -> None:
    dao.bus_parents = [link("p1", "s1", "Leila")]

    service.notify_incident({"type": "arrival", "bus_id": "bus-1", "description": "arrived"})

    assert dao.notifications == []


def test_student_stamped_incidents_never_fan_out(service: PushService, dao: FakePushDao) -> None:
    """Defense in depth (U5): a student-stamped incident names a child, so it
    must never reach the bus-wide fan-out — whatever its type (the guard
    protects the driver-absent 'student' path and the parent 'cancellation'
    path alike)."""
    dao.bus_parents = [link("p1", "s1", "Leila"), link("p2", "s3", "Wanjiru")]

    for incident_type in ("cancellation", "student", "other"):
        service.notify_incident({
            "type": incident_type, "bus_id": "bus-1", "bus_name": "Kifaru Bus",
            "student_id": "s1", "description": "Leila is off the afternoon run",
        })

    assert dao.notifications == []


def test_ride_cancelled_notifies_all_linked_parents_with_scoped_run_type(
    service: PushService, dao: FakePushDao
) -> None:
    """Cancel-a-Ride confirmation (U5): every linked parent of THAT child gets
    one 'ride-cancelled' row — run_id NULL (no run-scoped dedup: the caller
    only fires on real transitions) and run_type mapped from the scope so
    the feed's period filter surfaces it where the parent will look."""
    dao.parents = {
        "s1": [link("p1", "s1", "Leila"), link("p2", "s1", "Leila")],
        "s2": [link("p3", "s2", "Baraka")],  # another household: never notified
    }

    service.notify_ride_cancelled({"id": "s1", "name": "Leila", "bus_id": "bus-1"}, "afternoon")

    assert [n["user_id"] for n in dao.notifications] == ["p1", "p2"]
    for note in dao.notifications:
        assert note["type"] == "ride-cancelled"
        assert note["run_id"] is None
        assert note["run_type"] == "afternoon"
        assert note["student_id"] == "s1"
        assert "Leila" in note["body"]


def test_ride_cancelled_day_scope_maps_run_type_to_none(
    service: PushService, dao: FakePushDao
) -> None:
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_ride_cancelled({"id": "s1", "name": "Leila", "bus_id": None}, "day")

    assert len(dao.notifications) == 1
    assert dao.notifications[0]["run_type"] is None
    assert dao.notifications[0]["type"] == "ride-cancelled"


def test_admin_broadcast_one_row_per_distinct_parent(
    service: PushService, dao: FakePushDao
) -> None:
    """Route broadcast (U8): one 'admin-notice' row per DISTINCT recipient in
    the endpoint-resolved set — a duplicated id never double-sends. run_id
    NULL (two identical sends must stay two rows, R23) and run_type NULL
    (period-agnostic, R22); the body lands verbatim."""
    route = {"id": "r1", "name": "Express 1 — Morning", "bus_id": "bus-1"}

    service.notify_admin_broadcast(route, "Pickup delayed 20 min.", ["p1", "p2", "p1"])

    assert [n["user_id"] for n in dao.notifications] == ["p1", "p2"]
    for note in dao.notifications:
        assert note["type"] == "admin-notice"
        assert note["title"] == "School notice — Express 1 — Morning"
        assert note["body"] == "Pickup delayed 20 min."
        assert note["run_id"] is None
        assert note["run_type"] is None
        assert note["student_id"] is None
        assert note["bus_id"] == "bus-1"


def test_admin_broadcast_isolates_per_recipient_failures(
    service: PushService, dao: FakePushDao
) -> None:
    """One recipient's failing insert must not cut off the rest of the route:
    recipients before AND after the failure still get their rows (review C1)."""
    real_insert = dao.insert_notification

    def flaky_insert(user_id, *args, **kwargs):
        if user_id == "p2":
            raise RuntimeError("db hiccup")
        return real_insert(user_id, *args, **kwargs)

    dao.insert_notification = flaky_insert  # type: ignore[assignment]

    service.notify_admin_broadcast(
        {"id": "r1", "name": "Express 1", "bus_id": "bus-1"}, "hi", ["p1", "p2", "p3"]
    )

    assert [n["user_id"] for n in dao.notifications] == ["p1", "p3"]


def test_ride_cancelled_isolates_per_recipient_failures(
    service: PushService, dao: FakePushDao
) -> None:
    """A failing co-parent insert must not cost the other linked parents
    their confirmation (review C1)."""
    dao.parents = {
        "s1": [link("p1", "s1", "Leila"), link("p2", "s1", "Leila"), link("p3", "s1", "Leila")],
    }
    real_insert = dao.insert_notification

    def flaky_insert(user_id, *args, **kwargs):
        if user_id == "p2":
            raise RuntimeError("db hiccup")
        return real_insert(user_id, *args, **kwargs)

    dao.insert_notification = flaky_insert  # type: ignore[assignment]

    service.notify_ride_cancelled({"id": "s1", "name": "Leila", "bus_id": "bus-1"}, "morning")

    assert [n["user_id"] for n in dao.notifications] == ["p1", "p3"]
    assert all(n["type"] == "ride-cancelled" for n in dao.notifications)


def test_admin_broadcast_title_is_length_bounded(
    service: PushService, dao: FakePushDao
) -> None:
    """Web-push services reject ~4KB payloads: a very long route name must
    not blow up the composed title."""
    from app.services.push_service import BROADCAST_TITLE_MAX_CHARS

    service.notify_admin_broadcast({"id": "r1", "name": "R" * 300, "bus_id": None}, "hi", ["p1"])

    assert len(dao.notifications) == 1
    assert len(dao.notifications[0]["title"]) == BROADCAST_TITLE_MAX_CHARS
    assert dao.notifications[0]["title"].endswith("…")


def test_bus_approaching_within_radius(service: PushService, dao: FakePushDao) -> None:
    # ~550m from the bus position below.
    dao.stops = [
        {"stop_order": 1, "lat": -1.2921, "lng": 36.8219, "student_id": "s1",
         "student_name": "Leila", "student_status": "at-school"},
        # Far stop (~5km) must not notify.
        {"stop_order": 2, "lat": -1.3300, "lng": 36.8600, "student_id": "s2",
         "student_name": "Baraka", "student_status": "at-school"},
    ]
    dao.parents = {
        "s1": [link("p1", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_bus_position(RUN, -1.2871, 36.8219)

    assert [n["type"] for n in dao.notifications] == ["bus-approaching"]
    assert dao.notifications[0]["user_id"] == "p1"


def test_bus_approaching_dedups_per_run(service: PushService, dao: FakePushDao) -> None:
    dao.stops = [
        {"stop_order": 1, "lat": -1.2921, "lng": 36.8219, "student_id": "s1",
         "student_name": "Leila", "student_status": "at-school"},
    ]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_bus_position(RUN, -1.2920, 36.8219)
    service.notify_bus_position(RUN, -1.2919, 36.8219)

    assert len(dao.notifications) == 1


def test_bus_approaching_skips_passed_stops(service: PushService, dao: FakePushDao) -> None:
    run = {**RUN, "stops_completed": 1}
    dao.stops = [
        {"stop_order": 1, "lat": -1.2921, "lng": 36.8219, "student_id": "s1",
         "student_name": "Leila", "student_status": "at-school"},
    ]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_bus_position(run, -1.2921, 36.8219)

    assert dao.notifications == []


def test_bus_approaching_fires_for_next_stop_only(service: PushService, dao: FakePushDao) -> None:
    # Bus just arrived at stop 1; only the NEXT stop (2) gets "approaching".
    run = {**RUN, "stops_completed": 1}
    dao.stops = [
        {"stop_order": 2, "student_id": "s2", "student_name": "Baraka", "student_status": "at-school"},
        {"stop_order": 3, "student_id": "s3", "student_name": "Wanjiru", "student_status": "at-school"},
    ]
    dao.parents = {"s2": [link("p2", "s2", "Baraka")], "s3": [link("p3", "s3", "Wanjiru")]}

    service.notify_bus_approaching(run)
    assert [n["type"] for n in dao.notifications] == ["bus-approaching"]
    assert dao.notifications[0]["user_id"] == "p2"

    service.notify_bus_approaching(run)  # arriving again must not re-alert
    assert len(dao.notifications) == 1


def test_bus_approaching_skips_absent_and_gate(service: PushService, dao: FakePushDao) -> None:
    # Next stop's only child is absent → no alert.
    run = {**RUN, "stops_completed": 0}  # next is stop 1
    dao.stops = [
        {"stop_order": 1, "student_id": "s1", "student_name": "Leila", "student_status": "absent"},
    ]
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}
    service.notify_bus_approaching(run)
    assert dao.notifications == []

    # No student at the next stop (e.g. the school gate is next) → no alert.
    gate_run = {**RUN, "stops_completed": 5}
    service.notify_bus_approaching(gate_run)
    assert dao.notifications == []


def test_haversine_known_distance() -> None:
    # Nairobi CBD to Westlands is roughly 3.2-3.5 km.
    distance = haversine_m(-1.2864, 36.8172, -1.2672, 36.8071)
    assert 2000 < distance < 5000


def test_push_failures_never_raise(service: PushService, dao: FakePushDao) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    dao.students_on_run = boom  # type: ignore[assignment]

    service.notify_run_started(RUN)  # must not raise

    assert dao.notifications == []


def test_push_endpoint_ssrf_guard() -> None:
    assert is_safe_push_endpoint("https://fcm.googleapis.com/fcm/send/abc")
    assert is_safe_push_endpoint("https://updates.push.services.mozilla.com/wpush/v2/x")

    assert not is_safe_push_endpoint("http://fcm.googleapis.com/insecure")
    assert not is_safe_push_endpoint("https://localhost/push")
    assert not is_safe_push_endpoint("https://127.0.0.1/push")
    assert not is_safe_push_endpoint("https://10.0.0.5/push")
    assert not is_safe_push_endpoint("https://192.168.1.1/push")
    assert not is_safe_push_endpoint("https://169.254.169.254/latest/meta-data")
    assert not is_safe_push_endpoint("https://internal.local/push")
    assert not is_safe_push_endpoint("ftp://example.com/x")
    assert not is_safe_push_endpoint("not a url")
