"""Geocoding + route optimisation (#4, #9).

Provider precedence is driven by config:

* If ``GOOGLE_MAPS_API_KEY`` is set → Google Geocoding / Directions.
* Else if ``MAPBOX_TOKEN`` is set → Mapbox Geocoding / Optimization.
* Else → OpenStreetMap Nominatim for geocoding (free, key-less) and an
  in-process nearest-neighbour optimiser for ordering.

Every network call is best-effort: short timeouts, all errors swallowed, and a
graceful fall back to the offline path so a missing key or no connectivity
never breaks a save or a request.
"""
from __future__ import annotations

import math
from typing import Any

import httpx

from app.core.config import get_settings

_TIMEOUT = httpx.Timeout(6.0)
_USER_AGENT = "SafeRideKenya/1.0 (admin geocoding)"


def _provider() -> str:
    s = get_settings()
    if s.google_maps_api_key:
        return "google"
    if s.mapbox_token:
        return "mapbox"
    return "none"


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lat, lng) points."""
    r = 6371000.0
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# --- Geocoding --------------------------------------------------------------

def _geocode_google(address: str, key: str) -> dict | None:
    resp = httpx.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": key, "region": "ke"},
        timeout=_TIMEOUT,
    )
    data = resp.json()
    results = data.get("results") or []
    if not results:
        return None
    loc = results[0]["geometry"]["location"]
    return {"lat": loc["lat"], "lng": loc["lng"], "label": results[0].get("formatted_address", address)}


def _geocode_mapbox(address: str, token: str) -> dict | None:
    resp = httpx.get(
        f"https://api.mapbox.com/geocoding/v5/mapbox.places/{httpx.URL(address)}.json",
        params={"access_token": token, "country": "ke", "limit": 1},
        timeout=_TIMEOUT,
    )
    feats = resp.json().get("features") or []
    if not feats:
        return None
    lng, lat = feats[0]["center"]
    return {"lat": lat, "lng": lng, "label": feats[0].get("place_name", address)}


def _geocode_nominatim(address: str) -> dict | None:
    resp = httpx.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "format": "json", "limit": 1, "countrycodes": "ke"},
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
    )
    rows = resp.json()
    if not rows:
        return None
    return {"lat": float(rows[0]["lat"]), "lng": float(rows[0]["lon"]), "label": rows[0].get("display_name", address)}


def geocode(address: str | None, *, allow_fallback: bool = True) -> dict | None:
    """Resolve an address to ``{lat, lng, label, provider}`` or ``None``.

    With no provider key, only the free Nominatim fallback is used, and only
    when ``allow_fallback`` is true — so silent on-save geocoding makes no
    network calls until a real key is configured.
    """
    if not address or not address.strip():
        return None
    address = address.strip()
    s = get_settings()
    try:
        if s.google_maps_api_key:
            hit = _geocode_google(address, s.google_maps_api_key)
            if hit:
                return {**hit, "provider": "google"}
        elif s.mapbox_token:
            hit = _geocode_mapbox(address, s.mapbox_token)
            if hit:
                return {**hit, "provider": "mapbox"}
        if allow_fallback:
            hit = _geocode_nominatim(address)
            if hit:
                return {**hit, "provider": "nominatim"}
    except Exception:  # noqa: BLE001 - geocoding is always best-effort
        return None
    return None


# --- Route optimisation -----------------------------------------------------

def _nearest_neighbour(points: list[dict], start: dict | None) -> list[dict]:
    """Order points greedily by nearest next stop, beginning at ``start`` (or
    the first point if no anchor is given)."""
    remaining = list(points)
    ordered: list[dict] = []
    cursor = (start["lat"], start["lng"]) if start else None
    while remaining:
        if cursor is None:
            nxt = remaining.pop(0)
        else:
            nxt = min(remaining, key=lambda p: haversine_m(cursor, (p["lat"], p["lng"])))
            remaining.remove(nxt)
        ordered.append(nxt)
        cursor = (nxt["lat"], nxt["lng"])
    return ordered


def _optimize_google(points: list[dict], start: dict, end: dict | None, key: str) -> list[int] | None:
    waypoints = "optimize:true|" + "|".join(f"{p['lat']},{p['lng']}" for p in points)
    resp = httpx.get(
        "https://maps.googleapis.com/maps/api/directions/json",
        params={
            "origin": f"{start['lat']},{start['lng']}",
            "destination": f"{(end or start)['lat']},{(end or start)['lng']}",
            "waypoints": waypoints,
            "key": key,
        },
        timeout=_TIMEOUT,
    )
    routes = resp.json().get("routes") or []
    if not routes:
        return None
    return routes[0].get("waypoint_order")


def optimize_route(
    points: list[dict], *, start: dict | None = None, end: dict | None = None
) -> dict[str, Any]:
    """Return an optimised ordering of ``points`` (each ``{lat, lng, ...}``).

    Uses the configured provider's optimiser when a key is present, otherwise a
    nearest-neighbour heuristic. Returns ``{ordered, provider}`` where
    ``ordered`` is the input list re-sequenced.
    """
    located = [p for p in points if p.get("lat") is not None and p.get("lng") is not None]
    if len(located) <= 1:
        return {"ordered": located, "provider": "trivial"}
    s = get_settings()
    try:
        if s.google_maps_api_key and start:
            order = _optimize_google(located, start, end, s.google_maps_api_key)
            if order is not None:
                return {"ordered": [located[i] for i in order], "provider": "google"}
    except Exception:  # noqa: BLE001 - fall back to offline ordering
        pass
    return {"ordered": _nearest_neighbour(located, start), "provider": "nearest-neighbour"}
