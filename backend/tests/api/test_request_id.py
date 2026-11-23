"""Request ids, the API headers, docs off and redacted error records (hardening U11).

Covers AE11 and the request-id half of the plan's U11 scenarios. Records are
checked as the Lambda runtime would emit them (tests/lambda_json_log.py), not
only as caplog text: Lambda's JSON formatter copies ``exc_info`` into
``errorMessage`` and every extra attribute into the line, so a value anywhere
in the record counts. The driver code lookup's path reaches no record with
its code (Mangum's access line, in process, and the unexpected-error line).
The last tests drive the real Lambda entry path —
Mangum, a fake API Gateway event and context, the runtime's handler on the
root logger before the app is imported — in a fresh interpreter.
"""

import asyncio
import json
import logging
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi import Body
from fastapi.testclient import TestClient
from mangum import Mangum

from app.api import _helpers
from app.api import auth as auth_api
from app.api import health as health_api
from app.core.config import get_settings
from app.core.request_context import (
    RequestLogFilter,
    configure_logging,
    loggable_path,
    request_id_for,
)
from app.main import create_app
from tests.lambda_json_log import JsonLines

BACKEND_DIR = Path(__file__).resolve().parents[2]
ORIGIN = "http://localhost:5173"
EMAIL = "redaction.probe@saferide.test"
FULL_NAME = "Wanjiru Redaction-Probe"
LAT, LNG = -1.283417, 36.817223
# The digits a coordinate leaves in any text (the GPS suite's needle rule:
# five decimals, sign stripped), plus the full values.
NEEDLES = (EMAIL, "redaction.probe", FULL_NAME, "1.28342", "36.81722", "1.283417", "36.817223")
FIVE_HEADERS = {
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
}
BODY = {"email": EMAIL, "full_name": FULL_NAME, "lat": LAT, "lng": LNG}


def _leaky(payload: dict) -> RuntimeError:
    return RuntimeError(
        f"refused {payload['email']} {payload['full_name']} at {payload['lat']},{payload['lng']}"
    )


def _route_raises(payload: dict = Body(...)):
    raise _leaky(payload)


def _route_maps(payload: dict = Body(...)):
    def act():
        raise _leaky(payload)

    return _helpers.safe_call(act)


def _route_auth_maps(payload: dict = Body(...)):
    def act():
        raise _leaky(payload)

    return auth_api.safe_call(act)


def _route_db_error(payload: dict = Body(...)):
    # What a psycopg error's text looks like: the failing row in DETAIL.
    raise psycopg.errors.UniqueViolation(
        'duplicate key value violates unique constraint "app_users_email_key"\n'
        f"DETAIL:  Key (lower(email))=({payload['email']}) already exists."
    )


def _route_wrapped_db_error(payload: dict = Body(...)):
    def act():
        try:
            raise psycopg.errors.RaiseException(f"probe {payload['email']}")
        except psycopg.Error as error:
            raise ValueError(f"wrapped {payload['email']}") from error

    return _helpers.safe_call(act)


def _app():
    app = create_app()
    app.add_api_route("/api/test-only/raises", _route_raises, methods=["POST"])
    app.add_api_route("/api/test-only/maps", _route_maps, methods=["POST"])
    app.add_api_route("/api/test-only/auth-maps", _route_auth_maps, methods=["POST"])
    app.add_api_route("/api/test-only/db-error", _route_db_error, methods=["POST"])
    app.add_api_route("/api/test-only/wrapped", _route_wrapped_db_error, methods=["POST"])
    return app


@pytest.fixture
def client() -> TestClient:
    # raise_server_exceptions stays on: a test passes only if no exception
    # escapes the app (the catch-all answers it instead).
    return TestClient(_app())


@pytest.fixture
def json_log():
    """The runtime's handler on the root logger, with the filter the app's
    import installs on it inside Lambda (configure_logging, in_lambda=True)."""
    root = logging.getLogger()
    handler = JsonLines()
    handler.addFilter(RequestLogFilter())
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.fixture(autouse=True)
def _health_db_ok(monkeypatch):
    # No database in the unit suite: health answers from a stub.
    health_api.reset_cache()
    monkeypatch.setattr(health_api, "_database_answers", lambda: True)
    yield
    health_api.reset_cache()


