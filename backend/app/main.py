from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    accounts,
    admin,
    auth,
    driver,
    fleet,
    health,
    incidents,
    notifications,
    parent,
    parent_portal,
    push,
    runs_live,
    students_live,
)
from app.core.config import get_settings
from app.core.db import close_pool


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

    app.include_router(health.router)
    # Legacy routers (kept for compatibility; not driving the new UI).
    app.include_router(admin.router)
    app.include_router(driver.router)
    app.include_router(parent.router)
    app.include_router(notifications.router)
    # Live-model routers.
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
