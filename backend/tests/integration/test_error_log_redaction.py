"""Real database errors leave no row values in any record (hardening U11).

Run with the local stack up:

    RUN_INTEGRATION=1 ../.venv/bin/python -m pytest tests/integration/test_error_log_redaction.py -q

The app runs in process against the local database (``in_process_db``) with
test-only routes that provoke the two errors the plan names — a unique
violation on the email index and a check violation on a position row — plus
a PL/pgSQL ``raise`` whose primary message carries an email. A psycopg error's
text includes the failing row (``Key (email)=(…)``, ``Failing row contains
(…, lat, lng, …)``), so each record is checked as the Lambda runtime would
emit it (tests/lambda_json_log.py) and as caplog text: no email address and
no coordinate anywhere.
"""

import logging
import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from conftest import DSN, purge_accounts

from app.api import _helpers
from app.api import auth as auth_api
from app.core.db import get_global_connection
from app.core.request_context import RequestLogFilter
from app.main import create_app
from tests.lambda_json_log import JsonLines

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

MARKER = uuid.uuid4().hex[:8]
EMAIL = f"it-redaction-{MARKER}@saferide.test"
LAT, LNG = -1.2345671, 36.8765431
# Coordinate needles (the GPS suite's rule: five decimals, sign stripped) and
# the values as PostgreSQL prints a double in DETAIL.
NEEDLES = (EMAIL, f"it-redaction-{MARKER}", "1.23457", "36.87654", "1.2345671", "36.8765431")


def _insert_duplicate_email() -> None:
    with get_global_connection() as connection:
        connection.execute(
            "insert into app_users (email, password_hash, full_name) values (%s, 'x', 'Probe')",
            (EMAIL,),
        )


def _insert_bad_position() -> None:
    # The CHECK on source fires before the foreign keys are looked at, so the
    # throwaway ids never need to exist; DETAIL lists the whole row.
    with get_global_connection() as connection:
        connection.execute(
            "insert into run_positions (school_id, run_id, bus_id, source, lat, lng, accuracy_m) "
            "values (%s, %s, %s, 'not-a-source', %s, %s, 12.5)",
            (str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), LAT, LNG),
        )


def _raise_with_email() -> None:
    # A DO block takes no bind parameters; EMAIL is the test's own constant.
    # Without parameters psycopg sends the text as is, so % is PL/pgSQL's.
    with get_global_connection() as connection:
        connection.execute(f"do $$ begin raise exception 'refused %', '{EMAIL}'; end $$")


ROUTES = {
    "/api/test-only/email-raw": _insert_duplicate_email,
    "/api/test-only/email-mapped": lambda: _helpers.safe_call(_insert_duplicate_email),
    "/api/test-only/email-auth-mapped": lambda: auth_api.safe_call(_insert_duplicate_email),
    "/api/test-only/position-raw": _insert_bad_position,
    "/api/test-only/position-mapped": lambda: _helpers.safe_call(_insert_bad_position),
    "/api/test-only/raise-mapped": lambda: _helpers.safe_call(_raise_with_email),
}


@pytest.fixture(scope="module")
def account(in_process_db):
    with psycopg.connect(DSN, autocommit=True) as pg:
        user_id = str(
            pg.execute(
                "insert into app_users (email, password_hash, full_name) "
                "values (%s, 'x', 'IT Redaction Probe') returning id",
                (EMAIL,),
            ).fetchone()[0]
        )
    yield user_id
    purge_accounts(user_id)


@pytest.fixture(scope="module")
def client(account):
    app = create_app()
    for path, action in ROUTES.items():
        app.add_api_route(path, action, methods=["POST"])
    return TestClient(app)


@pytest.fixture
def json_log():
    root = logging.getLogger()
    handler = JsonLines()
    handler.addFilter(RequestLogFilter())
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous)


def _assert_clean(json_log: JsonLines, caplog) -> None:
    texts = [json_log.text()] + [record.getMessage() for record in caplog.records]
    for text in texts:
        for needle in NEEDLES:
            assert needle not in text, f"{needle!r} leaked: {text}"