def _assert_clean(text: str) -> None:
    for needle in NEEDLES:
        assert needle not in text, f"{needle!r} leaked into a record: {text}"


def _unexpected(handler: JsonLines) -> list[dict]:
    return [r for r in handler.records() if r["message"].startswith("unexpected-error")]


# --- AE11 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/api/test-only/raises", "/api/test-only/maps", "/api/test-only/auth-maps"]
)
def test_an_unexpected_error_answers_500_with_one_id_in_body_header_and_record(
    client, json_log, path
):
    response = client.post(path, json=BODY)

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert response.json() == {"detail": "Unexpected backend error", "request_id": request_id}
    records = _unexpected(json_log)
    assert len(records) == 1, json_log.text()
    record = records[0]
    assert record["level"] == "ERROR"
    assert record["logger"] == "saferide.errors"
    assert record["request_id"] == request_id
    assert f"request_id={request_id}" in record["message"]
    assert f"method=POST path={path} " in record["message"]
    assert "error=RuntimeError" in record["message"]
    # The frames, never the message: no errorMessage/errorType from exc_info.
    assert "errorMessage" not in record and "stackTrace" not in record
    # The innermost frame is the raise in this file's route.
    assert "test_request_id.py" in record["traceback"][-1]
    _assert_clean(json_log.text())
    for header, value in FIVE_HEADERS.items():
        assert response.headers[header] == value


def test_a_database_error_logs_its_sqlstate_and_never_its_text(client, json_log):
    response = client.post("/api/test-only/db-error", json=BODY)

    assert response.status_code == 500
    (record,) = _unexpected(json_log)
    assert "error=UniqueViolation sqlstate=23505" in record["message"]
    _assert_clean(json_log.text())


def test_a_wrapped_database_error_reports_the_cause_state_not_its_message(client, json_log):
    # The fallthrough's own path: ValueError from a RaiseException (P0001),
    # whose primary message is free text — here carrying the email.
    response = client.post("/api/test-only/wrapped", json=BODY)

    assert response.status_code == 500
    (record,) = _unexpected(json_log)
    assert "error=ValueError sqlstate=P0001" in record["message"]
    _assert_clean(json_log.text())


def test_mapped_errors_stay_quiet(json_log):
    # A unique violation through map_error is a 409, not an unexpected error.
    def act():
        raise psycopg.errors.UniqueViolation(f"Key (email)=({EMAIL}) already exists.")

    assert _helpers.map_error(_capture(act)).status_code == 409
    assert auth_api.map_error(_capture(act)).status_code == 409
    assert _unexpected(json_log) == []
    _assert_clean(json_log.text())


def _capture(action):
    try:
        action()
    except Exception as error:  # noqa: BLE001
        return error
    raise AssertionError("expected the action to raise")


def test_the_filter_strips_exception_text_from_any_logger_exception_record(json_log):
    # The DAOs' own `logger.exception(...)` calls (position_dao, run_dao…):
    # the filter on the runtime's handler keeps the class and frames only.
    logger = logging.getLogger("saferide.dao.probe")
    try:
        raise psycopg.errors.CheckViolation(
            'new row for relation "run_positions" violates check constraint\n'
            f"DETAIL:  Failing row contains ({LAT}, {LNG})."
        )
    except psycopg.Error:
        logger.exception("could not flag trail row %s", "row-1")

    (record,) = json_log.records()
    assert record["message"] == "could not flag trail row row-1"
    assert record["error_class"] == "CheckViolation"
    assert record["sqlstate"] == "23514"
    assert record["traceback"]
    assert "errorMessage" not in record
    _assert_clean(json_log.text())


def test_without_the_filter_the_runtime_shape_would_leak(json_log):
    # The hazard, so the redaction tests cannot pass vacuously: the same
    # record through the stand-in without the filter carries the row.
    bare = JsonLines()
    logger = logging.getLogger("saferide.dao.probe-bare")
    logger.addHandler(bare)
    logger.propagate = False
    try:
        try:
            raise RuntimeError(f"refused {EMAIL}")
        except RuntimeError:
            logger.exception("probe")
    finally:
        logger.removeHandler(bare)
        logger.propagate = True
    assert EMAIL in bare.text()
    assert bare.records()[0]["errorMessage"] == f"refused {EMAIL}"


