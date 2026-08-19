"""Duration-matrix provider (U3): chunked computeRouteMatrix with a
deterministic whole-or-nothing offline fallback."""
import pytest

from app.services import geo_service


@pytest.fixture
def no_keys(monkeypatch):
    """Force the offline path regardless of the ambient .env (which now carries a
    real Google key for the running stack)."""

    class _Settings:
        google_maps_api_key = ""
        mapbox_token = ""

    monkeypatch.setattr(geo_service, "get_settings", lambda: _Settings())


@pytest.fixture
def google_key(monkeypatch):
    class _Settings:
        google_maps_api_key = "test-key"
        mapbox_token = ""

    monkeypatch.setattr(geo_service, "get_settings", lambda: _Settings())


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _points(n):
    """Index-recoverable synthetic coordinates around Nairobi."""
    return [{"lat": -1.0 - i * 0.01, "lng": 36.0 + i * 0.01} for i in range(n)]


def _global_index(waypoint):
    lat = waypoint["waypoint"]["location"]["latLng"]["latitude"]
    return round((-1.0 - lat) / 0.01)


def _fake_post_factory(calls):
    """A httpx.post stand-in answering computeRouteMatrix blocks with the
    directed rule duration(i→j) = 1000*i + j seconds (asymmetric by design)."""

    def fake_post(url, *, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "json": json})
        elements = []
        for oi, origin in enumerate(json["origins"]):
            for dj, dest in enumerate(json["destinations"]):
                gi, gj = _global_index(origin), _global_index(dest)
                elements.append(
                    {
                        "originIndex": oi,
                        "destinationIndex": dj,
                        "duration": f"{1000 * gi + gj}s" if gi != gj else "0s",
                        "condition": "ROUTE_EXISTS",
                    }
                )
        return _Resp(elements)

    return fake_post


def test_chunking_33_points_splits_and_reassembles_by_index(google_key, monkeypatch):
    calls = []
    monkeypatch.setattr(geo_service.httpx, "post", _fake_post_factory(calls))
    pts = _points(33)  # 33×33 = 1089 elements > the 625-per-request cap
    out = geo_service.compute_duration_matrix(pts)

    assert len(calls) >= 2
    for call in calls:
        assert call["url"] == geo_service._ROUTE_MATRIX_URL
        assert call["headers"]["X-Goog-Api-Key"] == "test-key"
        for field in ("originIndex", "destinationIndex", "duration", "condition"):
            assert field in call["headers"]["X-Goog-FieldMask"]
        assert call["json"]["routingPreference"] == "TRAFFIC_UNAWARE"
        assert len(call["json"]["origins"]) * len(call["json"]["destinations"]) <= 625
    # Every one of the 1089 elements was requested exactly once.
    assert sum(len(c["json"]["origins"]) * len(c["json"]["destinations"]) for c in calls) == 1089

    assert out["provider"] == "google-routes"
    assert out["degraded"] is False
    assert len(out["matrix"]) == 33 and all(len(row) == 33 for row in out["matrix"])
    for i in range(33):
        for j in range(33):
            expected = 0 if i == j else 1000 * i + j
            assert out["matrix"][i][j] == expected


def test_keyless_returns_offline_matrix_without_http(no_keys, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("keyless path must make zero HTTP calls")

    monkeypatch.setattr(geo_service.httpx, "post", boom)
    monkeypatch.setattr(geo_service.httpx, "get", boom)
    pts = _points(4)
    out = geo_service.compute_duration_matrix(pts)
    assert out["provider"] == "offline"
    assert out["degraded"] is True
    assert all(out["matrix"][i][i] == 0 for i in range(4))
    # Haversine × 1.4 circuity ÷ 30 km/h, in whole seconds.
    dist = geo_service.haversine_m((pts[0]["lat"], pts[0]["lng"]), (pts[1]["lat"], pts[1]["lng"]))
    assert out["matrix"][0][1] == int(dist * 1.4 / (30 * 1000 / 3600))
    assert all(out["matrix"][i][j] > 0 for i in range(4) for j in range(4) if i != j)


def test_directed_asymmetry_in_response_is_preserved(google_key, monkeypatch):
    calls = []
    monkeypatch.setattr(geo_service.httpx, "post", _fake_post_factory(calls))
    out = geo_service.compute_duration_matrix(_points(3))
    assert out["provider"] == "google-routes"
    # The mocked rule is directed: A→B = 1, B→A = 1000.
    assert out["matrix"][0][1] == 1
    assert out["matrix"][1][0] == 1000
    assert out["matrix"][0][1] != out["matrix"][1][0]


def test_exception_on_second_chunk_falls_back_whole(google_key, monkeypatch):
    calls = []
    real_fake = _fake_post_factory(calls)

    def flaky_post(url, **kwargs):
        if len(calls) >= 1:  # first chunk succeeds, second explodes
            raise RuntimeError("network down")
        return real_fake(url, **kwargs)

    monkeypatch.setattr(geo_service.httpx, "post", flaky_post)
    pts = _points(33)  # needs ≥2 chunks
    out = geo_service.compute_duration_matrix(pts)
    assert len(calls) == 1  # the first chunk really did succeed before the failure
    assert out["provider"] == "offline"
    assert out["degraded"] is True
    # No mixing: the whole matrix is the offline estimate, chunk 1 discarded.
    assert out["matrix"] == geo_service._offline_duration_matrix(pts)


def test_self_durations_are_zero_on_google_path(google_key, monkeypatch):
    calls = []
    monkeypatch.setattr(geo_service.httpx, "post", _fake_post_factory(calls))
    out = geo_service.compute_duration_matrix(_points(5))
    assert out["provider"] == "google-routes"
    assert all(out["matrix"][i][i] == 0 for i in range(5))


def test_offline_matrix_is_deterministic(no_keys):
    pts = _points(6)
    first = geo_service.compute_duration_matrix(pts)
    second = geo_service.compute_duration_matrix(pts)
    assert first == second
    assert first["degraded"] is True