def _unexpected(json_log: JsonLines) -> list[dict]:
    return [r for r in json_log.records() if r["message"].startswith("unexpected-error")]


def test_the_errors_really_carry_the_values(account, in_process_db):
    # The hazard, so the redaction checks cannot pass vacuously.
    with pytest.raises(psycopg.errors.UniqueViolation) as unique:
        _insert_duplicate_email()
    assert EMAIL in str(unique.value)
    with pytest.raises(psycopg.errors.CheckViolation) as check:
        _insert_bad_position()
    assert "36.8765431" in str(check.value)
    with pytest.raises(psycopg.errors.RaiseException) as raised:
        _raise_with_email()
    assert EMAIL in raised.value.diag.message_primary


def test_a_unique_violation_on_the_email_index_leaves_no_email(client, json_log, caplog):
    with caplog.at_level(logging.INFO):
        response = client.post("/api/test-only/email-raw", json={"email": EMAIL})

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert response.json() == {"detail": "Unexpected backend error", "request_id": request_id}
    (record,) = _unexpected(json_log)
    assert record["request_id"] == request_id
    assert (
        "error=UniqueViolation sqlstate=23505 constraint=app_users_email_key table=app_users"
        in record["message"]
    )
    _assert_clean(json_log, caplog)


@pytest.mark.parametrize("path", ["/api/test-only/email-mapped", "/api/test-only/email-auth-mapped"])
def test_the_mapped_unique_violation_is_a_quiet_409(client, json_log, caplog, path):
    with caplog.at_level(logging.INFO):
        response = client.post(path, json={"email": EMAIL})

    assert response.status_code == 409
    assert _unexpected(json_log) == []
    _assert_clean(json_log, caplog)


def test_a_check_violation_on_a_position_row_leaves_no_coordinate(client, json_log, caplog):
    with caplog.at_level(logging.INFO):
        response = client.post("/api/test-only/position-raw", json={"lat": LAT, "lng": LNG})

    assert response.status_code == 500
    (record,) = _unexpected(json_log)
    assert (
        "error=CheckViolation sqlstate=23514 constraint=run_positions_source_check "
        "table=run_positions" in record["message"]
    )
    _assert_clean(json_log, caplog)


def test_the_mapped_check_violation_is_a_quiet_400(client, json_log, caplog):
    with caplog.at_level(logging.INFO):
        response = client.post("/api/test-only/position-mapped", json={"lat": LAT, "lng": LNG})

    assert response.status_code == 400
    assert _unexpected(json_log) == []
    _assert_clean(json_log, caplog)


def test_the_fallthrough_never_logs_the_primary_message(client, json_log, caplog):
    # P0001 is unmapped: map_error's fallthrough logs it. Its primary message
    # is free text (here the email), so only the state is kept.
    with caplog.at_level(logging.INFO):
        response = client.post("/api/test-only/raise-mapped", json={})

    assert response.status_code == 500
    (record,) = _unexpected(json_log)
    assert "error=RaiseException sqlstate=P0001" in record["message"]
    assert record["request_id"] == response.json()["request_id"]
    _assert_clean(json_log, caplog)


def test_a_dao_logger_exception_of_a_position_error_keeps_no_coordinate(
    account, in_process_db, json_log, caplog
):
    # The DAOs' `logger.exception("could not flag trail row %s", …)` shape:
    # the filter on the runtime's handler strips the text, keeps the names.
    logger = logging.getLogger("saferide.dao.redaction-probe")
    with caplog.at_level(logging.INFO):
        try:
            _insert_bad_position()
        except psycopg.Error:
            logger.exception("could not flag trail row %s", "probe")

    (record,) = [r for r in json_log.records() if r["logger"] == logger.name]
    assert record["error_class"] == "CheckViolation"
    assert record["sqlstate"] == "23514"
    assert record["constraint"] == "run_positions_source_check"
    assert "errorMessage" not in record
    for needle in NEEDLES:
        assert needle not in json_log.text()