# --- request ids -------------------------------------------------------------


def test_outside_lambda_each_request_gets_a_fresh_uuid4(client):
    first = client.get("/api/health").headers["x-request-id"]
    second = client.get("/api/health").headers["x-request-id"]

    assert uuid.UUID(first).version == 4
    assert uuid.UUID(second).version == 4
    assert first != second


def test_an_inbound_request_id_is_never_echoed_or_logged(client, json_log):
    planted = "planted-id-0001"
    response = client.post(
        "/api/test-only/raises", json=BODY, headers={"X-Request-Id": planted}
    )

    assert response.headers["x-request-id"] != planted
    assert response.json()["request_id"] != planted
    assert planted not in json_log.text()


def _with_lambda_context(app, request_id):
    async def wrapped(scope, receive, send):
        scope["aws.context"] = SimpleNamespace(aws_request_id=request_id)
        await app(scope, receive, send)

    return wrapped


def test_inside_lambda_the_invocation_id_is_the_request_id(json_log):
    lambda_id = "8f5a3c2e-6b1d-4e7a-9c0f-1a2b3c4d5e6f"
    client = TestClient(_with_lambda_context(_app(), lambda_id))

    ok = client.get("/api/health")
    failed = client.post("/api/test-only/raises", json=BODY)

    assert ok.headers["x-request-id"] == lambda_id
    assert failed.headers["x-request-id"] == lambda_id
    assert failed.json()["request_id"] == lambda_id
    (record,) = _unexpected(json_log)
    assert record["request_id"] == lambda_id


def test_a_malformed_lambda_id_never_reaches_a_header():
    scope = {"aws.context": SimpleNamespace(aws_request_id="abc\r\nSet-Cookie: x=1")}
    assert uuid.UUID(request_id_for(scope)).version == 4
    assert uuid.UUID(request_id_for({})).version == 4


def test_records_outside_a_request_carry_a_dash(json_log):
    logging.getLogger("saferide.probe").info("cold start")
    assert json_log.records()[0]["request_id"] == "-"


# --- headers on every response kind -------------------------------------------


def _assert_headers(response) -> None:
    assert uuid.UUID(response.headers["x-request-id"])
    for header, value in FIVE_HEADERS.items():
        assert response.headers.get(header) == value, (response.status_code, header)


def test_every_response_kind_carries_the_id_and_the_four_headers(client, monkeypatch):
    responses = {
        "200 health": client.get("/api/health"),
        "404 unknown path": client.get("/api/no-such-route"),
        "405 wrong method": client.delete("/api/health"),
        "401 no session": client.get("/api/auth/me"),
        "422 bad body": client.post("/api/auth/login", json={"email": 3}),
        "500 catch-all": client.post("/api/test-only/raises", json=BODY),
        "500 map_error": client.post("/api/test-only/maps", json=BODY),
        "preflight allowed": client.options(
            "/api/auth/login",
            headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
        ),
        "preflight refused": client.options(
            "/api/auth/login",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        ),
    }
    health_api.reset_cache()
    monkeypatch.setattr(health_api, "_database_answers", lambda: False)
    responses["503 health"] = client.get("/api/health")

    expected = {
        "200 health": 200, "404 unknown path": 404, "405 wrong method": 405,
        "401 no session": 401, "422 bad body": 422, "500 catch-all": 500,
        "500 map_error": 500, "preflight allowed": 200, "preflight refused": 400,
        "503 health": 503,
    }
    for kind, response in responses.items():
        assert response.status_code == expected[kind], (kind, response.text)
        _assert_headers(response)


def test_the_browser_can_read_the_id_even_on_the_catch_all_500(client):
    # The catch-all answers inside CORSMiddleware: a cross-origin 500 without
    # Access-Control-Allow-Origin would reach the app as "Failed to fetch".
    responses = [
        client.post("/api/test-only/raises", json=BODY, headers={"Origin": ORIGIN}),
        client.post("/api/test-only/maps", json=BODY, headers={"Origin": ORIGIN}),
        client.get("/api/health", headers={"Origin": ORIGIN}),
    ]
    for response in responses:
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert "X-Request-Id" in response.headers["access-control-expose-headers"]


