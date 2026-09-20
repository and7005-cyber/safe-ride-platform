"""The scope manifest must always match the registered routes (U1).

Runs in the unit suite (no database) so an unclassified route fails every
build, not just certification.
"""
from fastapi.routing import APIRoute

from app.main import create_app
from tests.scope_manifest import MANIFEST

ALLOWED = {
    "public", "auth", "provider-global", "school-scoped",
    "driver-scoped", "parent-linked", "user-scoped",
}


def _api_routes(routes, prefix: str = ""):
    """Yield (path, methods) for every APIRoute, walking included routers.

    FastAPI 0.13x+ keeps each include_router() call as a lazy entry in
    app.routes (original_router + include_context.prefix) instead of
    flattening it into APIRoutes; older versions flatten. Both shapes end
    here, so the manifest check does not depend on the pinned version.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route.methods
            continue
        original = getattr(route, "original_router", None)
        if original is not None:
            context = getattr(route, "include_context", None)
            inner_prefix = prefix + (getattr(context, "prefix", "") or "")
            yield from _api_routes(original.routes, inner_prefix)


def _registered() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for path, methods in _api_routes(create_app().routes):
        for method in methods - {"HEAD", "OPTIONS"}:
            out.add((method, path))
    return out


def test_every_route_is_classified():
    registered = _registered()
    # An empty walk would make the stale/missing checks pass vacuously; the
    # route shape changing under a FastAPI upgrade must fail loudly instead.
    assert len(registered) > 100, f"route walk found only {len(registered)} routes"
    declared = set(MANIFEST)
    missing = sorted(registered - declared)
    stale = sorted(declared - registered)
    assert not missing, f"routes missing a scope classification: {missing}"
    assert not stale, f"manifest entries for routes that no longer exist: {stale}"


def test_every_class_is_known():
    bad = {route: cls for route, cls in MANIFEST.items() if cls not in ALLOWED}
    assert not bad, f"unknown scope classes: {bad}"
