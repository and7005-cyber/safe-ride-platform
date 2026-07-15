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

import datetime as dt
import math
from typing import Any

import httpx

from app.core.config import get_settings

_TIMEOUT = httpx.Timeout(8.0)
_USER_AGENT = "SafeRideKenya/1.0 (admin geocoding)"

# Routes API v2 (the modern replacement for the Directions API): one call gives
# an optimised stop order, the encoded road polyline, and per-leg traffic-aware
# durations. See compute helpers below.
_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
_PLACES_URL = "https://places.googleapis.com/v1"
# Kenya is UTC+3 year-round (no DST), so a fixed offset is exact.
_EAT = dt.timezone(dt.timedelta(hours=3))
# Loose bounding box around greater Nairobi to bias address autocomplete.
_NAIROBI_BIAS = {
    "rectangle": {
        "low": {"latitude": -1.45, "longitude": 36.60},
        "high": {"latitude": -1.10, "longitude": 37.10},
    }
}
# Average urban bus speed (m/s) used to estimate leg durations when offline.
_OFFLINE_SPEED_MS = 25 * 1000 / 3600  # ~25 km/h


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


def reverse_geocode(lat: float | None, lng: float | None) -> dict:
    """Resolve coordinates to a human-readable address (R8: a dropped map pin
    fills the student's address field) via the Google Geocoding API's
    ``latlng`` lookup.

    Returns ``{found, label}``. Like :func:`geocode`, this is best-effort: no
    key, no result, or any network/parse error → ``{"found": False}`` so a
    failed lookup never breaks the caller (the address is simply left as-is).
    """
    if lat is None or lng is None:
        return {"found": False}
    s = get_settings()
    if not s.google_maps_api_key:
        return {"found": False}
    try:
        resp = httpx.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lng}", "key": s.google_maps_api_key, "region": "ke"},
            timeout=_TIMEOUT,
        )
        results = resp.json().get("results") or []
        label = results[0].get("formatted_address") if results else None
        if not label:
            return {"found": False}
        return {"found": True, "label": label}
    except Exception:  # noqa: BLE001 - reverse geocoding is always best-effort
        return {"found": False}


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


# --- Routes API v2: order + road geometry + traffic-aware ETAs ---------------