class _RaisesAboveEverything:
    """A middleware added after create_app, so outside RequestContext: what
    the backstop exception handler is for."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == "/api/health":
            raise RuntimeError(f"middleware refused {EMAIL}")
        await self.app(scope, receive, send)


def test_the_backstop_handler_keeps_the_contract_above_the_middleware(json_log):
    app = _app()
    app.add_middleware(_RaisesAboveEverything)
    # Starlette re-raises after its handler answers; the response is what counts.
    response = TestClient(app, raise_server_exceptions=False).get("/api/health")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert uuid.UUID(request_id).version == 4
    assert response.json() == {"detail": "Unexpected backend error", "request_id": request_id}
    for header, value in FIVE_HEADERS.items():
        assert response.headers[header] == value
    (record,) = _unexpected(json_log)
    assert record["request_id"] == request_id
    _assert_clean(json_log.text())


# --- docs off outside local --------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_404_in_production(monkeypatch, path):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    response = TestClient(create_app()).get(path)

    assert response.status_code == 404
    _assert_headers(response)


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_served_locally(monkeypatch, path):
    monkeypatch.setattr(get_settings(), "app_env", "local")
    response = TestClient(create_app()).get(path)

    assert response.status_code == 200
    assert uuid.UUID(response.headers["x-request-id"])
    if path == "/openapi.json":
        assert response.headers["content-security-policy"] == FIVE_HEADERS["content-security-policy"]
    else:
        # The two HTML pages load their scripts from a CDN; locally only.
        assert "content-security-policy" not in response.headers


# --- LOG_LEVEL -----------------------------------------------------------------


@pytest.fixture
def bare_root():
    """The root logger with no handlers for the duration of the test body.

    pytest attaches its capture handlers to the root for the call phase,
    after fixtures run, so the strip happens on first use, inside the test."""
    root = logging.getLogger()
    saved_level = root.level
    saved: list[logging.Handler] = []

    def strip() -> logging.Logger:
        for handler in list(root.handlers):
            saved.append(handler)
            root.removeHandler(handler)
        return root

    yield strip
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in saved:
        root.addHandler(handler)
    root.setLevel(saved_level)


def test_log_level_sets_the_root_level_and_a_stream_handler_off_lambda(bare_root):
    root = bare_root()
    configure_logging("debug", in_lambda=False)

    assert root.level == logging.DEBUG
    (handler,) = root.handlers
    assert any(isinstance(f, RequestLogFilter) for f in handler.filters)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING  # D20 stays

    configure_logging("debug", in_lambda=False)  # idempotent
    assert len(root.handlers) == 1
    configure_logging("no-such-level", in_lambda=False)
    assert root.level == logging.INFO


def test_off_lambda_handlers_someone_else_installed_are_left_alone(bare_root):
    root = bare_root()
    theirs = JsonLines()
    root.addHandler(theirs)

    configure_logging("INFO", in_lambda=False)

    assert root.handlers == [theirs]
    assert theirs.filters == []


def test_inside_lambda_the_level_and_handlers_stay_the_runtimes(bare_root):
    root = bare_root()
    runtime = JsonLines()
    root.addHandler(runtime)
    root.setLevel(logging.WARNING)

    configure_logging("DEBUG", in_lambda=True)
    configure_logging("DEBUG", in_lambda=True)

    assert root.level == logging.WARNING
    assert root.handlers == [runtime]
    assert sum(isinstance(f, RequestLogFilter) for f in runtime.filters) == 1


def test_log_level_defaults_to_info():
    from app.core.config import Settings

    assert Settings.model_fields["log_level"].default == "INFO"
    assert Settings.model_fields["app_version"].default == "dev"


# --- the driver code lookup's path ---------------------------------------------
# GET /api/auth/schools/{code} carries the school's driver code, which resolves
# to the school id for PIN sign-in: no record names it, in any typed form.

KNOWN_CODE, UNKNOWN_CODE, FAILING_CODE = "GFA07K1Q", "WXYZ8M3N", "HJKM5P6R"
# Each code's halves, compared case-insensitively: a typed code may be split
# by a hyphen, a space or a slash (Mangum unquotes the path). Every half holds
# a letter outside hex, so no timestamp or request id can match one.
CODE_NEEDLES = tuple(
    code[start:start + 4].lower()
    for code in (KNOWN_CODE, UNKNOWN_CODE, FAILING_CODE)
    for start in (0, 4)
)
LOOKUP_SCHOOL_ID = "5cae0000-0000-0000-0000-000000000001"


class _LookupDao:
    def school_by_driver_code(self, code):
        if code == FAILING_CODE:
            raise RuntimeError("lookup failed")
        if code == KNOWN_CODE:
            return {"id": LOOKUP_SCHOOL_ID, "name": "Probe School"}
        return None


class _LambdaContext:
    aws_request_id = "5e2d7c3b-9a4f-4b1e-8d0c-6f5e4d3c2b1a"
    function_name = "saferide-api"
    memory_limit_in_mb = 512
    invoked_function_arn = "arn:aws:lambda:af-south-1:000000000000:function:saferide-api"

    def get_remaining_time_in_millis(self):
        return 30_000


def _through_mangum(app, requests: list[tuple[str, str]]) -> list[dict]:
    """Each (method, raw path) through the real Mangum adapter as an HTTP API
    (payload v2) event, in process; returns Mangum's responses."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        handler = Mangum(app, lifespan="off")
        return [
            handler(
                {
                    "version": "2.0", "routeKey": "$default", "rawPath": path,
                    "rawQueryString": "", "headers": {"host": "api.example.test"},
                    "requestContext": {
                        "http": {"method": method, "path": path, "protocol": "HTTP/1.1",
                                 "sourceIp": "203.0.113.9", "userAgent": "probe"},
                        "requestId": "apigw-1", "stage": "$default", "routeKey": "$default",
                    },
                    "isBase64Encoded": False,
                },
                _LambdaContext(),
            )
            for method, path in requests
        ]
    finally:
        asyncio.set_event_loop(None)
        loop.close()


