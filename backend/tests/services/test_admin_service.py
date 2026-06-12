import pytest

from app.core.errors import BadRequestError, ConflictError
from app.services.admin_service import AdminService


class FakeAdminDao:
    def __init__(self) -> None:
        self.created_driver = None
        self.applied_attendance = None
        self.updated_student = None
        self.created_student_setup = None
        self.parent_link_token = None
        self.bus_exists = True
        self.trip_passenger = {
            "id": "tp-1",
            "school_id": "school-1",
            "status": "boarded",
            "actual_pickup_time": "2026-05-13T06:35:00+00:00",
            "actual_dropoff_time": None,
            "trip_status": "completed",
        }
        self.audit_record = None

    def bus_belongs_to_school(self, school_id: str, bus_id: str) -> bool:
        return self.bus_exists

    def create_driver(self, input_data, pin_hash: str):
        self.created_driver = (input_data, pin_hash)
        return {
            "id": "driver-1",
            "school_id": input_data.school_id,
            "full_name": input_data.full_name,
            "phone": input_data.phone,
            "default_bus_id": input_data.default_bus_id,
        }

    def update_student(self, student_id: str, input_data):
        self.updated_student = (student_id, input_data)
        return {
            "id": student_id,
            "school_id": input_data.school_id,
            "full_name": input_data.full_name,
            "home_address": input_data.home_address,
            "home_location_note": input_data.home_location_note,
        }

    def create_student_setup(self, input_data, parent_link_token: str | None):
        self.created_student_setup = input_data
        self.parent_link_token = parent_link_token
        return {
            "status": "ok",
            "data": {
                "student": {"id": "student-1", "full_name": input_data.student.full_name},
                "parentContact": {"id": "parent-contact-1"} if input_data.parent_contact else None,
                "parentLink": {"token": parent_link_token} if parent_link_token else None,
                "tripPassenger": {"id": "trip-passenger-1"} if input_data.trip_assignment else None,
            },
        }

    def upsert_daily_attendance(self, input_data):
        return {"id": "attendance-1", "school_id": input_data.school_id, "student_id": input_data.student_id, "attendance_date": input_data.attendance_date, "status": input_data.status}

    def apply_daily_attendance(self, attendance_row):
        self.applied_attendance = attendance_row

    def get_trip_passenger_for_update(self, school_id: str, trip_passenger_id: str):
        return self.trip_passenger

    def correct_trip_passenger_status(self, school_id: str, trip_passenger_id: str, corrected_status: str):
        return None

    def insert_audit_log(self, school_id: str, entity_id: str, original_value: dict, corrected_value: dict, reason: str):
        self.audit_record = (school_id, entity_id, original_value, corrected_value, reason)
        return "audit-1"


class FakeAtomicAdminDao:
    def __init__(self) -> None:
        self.marked_attendance = None
        self.corrected_status = None

    def mark_daily_attendance(self, input_data):
        self.marked_attendance = input_data
        return {"id": "attendance-atomic", "status": input_data.status}

    def correct_trip_passenger_status_with_audit(
        self,
        school_id: str,
        trip_passenger_id: str,
        corrected_status: str,
        reason: str,
    ):
        self.corrected_status = (school_id, trip_passenger_id, corrected_status, reason)
        return {"status": "ok", "audit_id": "audit-atomic"}


class DriverInput:
    school_id = "school-1"
    full_name = "Peter Mwangi"
    phone = "+254700000001"
    default_bus_id = None
    pin = "1234"


class AttendanceInput:
    school_id = "school-1"
    student_id = "student-1"
    attendance_date = "2026-05-13"
    status = "absent"
    note = "Sick"


class CorrectionInput:
    school_id = "school-1"
    trip_passenger_id = "tp-1"
    corrected_status = "dropped"
    reason = "Driver corrected record after call"


class UpdateStudentInput:
    school_id = "school-1"
    full_name = "Amina Otieno"
    home_address = "Updated Kilimani Road"
    home_location_note = "Gate C"


class StudentSetupStudentInput:
    full_name = "Nia Wanjiku"
    home_address = "Ngong Road"
    home_location_note = "Near main gate"


class StudentSetupParentInput:
    contact_1_name = "Mary Wanjiku"
    contact_1_phone = "+254700000010"
    contact_1_relationship = "Mother"
    contact_2_name = "John Wanjiku"
    contact_2_phone = "+254700000011"
    contact_2_relationship = "Father"


class StudentSetupTripInput:
    trip_id = "trip-1"
    sequence_position = 3
    estimated_minutes_from_start = 12


class StudentSetupInput:
    school_id = "school-1"
    student = StudentSetupStudentInput()
    parent_contact = StudentSetupParentInput()
    create_parent_link = True
    trip_assignment = StudentSetupTripInput()


def test_create_driver_hashes_pin_before_insert() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    result = service.create_driver(DriverInput())

    assert result["id"] == "driver-1"
    assert dao.created_driver[1].startswith("pbkdf2_sha256$")


