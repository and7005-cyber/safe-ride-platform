"""The API log metric filters match the lines the code really writes (hardening U12).

infra/backend/template.yaml counts five kinds of /saferide/api line into
SafeRide metrics. A filter whose pattern drifts from the code's log format
counts nothing, and its alarm stays OK forever, so each pattern is checked
here against lines produced by the real code paths — Mangum's access line,
``MailService.send`` with a failing transport, ``AuthService`` refusing PIN
sign-ins and capping mail — formatted as the Lambda runtime formats them under
``LogFormat: JSON`` (tests/lambda_json_log.py, with U11's request-id filter on
the handler as in production).

The matcher implements the subset of CloudWatch Logs' JSON filter syntax the
template uses, as documented ("Filter pattern syntax for metric filters"):
``{ (cond) && (cond) }`` where a condition is ``$.field = "value"`` — a
whole-value match in which ``*`` is a wildcard — or ``$.field = %regex%``, a
regex found anywhere in the value, restricted to CloudWatch's regex
characters (no parentheses, no spaces). ``aws logs test-metric-filter`` is the
authority: infra/README.md lists one command per filter with the sample
lines below, run before the deploy, and the last test keeps those commands
in step with the template.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex

import pytest
from mangum import Mangum

from app.core.request_context import RequestLogFilter
from app.services.auth_service import (
    PIN_LOCK_MAX_FAILURES,
    AuthService,
    PinLoginError,
    SchoolPinLockedError,
)
from app.services.mail_service import MAIL_CAP_PER_HOUR, MailMessage, MailService
from tests.core.template_text import REPO, load, unquote
from tests.lambda_json_log import JsonLines

T = load("backend")
FILTERS = {lid: unquote(T.properties(lid)["FilterPattern"]) for lid in T.of_type("AWS::Logs::MetricFilter")}
README = (REPO / "infra" / "README.md").read_text(encoding="utf-8")

SCHOOL = "5cae0000-0000-0000-0000-000000000001"
SCHOOL_CODE = "GFA-001"
DRIVER_CODE = "GFA07K1Q"


# --- the matcher ------------------------------------------------------------------

_CONDITION = re.compile(r'\(\s*\$\.(\w+)\s*(=|!=)\s*("(?:[^"\\]|\\.)*"|%[^%]+%)\s*\)')
# CloudWatch regex: alphanumerics, these symbols and these operators only.
_REGEX_CHARS = re.compile(r"^[A-Za-z0-9:_#=@/;,\-^$?\[\]{}|\\*+.]+$")


def parse(pattern: str) -> list[tuple[str, str, str]]:
    text = pattern.strip()
    assert text.startswith("{") and text.endswith("}"), pattern
    assert "||" not in text, "the matcher implements && only"
    conditions = []
    for part in text[1:-1].split("&&"):
        match = _CONDITION.fullmatch(part.strip())
        assert match, f"unsupported condition {part!r} in {pattern}"
        field, operator, value = match.groups()
        if value.startswith("%"):
            regex = value[1:-1]
            assert _REGEX_CHARS.match(regex), f"regex outside CloudWatch's character set: {regex}"
            assert "(" not in regex and ")" not in regex
        conditions.append((field, operator, value))
    regexes = sum(value.startswith("%") for _, _, value in conditions)
    assert regexes <= 2, "CloudWatch allows two regexes per pattern"
    return conditions


def _value_matches(value: str, expected: str) -> bool:
    if expected.startswith("%"):
        return re.search(expected[1:-1], value) is not None
    glob = expected[1:-1]
    return re.fullmatch(".*".join(re.escape(piece) for piece in glob.split("*")), value) is not None


def matches(pattern: str, line: str) -> bool:
    record = json.loads(line)
    for field, operator, expected in parse(pattern):
        value = record.get(field)
        hit = isinstance(value, str) and _value_matches(value, expected)
        if hit != (operator == "="):
            return False
    return True


def test_the_matcher_follows_the_documented_semantics():
    line = json.dumps({"message": "UpdateTrail", "logger": "x"})
    assert matches('{ ($.message = "UpdateTrail") }', line)
    assert not matches('{ ($.message = "Update") }', line)       # whole value
    assert matches('{ ($.message = "Update*") }', line)          # wildcard
    assert matches("{ ($.message = %Trail%) }", line)            # regex: found anywhere
    assert not matches("{ ($.message = %^Trail%) }", line)
    assert matches('{ ($.message = %Trail$%) && ($.logger = "x") }', line)
    assert not matches('{ ($.message = %Trail$%) && ($.logger = "y") }', line)
    assert matches('{ ($.message != "Other") }', line)
    with pytest.raises(AssertionError):
        parse("{ ($.message = %(Trail)%) }")


# --- real lines -----------------------------------------------------------------


@pytest.fixture
def runtime_log():
    """The runtime's JSON handler on the root logger at INFO, with U11's
    request-id filter on it (configure_logging does that inside Lambda)."""
    handler = JsonLines()
    handler.addFilter(RequestLogFilter())
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


class SesRejected(Exception):
    """Shaped like botocore's ClientError: the code lives in ``response``."""

    def __init__(self):
        super().__init__("Email address is not verified: parent@saferide.test")
        self.response = {"Error": {"Code": "MessageRejected", "Message": "parent@saferide.test"}}


