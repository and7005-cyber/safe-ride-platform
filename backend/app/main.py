import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders

from app.api import (
    accounts,
    auth,
    fleet,
    fleet_plans,
    health,
    incidents,
    parent_portal,
    push,
    runs_live,
    students_live,
)
from app.core.config import get_settings
from app.core.db import close_pool
from app.core.errors import SafeRideError, to_http_exception
from app.core.scope import current_scope

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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="SafeRide API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(ScopeResponseMiddleware)

    @app.exception_handler(SafeRideError)
    def saferide_error_handler(request: Request, error: SafeRideError) -> JSONResponse:
        http_error = to_http_exception(error)
        return JSONResponse(
            status_code=http_error.status_code, content={"detail": http_error.detail}
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(fleet.router)
    app.include_router(fleet_plans.router)
    app.include_router(students_live.router)
    app.include_router(runs_live.router)
    app.include_router(incidents.router)
    app.include_router(accounts.router)
    app.include_router(parent_portal.router)
    app.include_router(push.router)

    @app.on_event("shutdown")
    def shutdown() -> None:
        close_pool()

    return app


app = create_app()