def test_create_driver_stores_phone_and_default_bus() -> None:
    dao = FakeAdminDao()
    input_data = DriverInput()
    input_data.default_bus_id = "bus-1"
    service = AdminService(dao)

    result = service.create_driver(input_data)

    assert result["phone"] == "+254700000001"
    assert result["default_bus_id"] == "bus-1"
    assert dao.created_driver[0].default_bus_id == "bus-1"


def test_create_driver_rejects_cross_school_default_bus() -> None:
    dao = FakeAdminDao()
    dao.bus_exists = False
    input_data = DriverInput()
    input_data.default_bus_id = "bus-from-another-school"
    service = AdminService(dao)

    with pytest.raises(BadRequestError, match="Default bus is invalid"):
        service.create_driver(input_data)


def test_update_student_validates_and_delegates() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    result = service.update_student("student-1", UpdateStudentInput())

    assert result["home_address"] == "Updated Kilimani Road"
    assert dao.updated_student[0] == "student-1"


def test_update_student_rejects_empty_address() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)
    input_data = UpdateStudentInput()
    input_data.home_address = "   "

    with pytest.raises(BadRequestError, match="Student home address is required"):
        service.update_student("student-1", input_data)


def test_create_student_setup_generates_parent_link_token_and_delegates() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    result = service.create_student_setup(StudentSetupInput())

    assert result["student"]["id"] == "student-1"
    assert result["parentContact"]["id"] == "parent-contact-1"
    assert result["parentLink"]["token"] == dao.parent_link_token
    assert result["tripPassenger"]["id"] == "trip-passenger-1"
    assert isinstance(dao.parent_link_token, str)
    assert len(dao.parent_link_token) >= 32


def test_create_student_setup_without_optional_sections_creates_student_only() -> None:
    dao = FakeAdminDao()
    input_data = StudentSetupInput()
    input_data.parent_contact = None
    input_data.create_parent_link = False
    input_data.trip_assignment = None
    service = AdminService(dao)

    result = service.create_student_setup(input_data)

    assert result["student"]["id"] == "student-1"
    assert result["parentContact"] is None
    assert result["parentLink"] is None
    assert result["tripPassenger"] is None
    assert dao.parent_link_token is None


def test_create_student_setup_rejects_missing_parent_phone() -> None:
    dao = FakeAdminDao()
    input_data = StudentSetupInput()
    input_data.parent_contact.contact_1_phone = ""
    service = AdminService(dao)

    with pytest.raises(BadRequestError, match="Primary parent phone is required"):
        service.create_student_setup(input_data)


def test_mark_daily_attendance_applies_attendance_to_trip_passengers() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    row = service.mark_daily_attendance(AttendanceInput())

    assert row["id"] == "attendance-1"
    assert dao.applied_attendance == row


def test_mark_daily_attendance_uses_atomic_dao_when_available() -> None:
    dao = FakeAtomicAdminDao()
    service = AdminService(dao)

    row = service.mark_daily_attendance(AttendanceInput())

    assert row["id"] == "attendance-atomic"
    assert dao.marked_attendance.status == "absent"


def test_correct_trip_passenger_status_writes_audit_record() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)

    audit_id = service.correct_trip_passenger_status(CorrectionInput())

    assert audit_id == "audit-1"
    assert dao.audit_record[2]["status"] == "boarded"
    assert dao.audit_record[3] == {"status": "dropped"}


def test_correct_trip_passenger_status_uses_atomic_audit_dao_when_available() -> None:
    dao = FakeAtomicAdminDao()
    service = AdminService(dao)

    audit_id = service.correct_trip_passenger_status(CorrectionInput())

    assert audit_id == "audit-atomic"
    assert dao.corrected_status == (
        "school-1",
        "tp-1",
        "dropped",
        "Driver corrected record after call",
    )


def test_correct_trip_passenger_status_requires_completed_trip() -> None:
    dao = FakeAdminDao()
    dao.trip_passenger["trip_status"] = "active"
    service = AdminService(dao)

    with pytest.raises(ConflictError, match="Only completed trip records can be corrected"):
        service.correct_trip_passenger_status(CorrectionInput())


def test_create_driver_rejects_bad_pin() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)
    bad_input = DriverInput()
    bad_input.pin = "abc"

    with pytest.raises(BadRequestError, match="Driver PIN must be 4 to 6 digits"):
        service.create_driver(bad_input)


def test_mark_daily_attendance_rejects_invalid_status() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)
    bad_input = AttendanceInput()
    bad_input.status = "vacation"

    with pytest.raises(BadRequestError, match="Attendance status is invalid"):
        service.mark_daily_attendance(bad_input)


def test_correct_trip_passenger_status_rejects_invalid_status() -> None:
    dao = FakeAdminDao()
    service = AdminService(dao)
    bad_input = CorrectionInput()
    bad_input.corrected_status = "not_present"

    with pytest.raises(BadRequestError, match="Corrected trip passenger status is invalid"):
        service.correct_trip_passenger_status(bad_input)
