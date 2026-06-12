from fastapi import APIRouter

from app.core.config import get_settings
from app.schemas.notifications import ProcessNotificationsResponse
from app.services.notification_service import NotificationService

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def get_service() -> NotificationService:
    settings = get_settings()
    simulate_sms = not (
        settings.africas_talking_api_key.strip()
        and settings.africas_talking_username.strip()
    )
    return NotificationService(simulate_sms=simulate_sms)


@router.post("/process", response_model=ProcessNotificationsResponse)
def process_notifications():
    return get_service().process_pending()
