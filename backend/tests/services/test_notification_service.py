from app.services.notification_service import NotificationService


class FakeNotificationDao:
    def __init__(self) -> None:
        self.messages = [
            {
                "id": "sms-1",
                "channel": "sms",
                "recipient_phone": "+254700000101",
                "payload": {"body": "Hello"},
                "attempts": 0,
            },
            {
                "id": "push-1",
                "channel": "push",
                "recipient_phone": None,
                "payload": {"body": "Hello"},
                "attempts": 0,
            },
        ]
        self.claims = []
        self.sent = []
        self.skipped = []
        self.failures = []

    def recover_stale_claims(self):
        return None

    def list_pending_messages(self, limit: int):
        return self.messages

    def claim_message(self, message_id: str, attempts: int):
        self.claims.append((message_id, attempts))
        return next(message for message in self.messages if message["id"] == message_id)

    def mark_sent(self, message_id: str):
        self.sent.append(message_id)

    def mark_skipped(self, message_id: str, reason: str):
        self.skipped.append((message_id, reason))

    def mark_failed_or_retry(self, message_id: str, attempts: int, error: str):
        self.failures.append((message_id, attempts, error))


class FailingSmsService(NotificationService):
    def _send_sms(self, message: dict) -> None:
        raise RuntimeError("SMS provider unavailable")


class ProviderErrorResponse:
    status = 500

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return b"provider unavailable"


class ProviderRecipientFailureResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return (
            b'{"SMSMessageData":{"Recipients":'
            b'[{"status":"Failed","statusCode":404}]}}'
        )


def test_process_notifications_sends_sms_and_skips_push_without_provider() -> None:
    dao = FakeNotificationDao()
    service = NotificationService(dao, simulate_sms=True)

    result = service.process_pending()

    assert result == {"processed": 2}
    assert dao.claims == [("sms-1", 1), ("push-1", 1)]
    assert dao.sent == ["sms-1"]
    assert dao.skipped == [("push-1", "Push delivery is not implemented in local processor")]
    assert dao.failures == []


def test_process_notifications_marks_unsupported_channels_skipped() -> None:
    dao = FakeNotificationDao()
    dao.messages = [
        {
            "id": "email-1",
            "channel": "email",
            "recipient_phone": None,
            "payload": {"body": "Hello"},
            "attempts": 1,
        }
    ]
    service = NotificationService(dao, simulate_sms=True)

    result = service.process_pending()

    assert result == {"processed": 1}
    assert dao.claims == [("email-1", 2)]
    assert dao.sent == []
    assert dao.skipped == [("email-1", "email delivery is not implemented in local processor")]
    assert dao.failures == []


def test_process_notifications_retries_failed_sms_delivery() -> None:
    dao = FakeNotificationDao()
    service = FailingSmsService(dao, simulate_sms=False)

    result = service.process_pending()

    assert result == {"processed": 2}
    assert dao.sent == []
    assert dao.skipped == [("push-1", "Push delivery is not implemented in local processor")]
    assert dao.failures == [("sms-1", 1, "SMS provider unavailable")]


def test_real_sms_sender_rejects_provider_error(monkeypatch) -> None:
    import app.services.notification_service as notification_service

    monkeypatch.setattr(
        notification_service,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "africas_talking_api_key": "api-key",
                "africas_talking_username": "school",
            },
        )(),
    )
    monkeypatch.setattr(notification_service, "urlopen", lambda request, timeout: ProviderErrorResponse())
    service = NotificationService(FakeNotificationDao(), simulate_sms=False)

    try:
        service._send_sms(
            {
                "id": "sms-1",
                "channel": "sms",
                "recipient_phone": "+254700000101",
                "payload": {"body": "Hello"},
            }
        )
    except RuntimeError as error:
        assert "Africa's Talking response 500" in str(error)
    else:
        raise AssertionError("Expected RuntimeError for provider error response")


def test_real_sms_sender_rejects_recipient_failure(monkeypatch) -> None:
    import app.services.notification_service as notification_service

    monkeypatch.setattr(
        notification_service,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "africas_talking_api_key": "api-key",
                "africas_talking_username": "school",
            },
        )(),
    )
    monkeypatch.setattr(notification_service, "urlopen", lambda request, timeout: ProviderRecipientFailureResponse())
    service = NotificationService(FakeNotificationDao(), simulate_sms=False)

    try:
        service._send_sms(
            {
                "id": "sms-1",
                "channel": "sms",
                "recipient_phone": "+254700000101",
                "payload": {"body": "Hello"},
            }
        )
    except RuntimeError as error:
        assert "Africa's Talking response 200" in str(error)
    else:
        raise AssertionError("Expected RuntimeError for recipient failure response")


def test_process_notifications_ignores_messages_claimed_elsewhere() -> None:
    dao = FakeNotificationDao()

    def claim_message(message_id: str, attempts: int):
        dao.claims.append((message_id, attempts))
        return None

    dao.claim_message = claim_message
    service = NotificationService(dao, simulate_sms=True)

    result = service.process_pending()

    assert result == {"processed": 0}
    assert dao.claims == [("sms-1", 1), ("push-1", 1)]
    assert dao.sent == []
    assert dao.skipped == []
    assert dao.failures == []


def test_notification_route_disables_simulation_when_credentials_are_configured(monkeypatch) -> None:
    import app.api.notifications as notifications

    created_services = []

    class FakeRouteService:
        def __init__(self, simulate_sms: bool = True) -> None:
            created_services.append(simulate_sms)

        def process_pending(self):
            return {"processed": 0}

    monkeypatch.setattr(
        notifications,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "africas_talking_api_key": "api-key",
                "africas_talking_username": "school",
            },
        )(),
    )
    monkeypatch.setattr(notifications, "NotificationService", FakeRouteService)

    assert notifications.process_notifications() == {"processed": 0}
    assert created_services == [False]
