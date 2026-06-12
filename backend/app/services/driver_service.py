from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import create_session_token, hash_session_token, verify_pin
from app.dao.driver_dao import DriverDao

PASSENGER_EVENT_TYPES = {"passenger_boarded", "passenger_not_present", "passenger_dropped"}
SUPPORTED_DRIVER_EVENT_TYPES = {
    "trip_started",
    "passenger_boarded",
    "passenger_not_present",
    "passenger_dropped",
    "trip_ended",
    "issue_reported",
}


class DriverService:
    def __init__(self, dao: DriverDao | None = None) -> None:
        self.dao = dao or DriverDao()

    def verify_pin(self, pin: str) -> dict:
        matches = [driver for driver in self.dao.list_active_drivers() if verify_pin(pin, driver["pin_hash"])]
        if len(matches) != 1:
            raise UnauthorizedError("Invalid driver PIN")

        driver = matches[0]
        session_token = create_session_token()
        self.dao.create_driver_session(
            driver["school_id"],
            driver["id"],
            hash_session_token(session_token),
        )
        return {
            "id": driver["id"],
            "driverId": driver["id"],
            "schoolId": driver["school_id"],
            "fullName": driver["full_name"],
            "sessionToken": session_token,
        }

    def get_session(self, session_token: str) -> dict:
        session = self.dao.get_session(session_token)
        if not session:
            raise UnauthorizedError("Driver session is invalid or expired")
        return session

    def record_event(self, input_data) -> str:
        if input_data.event_type not in SUPPORTED_DRIVER_EVENT_TYPES:
            raise ConflictError("Unsupported driver event type")

        session = self.get_session(input_data.session_token)
        trip = self.dao.get_assigned_trip(
            session["school_id"],
            session["driver_id"],
            input_data.trip_id,
        )
        if not trip:
            raise ForbiddenError("Trip is not assigned to this driver")

        self._validate_trip_state(input_data.event_type, trip["status"])
        self._validate_passenger_event(input_data, session["school_id"])

        metadata = input_data.metadata or {}
        record_trip_event_atomic = getattr(self.dao, "record_trip_event_atomic", None)
        if callable(record_trip_event_atomic):
            return record_trip_event_atomic(
                session["school_id"],
                input_data.trip_id,
                input_data.trip_passenger_id,
                input_data.event_type,
                session["driver_id"],
                input_data.occurred_at,
                metadata,
            )

        event_id = self.dao.insert_trip_event(
            session["school_id"],
            input_data.trip_id,
            input_data.trip_passenger_id,
            input_data.event_type,
            session["driver_id"],
            input_data.occurred_at,
            metadata,
        )
        self.dao.apply_trip_event(
            event_id,
            input_data.event_type,
            input_data.trip_id,
            session["school_id"],
            input_data.trip_passenger_id,
            input_data.occurred_at,
        )
        if input_data.event_type in PASSENGER_EVENT_TYPES:
            self.dao.enqueue_parent_notifications(
                event_id,
                session["school_id"],
                input_data.trip_passenger_id,
                input_data.event_type,
                metadata,
            )
        return event_id

    def _validate_trip_state(self, event_type: str, trip_status: str) -> None:
        if event_type == "trip_started" and trip_status != "scheduled":
            raise ConflictError("Only scheduled trips can be started")
        if event_type in {"passenger_boarded", "passenger_not_present", "passenger_dropped", "issue_reported"} and trip_status not in {
            "active",
            "delayed",
            "issue_reported",
        }:
            raise ConflictError("Driver events can only be recorded for active trips")
        if event_type == "trip_ended" and trip_status not in {"active", "delayed", "issue_reported"}:
            raise ConflictError("Only active trips can be completed")

    def _validate_passenger_event(self, input_data, school_id: str) -> None:
        if input_data.event_type not in PASSENGER_EVENT_TYPES:
            return
        if not input_data.trip_passenger_id:
            raise ConflictError("tripPassengerId is required for passenger driver events")
        passenger = self.dao.get_trip_passenger(
            school_id,
            input_data.trip_id,
            input_data.trip_passenger_id,
        )
        if not passenger:
            raise ForbiddenError("Trip passenger cannot be updated for this event")
        valid = (
            input_data.event_type in {"passenger_boarded", "passenger_not_present"} and passenger["status"] == "pending"
        ) or (input_data.event_type == "passenger_dropped" and passenger["status"] == "boarded")
        if not valid:
            raise ConflictError("Trip passenger cannot be updated for this event")