class FailingTransport:
    def send(self, message: MailMessage) -> None:
        raise SesRejected()


class AuthDaoStub:
    """What pin_login and forgot_password read: one school with one driver
    (whose PIN is never guessed here), the failure window, the token cap."""

    def __init__(self):
        self.failures = 0
        self.tokens = 0

    def school_for_pin_login(self, school_id):
        return {"id": SCHOOL, "code": SCHOOL_CODE} if school_id == SCHOOL else None

    def pin_failures_since(self, school_id, source, window_minutes, limit):
        over = self.failures > limit
        return {"failures": self.failures, "retry_after_s": 60.0 if over else None}

    def record_pin_failure(self, school_id, source):
        self.failures += 1

    def driver_members_for_school(self, school_id):
        return [{"id": "u-driver", "pin_hash": "v2:k1:never-matches"}]

    def get_user_by_email(self, email):
        return {"id": "u-parent", "email": email, "disabled_at": None}

    def create_reset_token(self, user_id, token_hash, purpose, ttl_minutes, *,
                           cap_per_hour=None, void_earlier=False):
        self.tokens += 1
        return self.tokens <= cap_per_hour


def _mangum_lines(runtime_log, cases: list[tuple[str, str, int]]) -> dict[tuple[str, str, int], str]:
    """One request per (method, path, status) through the real Mangum
    adapter, answered by a bare ASGI app with that status; returns each
    request's access line."""
    answer = {"status": 200}

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": answer["status"], "headers": []})
        await send({"type": "http.response.body", "body": b""})

    class Context:
        aws_request_id = "3d1c6f2a-8b4e-4f0a-9c2d-5e6f7a8b9c0d"
        function_name = "saferide-api"
        memory_limit_in_mb = 512
        invoked_function_arn = "arn:aws:lambda:af-south-1:000000000000:function:saferide-api"

        def get_remaining_time_in_millis(self):
            return 30_000

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    lines = {}
    try:
        handler = Mangum(app, lifespan="off")
        for method, path, status in cases:
            answer["status"] = status
            before = len(runtime_log.lines)
            handler(
                {
                    "version": "2.0", "routeKey": "$default", "rawPath": path, "rawQueryString": "",
                    "headers": {"host": "api.example.test"},
                    "requestContext": {
                        "http": {"method": method, "path": path, "protocol": "HTTP/1.1",
                                 "sourceIp": "203.0.113.9", "userAgent": "probe"},
                        "requestId": "apigw-1", "stage": "$default", "routeKey": "$default",
                    },
                    "isBase64Encoded": False,
                },
                Context(),
            )
            (line,) = [
                line for line in runtime_log.lines[before:]
                if json.loads(line)["logger"] == "mangum.http"
            ]
            lines[(method, path, status)] = line
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    return lines


ACCESS_CASES = [
    ("GET", "/api/health", 200),
    ("POST", "/api/auth/signup", 201),
    ("GET", "/.env", 404),
    ("GET", "/.git/config", 404),
    ("GET", "/api/auth/me", 401),
    ("POST", "/api/auth/login", 400),
    ("GET", "/api/admin/schools", 403),
    ("POST", "/api/health", 405),
    ("POST", "/api/runs", 409),
    ("POST", "/api/auth/pin-login", 422),
    ("POST", "/api/auth/forgot-password", 429),
    ("GET", "/api/runs/today", 500),
    ("GET", "/api/health", 503),
    # The status is the line's LAST word: a path that ends in digits does
    # not decide it either way.
    ("GET", "/files/report-401", 404),
    ("GET", "/files/page-404", 403),
    # The driver code lookup: the filter logs its route template in place of
    # the path, and the status still decides.
    ("GET", f"/api/auth/schools/{DRIVER_CODE}", 429),
    ("GET", f"/api/auth/schools/{DRIVER_CODE}", 404),
]


def _client_error_counted(status: int) -> bool:
    return 400 <= status <= 499 and status != 404