@pytest.mark.parametrize(
    "path,logged",
    [
        ("/api/auth/schools/GFA07K1Q", "/api/auth/schools/{code}"),
        ("/api/auth/schools/gfa0 7k1q", "/api/auth/schools/{code}"),
        ("/api/auth/schools/GFA0/7K1Q", "/api/auth/schools/{code}"),
        ("/api/auth/schools/", "/api/auth/schools/"),
        ("/api/auth/schoolsGFA07K1Q", "/api/auth/schoolsGFA07K1Q"),
        ("/api/health", "/api/health"),
        (None, None),
    ],
)
def test_only_the_lookup_path_is_logged_as_its_route_template(path, logged):
    assert loggable_path(path) == logged


def test_the_driver_code_lookup_logs_no_code_and_its_access_line_keeps_the_status(
    json_log, monkeypatch
):
    monkeypatch.setattr(auth_api.service, "dao", _LookupDao())
    # The hazard, so the test cannot pass vacuously: a handler on Mangum's
    # own logger formats each record before the root handler's filter runs.
    bare = JsonLines()
    access_logger = logging.getLogger("mangum.http")
    access_logger.addHandler(bare)
    try:
        responses = _through_mangum(_app(), [
            ("GET", "/api/auth/schools/gfa0-7k1q"),
            ("GET", "/api/auth/schools/WXYZ-8M3N"),
            ("GET", f"/api/auth/schools/{FAILING_CODE}"),
            ("GET", "/api/auth/schools/GFA0%2F7K1Q"),
        ])
    finally:
        access_logger.removeHandler(bare)

    statuses = [200, 404, 500, 404]
    assert [response["statusCode"] for response in responses] == statuses
    assert json.loads(responses[0]["body"]) == {"schoolId": LOOKUP_SCHOOL_ID, "name": "Probe School"}
    access = [r["message"] for r in json_log.records() if r["logger"] == "mangum.http"]
    # The route template in place of the path; the status stays the last word
    # (the ApiClientErrorsFilter metric filter reads it there).
    assert access == [f"GET /api/auth/schools/{{code}} {status}" for status in statuses]
    (record,) = _unexpected(json_log)
    assert "method=GET path=/api/auth/schools/{code} " in record["message"]
    text = json_log.text().lower()
    for needle in CODE_NEEDLES:
        assert needle not in text, f"{needle!r} leaked into a record: {text}"
    raw = bare.text().lower()
    assert "gfa0-7k1q" in raw and "wxyz-8m3n" in raw and "gfa0/7k1q" in raw


# --- the real Lambda entry path ----------------------------------------------

