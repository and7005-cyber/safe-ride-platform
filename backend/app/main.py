import logging
import os

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    accounts,
    auth,
    fleet,
    fleet_plans,
    health,
    incidents,
    parent_portal,
    provider,
    push,
    runs_live,
    staff,
    students_live,
)
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.errors import SafeRideError, to_http_exception
from app.core.request_context import (
    REQUEST_ID_HEADER,
    REQUEST_ID_SCOPE_KEY,
    SECURITY_HEADERS,
    UNEXPECTED_ERROR_DETAIL,
    RequestInfo,
    bind_request,
    configure_logging,
    log_unexpected_error,
    request_id_for,
    unbind_request,
)
from app.core.scope import current_scope

# Hardening D20: httpx logs every request URL at INFO, and the geocoder
# (app/services/geo_service.py) sends the server Maps key and the searched
# home address as query parameters, so with Lambda logging at INFO both would
# reach CloudWatch. Pinned at import, so every entry path inherits it: uvicorn
# loads app.main:app and the Lambda handler imports this module. WARNING keeps
# the libraries' own warnings and errors.
for _library in ("httpx", "httpcore"):
    logging.getLogger(_library).setLevel(logging.WARNING)

# Hardening U11: the request-id and redaction filter on the root handlers on
# both entry paths; LOG_LEVEL applies only off Lambda (the runtime sets the
# level from the function's LoggingConfig and installs its JSON handler before
# this module is imported). The D20 pins above are per-logger, so a root
# level never lowers them.
configure_logging(
    get_settings().log_level, in_lambda=bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
)

_scope_logger = logging.getLogger("saferide.scope")


