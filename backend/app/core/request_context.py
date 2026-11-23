"""Request ids, the API response headers and redacted error logging (hardening U11).

One id per request, never taken from the caller: inside Lambda it is the
invocation's ``aws_request_id`` (Mangum puts the context object in the ASGI
scope as ``aws.context``), so the API's line, the platform's START/END/REPORT
lines and the ``requestId`` field of every record all carry the same value;
outside Lambda it is a fresh uuid4. An inbound ``X-Request-Id`` header is never
read or echoed — a caller could otherwise plant ids in the office's logs.

The never-log list (plan Key Technical Decision; execution decisions, Release
1b): bodies, query strings, tokens, PINs and hashes, passwords, email
addresses, names, coordinates. Lambda's JSON formatter turns a record's
``exc_info`` into ``errorMessage`` — ``str(exception)`` — and a psycopg error's
text carries the failing row (``Key (email)=(…)``, ``Failing row contains
(…, lat, lng, …)``). So no record leaves this process with ``exc_info``:
the unexpected-error line carries the class, the traceback frames
(``traceback.format_tb``: file, line, function, source line — never the
message) and, for database errors, the SQL state, constraint and table names
only. ``RequestLogFilter`` applies the same rule to every other record that
reaches the root handlers (the existing ``logger.exception`` calls in the
DAOs, the connection pool, Mangum).

One path is itself a secret: the public driver code lookup
(``GET /api/auth/schools/{code}``, hardening U10) carries the school's driver
code, which resolves to the school id for PIN sign-in. Mangum's access line
(``mangum.http``: ``"<METHOD> <path> <status>"``) and the unexpected-error
line name that route as ``/api/auth/schools/{code}``, never the code
(:func:`loggable_path`); the status stays the access line's last word, which
the ``ApiClientErrorsFilter`` metric filter reads.
"""

from __future__ import annotations

import logging
import re
import traceback
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

from psycopg import Error as PsycopgError

REQUEST_ID_HEADER = "X-Request-Id"
# Our own ASGI scope key (beside Mangum's ``aws.context``): it carries the id
# to the 5xx handlers, and to the backstop handler that runs outside the
# middleware after the context var is unbound.
REQUEST_ID_SCOPE_KEY = "saferide.request_id"
UNEXPECTED_ERROR_DETAIL = "Unexpected backend error"

# Every API response (U11/R25). The API serves JSON only, so the policy denies
# everything and forbids framing; HSTS matches the site's CloudFront policy.
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
    ("X-Content-Type-Options", "nosniff"),
    ("Cache-Control", "no-store"),
    ("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"),
)

# Lambda request ids are UUIDs; anything else (a test double, a future
# runtime change) falls back to a fresh id rather than reaching a header.
_ID_SHAPE = re.compile(r"^[A-Za-z0-9-]{1,64}$")

error_logger = logging.getLogger("saferide.errors")


@dataclass(frozen=True)
class RequestInfo:
    request_id: str
    method: str | None
    path: str | None


_current: ContextVar[RequestInfo | None] = ContextVar("saferide_request", default=None)


def request_id_for(scope: dict) -> str:
    """The id for one ASGI request: the Lambda invocation's, else a uuid4.
    Headers are deliberately not consulted."""
    context = scope.get("aws.context")
    candidate = getattr(context, "aws_request_id", None)
    if isinstance(candidate, str) and _ID_SHAPE.match(candidate):
        return candidate
    return str(uuid.uuid4())


def bind_request(info: RequestInfo) -> Token:
    return _current.set(info)


def unbind_request(token: Token) -> None:
    _current.reset(token)


def current_request_id() -> str | None:
    info = _current.get()
    return info.request_id if info is not None else None


# --- redaction ---------------------------------------------------------------

# Everything below the lookup's prefix, not only one segment: Mangum unquotes
# the path, so a typed "/" (%2F) would otherwise split the code across two.
DRIVER_CODE_PATH_PREFIX = "/api/auth/schools/"
DRIVER_CODE_PATH_LOGGED = "/api/auth/schools/{code}"


def _is_driver_code_path(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(DRIVER_CODE_PATH_PREFIX)
        and len(value) > len(DRIVER_CODE_PATH_PREFIX)
    )


def loggable_path(path: str | None) -> str | None:
    """A request path as a log line may carry it: the driver code lookup's
    path becomes its route template; every other path is unchanged."""
    return DRIVER_CODE_PATH_LOGGED if _is_driver_code_path(path) else path