_CHILD = r"""
import json, logging, os, sys
sys.path.insert(0, os.getcwd())
from tests.lambda_json_log import JsonLines

# What the runtime does before importing the handler module.
runtime = JsonLines()
root = logging.getLogger()
root.addHandler(runtime)
root.setLevel(logging.INFO)

from fastapi import Body
from app.api import health
from app.lambda_handler import handler
from app.main import app

def leaky(payload: dict = Body(...)):
    raise RuntimeError(f"refused {payload['email']} at {payload['lat']},{payload['lng']}")

app.add_api_route("/api/test-only/raises", leaky, methods=["POST"])
health._database_answers = lambda: True

def event(method, path, body=None, headers=None):
    return {
        "version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": "",
        "headers": {"host": "api.example.test", "content-type": "application/json",
                    **(headers or {})},
        "requestContext": {
            "http": {"method": method, "path": path, "protocol": "HTTP/1.1",
                     "sourceIp": "203.0.113.9", "userAgent": "probe"},
            "requestId": "apigw-1", "stage": "$default", "routeKey": "$default",
        },
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }

class Context:
    def __init__(self, request_id):
        self.aws_request_id = request_id
        self.function_name = "saferide-api"
        self.memory_limit_in_mb = 512
        self.invoked_function_arn = "arn:aws:lambda:af-south-1:000000000000:function:saferide-api"
    def get_remaining_time_in_millis(self):
        return 30000

body = json.loads(sys.argv[1])
failed = handler(
    event("POST", "/api/test-only/raises", body, {"x-request-id": "planted-id-0002"}),
    Context(sys.argv[2]),
)
ok = handler(event("GET", "/api/health"), Context(sys.argv[3]))
print(json.dumps({"failed": failed, "ok": ok, "lines": runtime.lines,
                  "root_level": root.level}))
"""


@pytest.fixture(scope="module")
def lambda_run():
    failed_id = "0b7e6c1a-2f3d-4c5b-8a9e-7d6c5b4a3f21"
    ok_id = "1c8f7d2b-3e4a-4d6c-9b0f-8e7d6c5b4a32"
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, json.dumps(BODY), failed_id, ok_id],
        cwd=BACKEND_DIR,
        env={
            **_child_env(),
            "AWS_LAMBDA_FUNCTION_NAME": "saferide-api",
            "APP_ENV": "production",
            "LOG_LEVEL": "DEBUG",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    result["failed_id"], result["ok_id"] = failed_id, ok_id
    return result


def _child_env() -> dict:
    import os

    keep = ("PATH", "HOME", "LANG", "SYSTEMROOT", "TMPDIR")
    return {key: os.environ[key] for key in keep if key in os.environ}


def test_through_mangum_the_500_carries_the_invocation_id(lambda_run):
    failed = lambda_run["failed"]
    assert failed["statusCode"] == 500
    assert failed["headers"]["x-request-id"] == lambda_run["failed_id"]
    assert json.loads(failed["body"]) == {
        "detail": "Unexpected backend error",
        "request_id": lambda_run["failed_id"],
    }
    for header, value in FIVE_HEADERS.items():
        assert failed["headers"][header] == value
    ok = lambda_run["ok"]
    assert ok["statusCode"] == 200
    assert ok["headers"]["x-request-id"] == lambda_run["ok_id"]


def test_through_mangum_one_record_carries_the_id_and_nothing_from_the_body(lambda_run):
    records = [json.loads(line) for line in lambda_run["lines"]]
    unexpected = [r for r in records if r["message"].startswith("unexpected-error")]
    assert len(unexpected) == 1, lambda_run["lines"]
    assert unexpected[0]["request_id"] == lambda_run["failed_id"]
    assert "path=/api/test-only/raises" in unexpected[0]["message"]
    # Mangum never sees the exception, so it adds no exception record of its own.
    assert not any("errorMessage" in r or "errorType" in r for r in records)
    text = "\n".join(lambda_run["lines"])
    _assert_clean(text)
    assert "planted-id-0002" not in text


def test_through_mangum_the_runtime_level_is_left_alone(lambda_run):
    # LOG_LEVEL=DEBUG was set; inside Lambda the runtime's INFO stands.
    assert lambda_run["root_level"] == logging.INFO
