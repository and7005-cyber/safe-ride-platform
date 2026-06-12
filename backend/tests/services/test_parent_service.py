import pytest

from app.core.errors import BadRequestError, ForbiddenError
from app.services.parent_service import ParentService


class FakeParentDao:
    def __init__(self) -> None:
        self.parent_link = {"id": "link-1", "school_id": "school-1", "student_id": "student-1"}
        self.trip = {
            "id": "trip-1",
            "name": "Morning Route A",
            "session": "morning",
            "service_date": "2026-05-13",
            "scheduled_start": "06:30:00",
            "status": "active",
        }
        self.passengers = [
            {
                "id": "tp-1",
                "student_id": "student-1",
                "student_name": "Amina Otieno",
                "location_label": "Kilimani stop",
                "sequence_position": 1,
                "estimated_minutes_from_start": 5,
                "status": "boarded",
            },
            {
                "id": "tp-2",
                "student_id": "student-2",
                "student_name": "Brian Mwangi",
                "location_label": "Lavington stop",
                "sequence_position": 2,
                "estimated_minutes_from_start": 11,
                "status": "pending",
            },
        ]
        self.subscription = None

    def get_parent_link(self, token: str):
        return self.parent_link if token == "good-token" else None

    def get_active_trip_for_student_today(self, school_id: str, student_id: str):
        return self.trip

    def list_parent_progress_passengers(self, school_id: str, trip_id: str):
        return self.passengers

    def upsert_push_subscription(self, school_id: str, parent_link_id: str, endpoint: str, p256dh: str, auth: str):
        self.subscription = (school_id, parent_link_id, endpoint, p256dh, auth)


def test_parent_progress_hides_other_student_identity_and_location() -> None:
    service = ParentService(FakeParentDao())

    progress = service.get_trip_progress("good-token")

    assert progress["ownStudentId"] == "student-1"
    assert progress["trip"]["serviceDate"] == "2026-05-13"
    assert progress["trip"]["scheduledStart"] == "06:30:00"
    assert progress["passengers"][0]["studentId"] == "student-1"
    assert progress["passengers"][0]["studentName"] == "Amina Otieno"
    assert progress["passengers"][0]["locationLabel"] == "Kilimani stop"
    assert progress["passengers"][0]["sequencePosition"] == 1
    assert progress["passengers"][0]["estimatedMinutesFromStart"] == 5
    assert progress["passengers"][1]["studentId"] is None
    assert progress["passengers"][1]["studentName"] is None
    assert progress["passengers"][1]["locationLabel"] == "Stop 2"


def test_parent_progress_rejects_bad_token() -> None:
    service = ParentService(FakeParentDao())

    with pytest.raises(ForbiddenError, match="Invalid or revoked parent link"):
        service.get_trip_progress("bad-token")


def test_parent_progress_returns_empty_passengers_when_no_trip_today() -> None:
    dao = FakeParentDao()
    dao.trip = None
    service = ParentService(dao)

    progress = service.get_trip_progress("good-token")

    assert progress == {"ownStudentId": "student-1", "trip": None, "passengers": []}


def test_register_push_subscription_uses_parent_link_school() -> None:
    dao = FakeParentDao()
    service = ParentService(dao)

    result = service.register_push_subscription("good-token", "https://push.example/sub", "p256dh", "auth")

    assert result == {"ok": True}
    assert dao.subscription == ("school-1", "link-1", "https://push.example/sub", "p256dh", "auth")


def test_register_push_subscription_rejects_insecure_endpoint() -> None:
    service = ParentService(FakeParentDao())

    with pytest.raises(BadRequestError, match="Invalid subscription endpoint"):
        service.register_push_subscription("good-token", "http://push.example/sub", "p256dh", "auth")


def test_register_push_subscription_rejects_malformed_endpoint() -> None:
    service = ParentService(FakeParentDao())

    with pytest.raises(BadRequestError, match="Invalid subscription endpoint"):
        service.register_push_subscription("good-token", "https://", "p256dh", "auth")


def test_register_push_subscription_rejects_empty_keys() -> None:
    service = ParentService(FakeParentDao())

    with pytest.raises(BadRequestError, match="Invalid subscription keys"):
        service.register_push_subscription("good-token", "https://push.example/sub", "", "auth")


def test_register_push_subscription_rejects_bad_token() -> None:
    service = ParentService(FakeParentDao())

    with pytest.raises(ForbiddenError, match="Invalid or revoked parent link"):
        service.register_push_subscription("bad-token", "https://push.example/sub", "p256dh", "auth")
