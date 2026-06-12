import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import get_settings
from app.dao.notification_dao import NotificationDao


class NotificationService:
    def __init__(self, dao: NotificationDao | None = None, simulate_sms: bool = True) -> None:
        self.dao = dao or NotificationDao()
        self.simulate_sms = simulate_sms

    def process_pending(self, limit: int = 50) -> dict[str, int]:
        self.dao.recover_stale_claims()
        processed = 0

        for message in self.dao.list_pending_messages(limit):
            attempts = int(message.get("attempts") or 0) + 1
            claimed = self.dao.claim_message(message["id"], attempts)
            if not claimed:
                continue

            processed += 1
            if claimed["channel"] == "sms":
                self._process_sms(claimed, attempts)
            elif claimed["channel"] == "push":
                self.dao.mark_skipped(claimed["id"], "Push delivery is not implemented in local processor")
            else:
                self.dao.mark_skipped(
                    claimed["id"],
                    f"{claimed['channel']} delivery is not implemented in local processor",
                )

        return {"processed": processed}

    def _process_sms(self, message: dict[str, Any], attempts: int) -> None:
        try:
            self._send_sms(message)
        except Exception as error:
            self.dao.mark_failed_or_retry(message["id"], attempts, str(error))
            return

        self.dao.mark_sent(message["id"])

    def _send_sms(self, message: dict[str, Any]) -> None:
        if self.simulate_sms:
            return

        settings = get_settings()
        if not settings.africas_talking_api_key or not settings.africas_talking_username:
            raise RuntimeError("Real SMS delivery is not configured")
        if not message.get("recipient_phone"):
            raise RuntimeError("SMS delivery requires recipient_phone")

        payload = message.get("payload") or {}
        body = str(payload.get("body") or "SafeRide update from your school.")
        encoded_body = urlencode(
            {
                "username": settings.africas_talking_username,
                "to": message["recipient_phone"],
                "message": body,
            }
        ).encode()
        request = Request(
            "https://api.africastalking.com/version1/messaging",
            data=encoded_body,
            headers={
                "apiKey": settings.africas_talking_api_key,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Africa's Talking response {response.status}: {response_body[:500]}")
            if not _provider_body_is_success(response_body):
                raise RuntimeError(f"Africa's Talking response {response.status}: {response_body[:500]}")


def _provider_body_is_success(response_body: str) -> bool:
    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return True

    recipients = _provider_recipients(parsed)
    if recipients is None:
        return True
    return all(_recipient_is_success(recipient) for recipient in recipients)


def _provider_recipients(parsed_body: Any) -> list[dict[str, Any]] | None:
    if not isinstance(parsed_body, dict):
        return None

    sms_message_data = parsed_body.get("SMSMessageData")
    if isinstance(sms_message_data, dict) and isinstance(sms_message_data.get("Recipients"), list):
        return sms_message_data["Recipients"]
    if isinstance(parsed_body.get("Recipients"), list):
        return parsed_body["Recipients"]
    return None


def _recipient_is_success(recipient: Any) -> bool:
    if not isinstance(recipient, dict):
        return False

    return recipient.get("status") == "Success" or recipient.get("statusCode") in {
        101,
        102,
        "101",
        "102",
    }