def _dur_s(value: Any) -> int | None:
    """Routes API durations are strings like ``"456s"`` — coerce to int seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.endswith("s"):
        try:
            return int(round(float(value[:-1])))
        except ValueError:
            return None
    return None


def _latlng(p: dict) -> dict:
    return {"location": {"latLng": {"latitude": p["lat"], "longitude": p["lng"]}}}


def next_departure(anchor_hhmm: str | None, *, default: str) -> dt.datetime:
    """Next future occurrence (Africa/Nairobi) of ``anchor_hhmm`` (or ``default``).

    Used both as the Routes API ``departureTime`` for predictive traffic and as
    the baseline clock for per-stop ETAs. Routes API requires a future time, so
    if today's slot has passed we roll to tomorrow.
    """
    hhmm = anchor_hhmm or default
    try:
        h, m = (int(x) for x in hhmm.split(":")[:2])
    except (ValueError, AttributeError):
        h, m = (int(x) for x in default.split(":"))
    now = dt.datetime.now(tz=_EAT)
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand <= now + dt.timedelta(seconds=30):
        cand += dt.timedelta(days=1)
    return cand


def ensure_future(when: dt.datetime) -> dt.datetime:
    """Roll a departure to the next day if it is in the past — the Routes API
    rejects a past ``departureTime``. The backward-scheduling solve derives
    candidate departures as ``gate_anchor - drive`` which can land just before
    ``now`` when a roster is edited close to the bell; predictive traffic at the
    same wall-clock tomorrow is a fine stand-in, and the written stop times are
    wall-clock HH:MM (date-independent)."""
    now = dt.datetime.now(tz=_EAT)
    return when if when > now + dt.timedelta(seconds=30) else when + dt.timedelta(days=1)


# Backward-solve bounds (U4): the morning departure is found by fixed-point
# iteration (Google Routes has no arrive-by parameter). Both the shift and the
# convergence test run on raw cumulative seconds, never minute-truncated HH:MM.
SOLVE_MAX_ITERS = 4
SOLVE_TOL_S = 60


def solve_morning_departure(seq: list[dict], anchor_dt: dt.datetime) -> tuple[dt.datetime, dict, bool]:
    """Fixed-point solve for a morning departure so the gate-arrival ETA hits
    ``anchor_dt`` (the gate is the LAST point of ``seq``). Google Routes has no
    arrive-by, so iterate a forward solve: probe a departure, measure the drive,
    shift by the gate-time error, repeat — bounded at ``SOLVE_MAX_ITERS`` with a
    ``SOLVE_TOL_S`` tolerance on raw seconds. Returns ``(departure, geom,
    converged)``; on non-convergence the argmin-error iterate (best across ALL
    iterations, not the last, which can be the worse side of a rush-hour
    discontinuity). Each geo call is future-rolled so it never sends a past time;
    ``departure`` is the solved wall-clock the ETAs are computed from."""
    probe = anchor_dt  # first probe departs at the anchor and measures the drive
    best: tuple[float, dt.datetime, dict] | None = None
    for _ in range(SOLVE_MAX_ITERS):
        geom = route_geometry(seq, departure=ensure_future(probe))
        if geom["provider"] != "google-routes" or len(geom["legs"]) != len(seq) - 1:
            return probe, geom, False  # geometry degraded — caller falls back
        drive_s = sum((leg.get("duration_s") or 0) for leg in geom["legs"])
        err_s = (anchor_dt - (probe + dt.timedelta(seconds=drive_s))).total_seconds()
        if best is None or abs(err_s) < best[0]:
            best = (abs(err_s), probe, geom)
        if abs(err_s) <= SOLVE_TOL_S:
            return probe, geom, True
        probe = probe + dt.timedelta(seconds=err_s)  # shift departure by the gate error
    return best[1], best[2], False  # non-convergent: the argmin-error iterate


def _to_rfc3339(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _routes_call(
    origin: dict,
    destination: dict,
    intermediates: list[dict],
    key: str,
    *,
    optimize: bool,
    departure: dt.datetime | None,
) -> dict | None:
    body: dict[str, Any] = {
        "origin": _latlng(origin),
        "destination": _latlng(destination),
        "travelMode": "DRIVE",
    }
    if intermediates:
        body["intermediates"] = [_latlng(p) for p in intermediates]
        if optimize:
            body["optimizeWaypointOrder"] = True
    if departure is not None:
        # TRAFFIC_AWARE (not _OPTIMAL) is the only traffic mode compatible with
        # optimizeWaypointOrder, and it still factors predictive congestion.
        body["routingPreference"] = "TRAFFIC_AWARE"
        body["departureTime"] = _to_rfc3339(departure)
    else:
        body["routingPreference"] = "TRAFFIC_UNAWARE"
    fields = (
        "routes.optimizedIntermediateWaypointIndex,"
        "routes.polyline.encodedPolyline,"
        "routes.distanceMeters,routes.duration,"
        "routes.legs.distanceMeters,routes.legs.duration"
    )
    resp = httpx.post(
        _ROUTES_URL,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": fields,
        },
        json=body,
        timeout=_TIMEOUT,
    )
    routes = resp.json().get("routes") or []
    return routes[0] if routes else None


def optimized_order_with_provider(students: list[dict], school: dict | None) -> dict[str, Any]:
    """Like :func:`optimized_order`, but the caller learns WHO ordered.

    Returns ``{ordered, provider}`` where ``provider`` is ``'google'`` only
    when the Routes API waypoint optimiser actually sequenced the points,
    ``'trivial'`` when there was nothing to order (fewer than two located
    points), and ``'nearest-neighbour'`` for the offline fallback. Geometry
    writes are gated on this signal (U6/R10): the bare list return of
    ``optimized_order`` swallows failures, which is fine for the planner's
    best-effort options but would let a silent fallback masquerade as a
    computed order.
    """
    located = [p for p in students if p.get("lat") is not None and p.get("lng") is not None]
    if len(located) <= 1:
        return {"ordered": located, "provider": "trivial"}
    s = get_settings()
    if s.google_maps_api_key and school:
        try:
            rt = _routes_call(
                school, school, located, s.google_maps_api_key, optimize=True, departure=None
            )
            order = rt.get("optimizedIntermediateWaypointIndex") if rt else None
            if order is not None and len(order) == len(located):
                return {"ordered": [located[i] for i in order], "provider": "google"}
        except Exception:  # noqa: BLE001 - fall back to offline ordering
            pass
    return {"ordered": _nearest_neighbour(located, school or located[0]), "provider": "nearest-neighbour"}


def optimized_order(students: list[dict], school: dict | None) -> list[dict]:
    """Return ``students`` re-sequenced into an efficient visiting order.

    Uses the Routes API waypoint optimiser anchored on the school (a round trip
    used purely to derive the order), falling back to the offline
    nearest-neighbour heuristic when there is no key or the call fails.
    """
    return optimized_order_with_provider(students, school)["ordered"]


def route_geometry(sequence: list[dict], *, departure: dt.datetime | None = None) -> dict[str, Any]:
    """Road geometry + per-leg metrics for a FIXED ordered ``sequence``.

    Returns ``{polyline, total_distance_m, total_duration_s, legs, provider}``
    where ``legs[i]`` is the trip from ``sequence[i]`` to ``sequence[i+1]``
    (so ``len(legs) == len(sequence) - 1``). With the Google key set this is the
    real road route with traffic-aware durations; otherwise it degrades to
    straight-line haversine distances and a flat speed estimate.
    """
    pts = [p for p in sequence if p.get("lat") is not None and p.get("lng") is not None]
    if len(pts) < 2:
        return {
            "polyline": None,
            "total_distance_m": 0,
            "total_duration_s": 0,
            "legs": [],
            "provider": "trivial",
        }
    s = get_settings()
    if s.google_maps_api_key:
        try:
            rt = _routes_call(
                pts[0], pts[-1], pts[1:-1], s.google_maps_api_key,
                optimize=False, departure=departure,
            )
            if rt:
                legs = [
                    {"distance_m": leg.get("distanceMeters") or 0, "duration_s": _dur_s(leg.get("duration"))}
                    for leg in (rt.get("legs") or [])
                ]
                return {
                    "polyline": (rt.get("polyline") or {}).get("encodedPolyline"),
                    "total_distance_m": rt.get("distanceMeters") or 0,
                    "total_duration_s": _dur_s(rt.get("duration")) or 0,
                    "legs": legs,
                    "provider": "google-routes",
                }
        except Exception:  # noqa: BLE001 - fall back to straight-line estimate
            pass
    legs = []
    for a, b in zip(pts, pts[1:]):
        dist = haversine_m((a["lat"], a["lng"]), (b["lat"], b["lng"]))
        legs.append({"distance_m": int(dist), "duration_s": int(dist / _OFFLINE_SPEED_MS)})
    return {
        "polyline": None,
        "total_distance_m": int(sum(leg["distance_m"] for leg in legs)),
        "total_duration_s": int(sum(leg["duration_s"] for leg in legs)),
        "legs": legs,
        "provider": "offline",
    }


# --- Places autocomplete (New) ----------------------------------------------

def places_autocomplete(query: str | None) -> list[dict]:
    """Address suggestions biased to Nairobi via the Places API (New).

    Returns ``[{place_id, description, primary, secondary}]``. Empty without a
    key or query — the planner still accepts free-typed addresses (geocoded on
    submit), so autocomplete is a pure enhancement.
    """
    s = get_settings()
    if not query or not query.strip() or not s.google_maps_api_key:
        return []
    try:
        resp = httpx.post(
            f"{_PLACES_URL}/places:autocomplete",
            headers={"Content-Type": "application/json", "X-Goog-Api-Key": s.google_maps_api_key},
            json={"input": query.strip(), "includedRegionCodes": ["ke"], "locationBias": _NAIROBI_BIAS},
            timeout=_TIMEOUT,
        )
        out = []
        for sug in resp.json().get("suggestions") or []:
            pred = sug.get("placePrediction") or {}
            pid = pred.get("placeId")
            text = (pred.get("text") or {}).get("text")
            fmt = pred.get("structuredFormat") or {}
            primary = (fmt.get("mainText") or {}).get("text") or text
            secondary = (fmt.get("secondaryText") or {}).get("text")
            if pid and text:
                out.append({"place_id": pid, "description": text, "primary": primary, "secondary": secondary})
        return out
    except Exception:  # noqa: BLE001 - autocomplete is best-effort
        return []


def place_details(place_id: str | None) -> dict | None:
    """Resolve a Places ``place_id`` to ``{lat, lng, label}`` via Place Details (New)."""
    s = get_settings()
    if not place_id or not s.google_maps_api_key:
        return None
    try:
        resp = httpx.get(
            f"{_PLACES_URL}/places/{place_id}",
            headers={
                "X-Goog-Api-Key": s.google_maps_api_key,
                "X-Goog-FieldMask": "location,formattedAddress,displayName",
            },
            timeout=_TIMEOUT,
        )
        data = resp.json()
        loc = data.get("location") or {}
        if loc.get("latitude") is None:
            return None
        label = data.get("formattedAddress") or (data.get("displayName") or {}).get("text") or ""
        return {"lat": loc["latitude"], "lng": loc["longitude"], "label": label}
    except Exception:  # noqa: BLE001 - best-effort
        return None