@pytest.fixture
def sample_lines(runtime_log) -> dict[str, list[tuple[str, bool]]]:
    """filter -> [(line, whether it must match)], every line from real code."""
    access = _mangum_lines(runtime_log, ACCESS_CASES)
    runtime_log.lines.clear()

    # Mail: one failed send per kind, then the cap's INFO line and the
    # simulated transport's INFO line, which must not count as failures.
    failing = MailService(FailingTransport())
    for kind in ("reset", "verify", "existing-account", "email-changed", "office-reset"):
        assert failing.send(MailMessage(kind=kind, to="parent@saferide.test", subject="s",
                                        text="t", html="h")) is False
    mail_failed = list(runtime_log.lines)
    runtime_log.lines.clear()
    from app.services.mail_service import SimulatedTransport

    capped = AuthService(dao=AuthDaoStub(), accounts=object(), mail=MailService(SimulatedTransport()))
    for _ in range(MAIL_CAP_PER_HOUR + 1):
        capped.forgot_password("parent@saferide.test")
    mail_other = list(runtime_log.lines)
    assert any("mail-capped kind=reset" in line for line in mail_other)
    assert any("mail (simulated) kind=reset" in line for line in mail_other)
    runtime_log.lines.clear()

    # PIN sign-in: no school, an unknown and a malformed school, failures at
    # a known school until the lock, then a refusal under the lock.
    dao = AuthDaoStub()
    auth = AuthService(dao=dao, accounts=object(), mail=MailService(SimulatedTransport()))
    with pytest.raises(PinLoginError):
        auth.pin_login("482915", None, source="203.0.113.9")
    required = list(runtime_log.lines)
    runtime_log.lines.clear()
    for school in ("6cae0000-0000-0000-0000-000000000009", "not-a-school"):
        with pytest.raises(PinLoginError):
            auth.pin_login("482915", school, source="203.0.113.9")
    for _ in range(PIN_LOCK_MAX_FAILURES + 1):
        with pytest.raises(PinLoginError):
            auth.pin_login("000001", SCHOOL, source="203.0.113.9")
    failures = list(runtime_log.lines)
    runtime_log.lines.clear()
    with pytest.raises(SchoolPinLockedError):
        auth.pin_login("482915", SCHOOL, source="203.0.113.9")
    locked = list(runtime_log.lines)
    runtime_log.lines.clear()

    assert len(required) == 1 and len(locked) == 1
    assert len(failures) == 2 + PIN_LOCK_MAX_FAILURES + 1
    assert len(mail_failed) == 5
    auth_lines = required + failures + locked
    every = [*access.values(), *mail_failed, *mail_other, *auth_lines]
    return {
        "ApiClientErrorsFilter": [
            (line, _client_error_counted(status)) for (_, _, status), line in access.items()
        ] + [(line, False) for line in mail_failed + mail_other + auth_lines],
        "MailSendFailedFilter": [(line, line in mail_failed) for line in every],
        "PinFailureFilter": [(line, line in failures) for line in every],
        "PinLockedFilter": [(line, line in locked) for line in every],
        "SchoolRequiredFilter": [(line, line in required) for line in every],
    }


def test_every_filter_is_checked_here():
    assert set(FILTERS) == {
        "ApiClientErrorsFilter", "MailSendFailedFilter", "PinFailureFilter",
        "PinLockedFilter", "SchoolRequiredFilter",
    }


@pytest.mark.parametrize("metric_filter", sorted(FILTERS))
def test_each_filter_counts_exactly_its_own_lines(metric_filter, sample_lines):
    for line, expected in sample_lines[metric_filter]:
        assert matches(FILTERS[metric_filter], line) is expected, (metric_filter, line)
    assert any(expected for _, expected in sample_lines[metric_filter])


def test_the_real_lines_have_the_shape_the_filters_read(sample_lines):
    """The JSON keys the patterns select exist on every line, and no line
    carries an address, a PIN or a provider's error text."""
    lines = [line for line, _ in sample_lines["MailSendFailedFilter"]]
    for line in lines:
        record = json.loads(line)
        assert {"timestamp", "level", "message", "logger", "request_id"} <= set(record), line
        assert "saferide.test" not in line and "482915" not in line and "000001" not in line
        assert "not verified" not in line and DRIVER_CODE not in line
    access = [json.loads(line) for line in lines if json.loads(line)["logger"] == "mangum.http"]
    # The shape of the production line the brief quotes (the runtime adds
    # requestId from the invocation; the stand-in has no invocation).
    assert {"message": "GET /api/health 200", "level": "INFO", "logger": "mangum.http"}.items() <= (
        access[0].items()
    )
    assert "GET /api/auth/schools/{code} 429" in {record["message"] for record in access}
    by_logger = {json.loads(line)["logger"] for line in lines}
    assert {"mangum.http", "saferide.mail", "saferide.auth"} <= by_logger


# --- the pre-deploy commands in infra/README.md -----------------------------------

_README_COMMAND = re.compile(
    r"aws logs test-metric-filter --region af-south-1 \\\n"
    r"\s+--filter-pattern ('[^'\n]+') \\\n"
    r"\s+--log-event-messages ((?:'[^'\n]+'(?: \\\n\s+)?)+)"
)


def test_the_readme_runs_test_metric_filter_with_every_pattern_and_a_matching_sample():
    """The operator runs these before the 1b deploy; the expected answer is
    written beside each (the first message matches, any others do not)."""
    commands = _README_COMMAND.findall(README)
    patterns = {shlex.split(pattern)[0]: messages for pattern, messages in commands}
    assert set(patterns) == set(FILTERS.values()), "a template pattern has no README command"
    for pattern, raw_messages in patterns.items():
        messages = shlex.split(raw_messages.replace("\\\n", " "))
        assert messages, pattern
        assert matches(pattern, messages[0]), (pattern, messages[0])
        for other in messages[1:]:
            assert not matches(pattern, other), (pattern, other)
