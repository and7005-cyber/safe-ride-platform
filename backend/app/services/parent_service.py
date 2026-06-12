from urllib.parse import urlparse

from app.core.errors import BadRequestError, ForbiddenError
from app.dao.parent_dao import ParentDao


class ParentService:
    def __init__(self, dao: ParentDao | None = None) -> None:
        self.dao = dao or ParentDao()

    def get_trip_progress(self, token: str) -> dict:
        parent_link = self.dao.get_parent_link(token)
        if not parent_link:
            raise ForbiddenError("Invalid or revoked parent link")

        trip = self.dao.get_active_trip_for_student_today(
            parent_link["school_id"],
            parent_link["student_id"],
        )
        if not trip:
            return {"ownStudentId": parent_link["student_id"], "trip": None, "passengers": []}

        passengers = [
            self._to_parent_safe_passenger(row, parent_link["student_id"])
            for row in self.dao.list_parent_progress_passengers(parent_link["school_id"], trip["id"])
        ]
        return {
            "ownStudentId": parent_link["student_id"],
            "trip": {
                "id": trip["id"],
                "name": trip["name"],
                "session": trip["session"],
                "serviceDate": str(trip["service_date"]),
                "scheduledStart": str(trip["scheduled_start"]),
                "status": trip["status"],
            },
            "passengers": passengers,
        }

    def register_push_subscription(self, token: str, endpoint: str, p256dh: str, auth: str) -> dict:
        parsed_endpoint = urlparse(endpoint)
        if parsed_endpoint.scheme != "https" or not parsed_endpoint.netloc:
            raise BadRequestError("Invalid subscription endpoint")
        if not p256dh.strip() or not auth.strip():
            raise BadRequestError("Invalid subscription keys")

        parent_link = self.dao.get_parent_link(token)
        if not parent_link:
            raise ForbiddenError("Invalid or revoked parent link")

        self.dao.upsert_push_subscription(parent_link["school_id"], parent_link["id"], endpoint, p256dh, auth)
        return {"ok": True}

    def _to_parent_safe_passenger(self, row: dict, own_student_id: str) -> dict:
        is_own_child = row["student_id"] == own_student_id
        return {
            "id": row["id"],
            "studentId": row["student_id"] if is_own_child else None,
            "studentName": row["student_name"] if is_own_child else None,
            "locationLabel": row["location_label"] if is_own_child else f"Stop {row['sequence_position']}",
            "sequencePosition": row["sequence_position"],
            "estimatedMinutesFromStart": row["estimated_minutes_from_start"],
            "status": row["status"],
        }
