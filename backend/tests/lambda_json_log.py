"""A stand-in for the Lambda Python runtime's JSON log formatter (hardening U11).

The functions log through ``LoggingConfig: LogFormat: JSON``, which makes the
runtime (awslambdaric, ``lambda_runtime_log_utils.JsonFormatter``) put its own
handler on the root logger before the handler module is imported. Its shape,
reproduced here because the runtime package is not a dependency:

- ``timestamp``, ``level``, ``message`` (``getMessage()``), ``logger``;
- from ``exc_info``: ``stackTrace`` (``traceback.format_tb``), ``errorType``
  (the class name) and ``errorMessage`` (``str(exception)``), plus
  ``location`` (``pathname:funcName:lineno``) — the message is what carries a
  psycopg error's failing row;
- ``requestId`` from the record's ``aws_request_id``;
- every other record attribute that is not a standard LogRecord field (so
  ``extra=`` keys and anything a filter sets) as a top-level key;
- ``None`` values dropped; ``json.dumps(..., default=str, ensure_ascii=False)``.

Tests assert on the emitted line as a whole, so a value anywhere in it —
message, extra, error field — counts.
"""

import json
import logging
import traceback

_RESERVED_FIELDS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "aws_request_id", "tenant_id", "_frame_type",
}


class LambdaJsonFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(datefmt="%Y-%m-%dT%H:%M:%SZ")

    def format(self, record: logging.LogRecord) -> str:
        record.levelno = min(50, max(0, record.levelno)) // 10 * 10
        record.levelname = logging.getLevelName(record.levelno)
        exc_info = record.exc_info
        message = (
            record.msg
            if isinstance(record.msg, dict) and not record.args
            else record.getMessage()
        )
        result = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": message,
            "logger": record.name,
            "stackTrace": traceback.format_tb(exc_info[2]) if exc_info else None,
            "errorType": exc_info[0].__name__ if exc_info else None,
            "errorMessage": str(exc_info[1]) if exc_info else None,
            "requestId": getattr(record, "aws_request_id", None),
            "location": (
                f"{record.pathname}:{record.funcName}:{record.lineno}" if exc_info else None
            ),
        }
        if getattr(record, "tenant_id", None) is not None:
            result["tenantId"] = record.tenant_id
        result.update(
            (key, value)
            for key, value in record.__dict__.items()
            if key not in _RESERVED_FIELDS and key not in result
        )
        result = {key: value for key, value in result.items() if value is not None}
        return json.dumps(result, default=str, ensure_ascii=False)


class JsonLines(logging.Handler):
    """Collects each record exactly as the runtime would emit it."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.setFormatter(LambdaJsonFormatter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.lines]

    def text(self) -> str:
        return "\n".join(self.lines)