def database_error(error: BaseException | None) -> PsycopgError | None:
    """The psycopg error behind ``error``: itself, or the first one in its
    cause/context chain (a service that wraps a database failure)."""
    seen = 0
    while error is not None and seen < 5:
        if isinstance(error, PsycopgError):
            return error
        error = error.__cause__ or error.__context__
        seen += 1
    return None


def database_fields(error: BaseException | None) -> dict[str, str | None]:
    """SQL state, constraint and table names — never ``message_primary``
    (a raised exception's text is free-form), ``message_detail`` (the failing
    row) or ``str(error)`` (both)."""
    db_error = database_error(error)
    if db_error is None:
        return {"sqlstate": None, "constraint": None, "table": None}
    diag = getattr(db_error, "diag", None)
    return {
        "sqlstate": getattr(db_error, "sqlstate", None),
        "constraint": getattr(diag, "constraint_name", None),
        "table": getattr(diag, "table_name", None),
    }


def traceback_frames(error: BaseException) -> list[str]:
    """The frames only, as the Lambda formatter's ``stackTrace`` would list
    them — without the final ``Class: message`` line."""
    return traceback.format_tb(error.__traceback__)


def log_unexpected_error(
    error: BaseException,
    *,
    method: str | None = None,
    path: str | None = None,
    request_id: str | None = None,
) -> None:
    """The one record an unexpected error leaves: method, path (never the
    query string, and never a driver code: :func:`loggable_path`), request
    id, class, the database names, and the traceback frames as a structured
    field. ``unexpected-error`` is the stable token for searches and metric
    filters. Anything not passed comes from the bound request (map_error's
    callers pass nothing)."""
    info = _current.get()
    method = method or (info.method if info else None)
    path = loggable_path(path or (info.path if info else None))
    request_id = request_id or (info.request_id if info else None) or "-"
    fields = database_fields(error)
    error_logger.error(
        "unexpected-error method=%s path=%s request_id=%s error=%s "
        "sqlstate=%s constraint=%s table=%s",
        method or "-",
        path or "-",
        request_id,
        type(error).__name__,
        fields["sqlstate"] or "-",
        fields["constraint"] or "-",
        fields["table"] or "-",
        extra={"request_id": request_id, "traceback": traceback_frames(error)},
    )


class RequestLogFilter(logging.Filter):
    """Installed on the root handlers (Lambda's own JSON handler, or the local
    stream handler): stamps ``request_id`` on every record, strips
    ``exc_info`` from any record that carries one, keeping the class, the
    frames and the database names, and puts the lookup's route template in
    place of any driver code lookup path among a record's arguments (Mangum's
    access line passes the path as its second; the method and the status are
    left as they are). Always returns True — it redacts, it never drops."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = current_request_id() or "-"
        args = record.args
        if isinstance(args, tuple) and any(_is_driver_code_path(arg) for arg in args):
            record.args = tuple(loggable_path(arg) if isinstance(arg, str) else arg for arg in args)
        if record.exc_info:
            error = record.exc_info[1]
            if error is not None:
                record.error_class = type(error).__name__
                record.traceback = traceback_frames(error)
                for key, value in database_fields(error).items():
                    if value is not None:
                        setattr(record, key, value)
            record.exc_info = None
            record.exc_text = None
        return True


class LocalFormatter(logging.Formatter):
    """The text format outside Lambda: one line with the request id, then the
    traceback frames when the record carries them."""

    def __init__(self) -> None:
        super().__init__("%(levelname)s %(name)s request_id=%(request_id)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        line = super().format(record)
        frames = getattr(record, "traceback", None)
        if frames:
            line = line + "\n" + "".join(frames).rstrip("\n")
        return line


def configure_logging(level_name: str, *, in_lambda: bool) -> None:
    """Run once at app import on both entry paths; idempotent.

    Inside Lambda the runtime has already put its JSON handler on the root
    logger and set the level from the function's LoggingConfig: the filter
    goes on that handler and nothing else changes. Outside Lambda ``LOG_LEVEL``
    sets the root level, and when the root has no handler (uvicorn configures
    only its own loggers, so the app's INFO lines would go nowhere) a stream
    handler with the filter is added. Handlers someone else put on the root
    outside Lambda (pytest's capture, an embedding process) are left as they
    are."""
    root = logging.getLogger()
    if in_lambda:
        targets = list(root.handlers)
    else:
        level = logging.getLevelName(level_name.strip().upper())
        root.setLevel(level if isinstance(level, int) else logging.INFO)
        targets = []
        if not root.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(LocalFormatter())
            root.addHandler(handler)
            targets = [handler]
    for handler in targets:
        if not any(isinstance(existing, RequestLogFilter) for existing in handler.filters):
            handler.addFilter(RequestLogFilter())
