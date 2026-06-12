import pytest

from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import hash_pin
from app.services.driver_service import DriverService


class FakeDriverDao:
    def __init__(self) -> None:
        self.driver = {
            "id": "driver-1",
            "school_id": "school-1",
            "full_name": "Peter Mwangi",
            "pin_hash": hash_pin("1234", salt="demo-driver-salt"),
        }
        self.session = {"driver_id": "driver-1", "school_id": "school-1"}
        self.trip = {"id": "trip-1", "school_id": "school-1", "driver_id": "driver-1", "status": "scheduled"}
        self.passenger = {"id": "tp-1", "status": "pending", "student_id": "student-1"}
        self.inserted_event = None
        self.applied_event = None
        self.enqueued_event = None

    def list_active_drivers(self):
        return [self.driver]

    def create_driver_session(self, school_id: str, driver_id: str, token_hash: str):
        return None

    def get_session(self, session_token: str):
        return self.session if session_token == "valid-session" else None

    def get_assigned_trip(self, school_id: str, driver_id: str, trip_id: str):
        return self.trip

    def get_trip_passenger(self, school_id: str, trip_id: str, trip_passenger_id: str):
        return self.passenger

    def insert_trip_event(self, school_id: str, trip_id: str, trip_passenger_id, event_type: str, driver_id: str, occurred_at, metadata: dict):
        self.inserted_event = (school_id, trip_id, trip_passenger_id, event_type, driver_id, occurred_at, metadata)
        return "event-1"

    def apply_trip_event(self, event_id: str, event_type: str, trip_id: str, school_id: str, trip_passenger_id, occurred_at):
        self.applied_event = (event_id, event_type, trip_id, school_id, trip_passenger_id, occurred_at)

    def enqueue_parent_notifications(self, event_id: str, school_id: str, trip_passenger_id: str, event_type: str, metadata: dict):
        self.enqueued_event = (event_id, school_id, trip_passenger_id, event_type, metadata)


class FakeAtomicDriverDao(FakeDriverDao):
    def __init__(self) -> None:
        super().__init__()
        self.recorded_event = None

    def record_trip_event_atomic(
        self,
        school_id: str,
        trip_id: str,
        trip_passenger_id,
        event_type: str,
        driver_id: str,
        occurred_at,
        metadata: dict,
    ):
        self.recorded_event = (school_id, trip_id, trip_passenger_id, event_type, driver_id, occurred_at, metadata)
        return "event-atomic"


class EventInput:
    session_token = "valid-session"
    trip_id = "trip-1"
    trip_passenger_id = None
    event_type = "trip_started"
    occurred_at = "2026-05-13T06:30:00Z"
    metadata = {}


def test_verify_driver_pin_returns_session() -> None:
    service = DriverService(FakeDriverDao())

    session = service.verify_pin("1234")

    assert session["driverId"] == "driver-1"
    assert session["schoolId"] == "school-1"
    assert session["fullName"] == "Peter Mwangi"
    assert len(session["sessionToken"]) == 64


def test_verify_driver_pin_rejects_unknown_pin() -> None:
    service = DriverService(FakeDriverDao())

    with pytest.raises(UnauthorizedError, match="Invalid driver PIN"):
        service.verify_pin("9999")


def test_record_trip_started_event_for_scheduled_trip() -> None:
    dao = FakeDriverDao()
    service = DriverService(dao)

    event_id = service.record_event(EventInput())

    assert event_id == "event-1"
    assert dao.inserted_event[3] == "trip_started"
    assert dao.applied_event[1] == "trip_started"


def test_record_event_uses_atomic_dao_when_available() -> None:
    dao = FakeAtomicDriverDao()
    service = DriverService(dao)

    event_id = service.record_event(EventInput())

    assert event_id == "event-atomic"
    assert dao.recorded_event[3] == "trip_started"
    assert dao.inserted_event is None


def test_passenger_boarding_requires_pending_passenger() -> None:
    dao = FakeDriverDao()
    dao.trip["status"] = "active"
    service = DriverService(dao)
    event = EventInput()
    event.event_type = "passenger_boarded"
    event.trip_passenger_id = "tp-1"

    event_id = service.record_event(event)

    assert event_id == "event-1"
    assert dao.enqueued_event[3] == "passenger_boarded"


def test_passenger_event_requires_trip_passenger_id() -> None:
    dao = FakeDriverDao()
    dao.trip["status"] = "active"
    service = DriverService(dao)
    event = EventInput()
    event.event_type = "passenger_boarded"
    event.trip_passenger_id = None

    with pytest.raises(ConflictError, match="tripPassengerId is required"):
        service.record_event(event)


def test_passenger_dropoff_requires_boarded_passenger() -> None:
    dao = FakeDriverDao()
    dao.trip["status"] = "active"
    dao.passenger["status"] = "pending"
    service = DriverService(dao)
    event = EventInput()
    event.event_type = "passenger_dropped"
    event.trip_passenger_id = "tp-1"

    with pytest.raises(ConflictError, match="Trip passenger cannot be updated for this event"):
        service.record_event(event)


def test_driver_event_rejects_unsupported_event_type() -> None:
    service = DriverService(FakeDriverDao())
    event = EventInput()
    event.event_type = "driver_took_a_shortcut"

    with pytest.raises(ConflictError, match="Unsupported driver event type"):
        service.record_event(event)


def test_driver_cannot_record_unassigned_session() -> None:
    dao = FakeDriverDao()
    service = DriverService(dao)
    event = EventInput()
    event.session_token = "bad-session"

    with pytest.raises(UnauthorizedError, match="Driver session is invalid or expired"):
        service.record_event(event)


def test_driver_cannot_record_unassigned_trip() -> None:
    dao = FakeDriverDao()
    dao.trip = None
    service = DriverService(dao)

    with pytest.raises(ForbiddenError, match="Trip is not assigned to this driver"):
        service.record_event(EventInput())


def test_driver_event_rejects_invalid_trip_state() -> None:
    dao = FakeDriverDao()
    dao.trip["status"] = "completed"
    service = DriverService(dao)

    with pytest.raises(ConflictError, match="Only scheduled trips can be started"):
        service.record_event(EventInput())
