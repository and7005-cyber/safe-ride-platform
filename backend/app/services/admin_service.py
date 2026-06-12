from secrets import token_urlsafe

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_pin
from app.dao.admin_dao import AdminDao

VALID_ATTENDANCE_STATUSES = {"riding", "absent", "alternative_transport"}
VALID_TRIP_PASSENGER_STATUSES = {
    "pending",
    "boarded",
    "dropped",
    "absent_admin",
    "absent_driver",
    "alternative_transport",
}


class AdminService:
    def __init__(self, dao: AdminDao | None = None) -> None:
        self.dao = dao or AdminDao()

    def create_driver(self, input_data):
        if getattr(input_data, "default_bus_id", None):
            if not self.dao.bus_belongs_to_school(input_data.school_id, input_data.default_bus_id):
                raise BadRequestError("Default bus is invalid")

        try:
            pin_hash = hash_pin(input_data.pin)
        except ValueError as error:
            raise BadRequestError(str(error)) from error
        return self.dao.create_driver(input_data, pin_hash)

    def update_student(self, student_id: str, input_data):
        self._require_text(input_data.full_name, "Student full name is required")
        self._require_text(input_data.home_address, "Student home address is required")
        result = self.dao.update_student(student_id, input_data)
        if not result:
            raise NotFoundError("Student not found")
        return result

    def create_student_setup(self, input_data):
        self._require_text(input_data.student.full_name, "Student full name is required")
        self._require_text(input_data.student.home_address, "Student home address is required")

        if input_data.parent_contact:
            self._require_text(input_data.parent_contact.contact_1_name, "Primary parent name is required")
            self._require_text(input_data.parent_contact.contact_1_phone, "Primary parent phone is required")
            self._require_text(
                input_data.parent_contact.contact_1_relationship,
                "Primary parent relationship is required",
            )

        if input_data.trip_assignment:
            if input_data.trip_assignment.sequence_position < 1:
                raise BadRequestError("Stop number must be greater than zero")
            if input_data.trip_assignment.estimated_minutes_from_start < 0:
                raise BadRequestError("Minutes from start must be zero or greater")

        parent_link_token = token_urlsafe(24) if input_data.create_parent_link else None
        result = self.dao.create_student_setup(input_data, parent_link_token)
        if result["status"] == "trip_not_found":
            raise NotFoundError("Trip was not found for this school")
        return result["data"]

    def mark_daily_attendance(self, input_data):
        if input_data.status not in VALID_ATTENDANCE_STATUSES:
            raise BadRequestError("Attendance status is invalid")

        mark_daily_attendance = getattr(self.dao, "mark_daily_attendance", None)
        if callable(mark_daily_attendance):
            return mark_daily_attendance(input_data)

        attendance_row = self.dao.upsert_daily_attendance(input_data)
        self.dao.apply_daily_attendance(attendance_row)
        return attendance_row

    def correct_trip_passenger_status(self, input_data):
        if input_data.corrected_status not in VALID_TRIP_PASSENGER_STATUSES:
            raise BadRequestError("Corrected trip passenger status is invalid")

        correct_with_audit = getattr(self.dao, "correct_trip_passenger_status_with_audit", None)
        if callable(correct_with_audit):
            result = correct_with_audit(
                input_data.school_id,
                input_data.trip_passenger_id,
                input_data.corrected_status,
                input_data.reason,
            )
            if result["status"] == "not_found":
                raise NotFoundError("Trip passenger record not found")
            if result["status"] == "not_completed":
                raise ConflictError("Only completed trip records can be corrected")
            return result["audit_id"]

        existing = self.dao.get_trip_passenger_for_update(
            input_data.school_id,
            input_data.trip_passenger_id,
        )
        if not existing:
            raise NotFoundError("Trip passenger record not found")
        if existing["trip_status"] != "completed":
            raise ConflictError("Only completed trip records can be corrected")

        original_value = {
            "status": existing["status"],
            "actual_pickup_time": existing["actual_pickup_time"],
            "actual_dropoff_time": existing["actual_dropoff_time"],
        }
        corrected_value = {"status": input_data.corrected_status}
        self.dao.correct_trip_passenger_status(
            input_data.school_id,
            input_data.trip_passenger_id,
            input_data.corrected_status,
        )
        return self.dao.insert_audit_log(
            input_data.school_id,
            input_data.trip_passenger_id,
            original_value,
            corrected_value,
            input_data.reason,
        )

    def _require_text(self, value: str | None, message: str) -> None:
        if value is None or not value.strip():
            raise BadRequestError(message)
