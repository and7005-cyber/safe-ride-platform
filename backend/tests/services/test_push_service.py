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

    def insert_notification(self, user_id, type, title, body, student_id=None, run_id=None, bus_id=None):
        if run_id is not None and student_id is not None:
            key = (user_id, run_id, student_id, type)
            if key in self.dedup_keys:
                return None
            self.dedup_keys.add(key)
        row = {
            "user_id": user_id, "type": type, "title": title, "body": body,
            "student_id": student_id, "run_id": run_id, "bus_id": bus_id,
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


def test_afternoon_run_end_sends_dropped_off_for_boarded_snapshot(
    service: PushService, dao: FakePushDao
) -> None:
    # end_run captured the pre-sweep on-bus roster; statuses already swept.
    run = {**AFTERNOON_RUN, "boarded_student_ids": ["s1"]}
    dao.run_students = [
        {"id": "s1", "name": "Leila", "status": "dropped-off"},
        {"id": "s2", "name": "Baraka", "status": "dropped-off"},  # never boarded
    ]
    dao.parents = {
        "s1": [link("p1", "s1", "Leila")],
        "s2": [link("p2", "s2", "Baraka")],
    }

    service.notify_run_ended(run)

    assert [n["type"] for n in dao.notifications] == ["dropped-off"]
    assert dao.notifications[0]["user_id"] == "p1"


def test_morning_run_end_falls_back_to_reached_school(
    service: PushService, dao: FakePushDao
) -> None:
    run = {**RUN, "boarded_student_ids": ["s1"]}
    dao.parents = {"s1": [link("p1", "s1", "Leila")]}

    service.notify_run_ended(run)

    assert [n["type"] for n in dao.notifications] == ["reached-school"]


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
