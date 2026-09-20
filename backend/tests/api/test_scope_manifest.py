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


def _registered() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in create_app().routes:
        if isinstance(route, APIRoute):
            for method in route.methods - {"HEAD", "OPTIONS"}:
                out.add((method, route.path))
    return out


def test_every_route_is_classified():
    registered = _registered()
    declared = set(MANIFEST)
    missing = sorted(registered - declared)
    stale = sorted(declared - registered)
    assert not missing, f"routes missing a scope classification: {missing}"
    assert not stale, f"manifest entries for routes that no longer exist: {stale}"


def test_every_class_is_known():
    bad = {route: cls for route, cls in MANIFEST.items() if cls not in ALLOWED}
    assert not bad, f"unknown scope classes: {bad}"
