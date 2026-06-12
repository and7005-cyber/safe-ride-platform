from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    accounts,
    auth,
    fleet,
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

    @app.exception_handler(SafeRideError)
    def saferide_error_handler(request: Request, error: SafeRideError) -> JSONResponse:
        http_error = to_http_exception(error)
        return JSONResponse(
            status_code=http_error.status_code, content={"detail": http_error.detail}
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(fleet.router)
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