class ScopeResponseMiddleware:
    """Raw-ASGI (deliberately not BaseHTTPMiddleware, which would run the app
    in a separate task and hide the request's scope context var — breaking
    both this middleware and background-task GUC inheritance).

    Scoped responses are per-school: mark them uncacheable and header-variant,
    and emit the one request log line (route, school, actor kind, outcome).
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = {"code": None}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                request_scope = current_scope()
                if request_scope is not None:
                    headers = MutableHeaders(scope=message)
                    headers["Cache-Control"] = "no-store"
                    vary = headers.get("Vary")
                    headers["Vary"] = f"{vary}, X-School-Id" if vary else "X-School-Id"
            await send(message)

        await self.app(scope, receive, send_wrapper)
        request_scope = current_scope()
        if request_scope is not None:
            _scope_logger.info(
                "scoped request method=%s path=%s school=%s actor=%s status=%s",
                scope.get("method"),
                scope.get("path"),
                ",".join(request_scope.school_ids) or "-",
                request_scope.actor_kind,
                status["code"],
            )


# Interactive docs exist only on a local stack (U11/R18). There the docs pages
# load Swagger UI and ReDoc from a CDN, so they alone go without the API's
# deny-all content-security policy.
_DOCS_URLS = {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
_DOCS_PAGES = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


def apply_response_headers(
    headers: MutableHeaders, request_id: str | None, *, csp: bool = True
) -> None:
    if request_id:
        headers[REQUEST_ID_HEADER] = request_id
    for name, value in SECURITY_HEADERS:
        if csp or name != "Content-Security-Policy":
            headers[name] = value


def unexpected_error_response(request_id: str | None) -> JSONResponse:
    """The 500 an unexpected error answers with: the generic detail and the id
    the user quotes. Stamps the headers itself, for the backstop handler whose
    response never passes RequestContextMiddleware."""
    response = JSONResponse(
        status_code=500,
        content={"detail": UNEXPECTED_ERROR_DETAIL, "request_id": request_id},
    )
    apply_response_headers(response.headers, request_id)
    return response


class RequestContextMiddleware:
    """The outermost raw-ASGI layer (hardening U11; raw for the same reason as
    ScopeResponseMiddleware: the request's context vars must reach the route).

    Mints the request id (``request_id_for``: the Lambda invocation's, never
    an inbound header), binds it for the logging filter, and stamps it and the
    security headers on every response that passes: routes, 404s, 422s, CORS
    preflights, the 5xx handlers and UnexpectedErrorMiddleware's 500.
    """

    def __init__(self, app, *, docs_pages: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.docs_pages = docs_pages

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_for(scope)
        # For the backstop handler, which runs outside this layer.
        scope[REQUEST_ID_SCOPE_KEY] = request_id
        path = scope.get("path")
        csp = path not in self.docs_pages
        token = bind_request(
            RequestInfo(request_id=request_id, method=scope.get("method"), path=path)
        )

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                apply_response_headers(MutableHeaders(scope=message), request_id, csp=csp)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            unbind_request(token)


class UnexpectedErrorMiddleware:
    """The catch-all for exceptions no handler took (hardening U11): one
    redacted record, then the 500 contract. It sits inside CORSMiddleware so
    the browser can read that 500 (and its X-Request-Id); Starlette's own
    ServerErrorMiddleware answers outside CORS, and it re-raises into Mangum,
    whose ``logger.exception`` would add a second record for the same error.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as error:  # noqa: BLE001 — the catch-all, by design
            request_id = scope.get(REQUEST_ID_SCOPE_KEY)
            log_unexpected_error(
                error, method=scope.get("method"), path=scope.get("path"), request_id=request_id
            )
            if not started:
                await unexpected_error_response(request_id)(scope, receive, send_wrapper)
            # Once the response has started nothing more can be sent; the
            # record above is the trace, and re-raising would only add
            # Mangum's.


def create_app() -> FastAPI:
    settings = get_settings()
    docs = _DOCS_URLS if settings.is_local else dict.fromkeys(_DOCS_URLS)
    app = FastAPI(title="SafeRide API", **docs)

    # Starlette wraps in reverse order of adding: the last added is outermost.
    # RequestContext -> Scope -> CORS -> UnexpectedError -> handlers -> routes.
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # The browser may read the id, so a 5xx message can quote it (U11),
        # and Retry-After, so the PIN lock screen can count down (U10, DL4).
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    app.add_middleware(ScopeResponseMiddleware)
    app.add_middleware(
        RequestContextMiddleware, docs_pages=_DOCS_PAGES if settings.is_local else frozenset()
    )

    @app.exception_handler(SafeRideError)
    def saferide_error_handler(request: Request, error: SafeRideError) -> JSONResponse:
        http_error = to_http_exception(error)
        return JSONResponse(
            status_code=http_error.status_code, content={"detail": http_error.detail}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, error: StarletteHTTPException):
        # A 5xx (map_error's fallthrough, which logged it) carries the request
        # id in the body as well as the header; everything else keeps
        # FastAPI's own rendering.
        if error.status_code >= 500:
            request_id = request.scope.get(REQUEST_ID_SCOPE_KEY)
            return JSONResponse(
                status_code=error.status_code,
                content={"detail": error.detail, "request_id": request_id},
                headers=error.headers,
            )
        return await http_exception_handler(request, error)

    @app.exception_handler(Exception)
    def unexpected_error_handler(request: Request, error: Exception) -> JSONResponse:
        # Backstop only: UnexpectedErrorMiddleware answers every exception
        # raised beneath it, so this sees one raised by the layers above it.
        # Starlette re-raises after it; the root filter strips the text from
        # Mangum's record of that.
        request_id = request.scope.get(REQUEST_ID_SCOPE_KEY) or request_id_for(request.scope)
        log_unexpected_error(
            error, method=request.method, path=request.url.path, request_id=request_id
        )
        return unexpected_error_response(request_id)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(fleet.router)
    app.include_router(fleet_plans.router)
    app.include_router(students_live.router)
    app.include_router(runs_live.router)
    app.include_router(incidents.router)
    app.include_router(accounts.router)
    app.include_router(staff.router)
    app.include_router(provider.router)
    app.include_router(parent_portal.router)
    app.include_router(push.router)

    @app.on_event("shutdown")
    def shutdown() -> None:
        close_pool()

    return app


app = create_app()
