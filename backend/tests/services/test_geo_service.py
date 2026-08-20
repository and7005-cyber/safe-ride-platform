import datetime as dt

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


def test_haversine_known_distance():
    # Nairobi CBD to ~1.5 km away.
    d = geo_service.haversine_m((-1.286389, 36.817223), (-1.300000, 36.820000))
    assert 1000 < d < 2500


def test_nearest_neighbour_starts_at_anchor(no_keys):
    pts = [
        {"lat": -1.30, "lng": 36.83, "label": "C"},
        {"lat": -1.29, "lng": 36.82, "label": "B"},
        {"lat": -1.286, "lng": 36.817, "label": "A"},
    ]
    start = {"lat": -1.286, "lng": 36.817}
    out = geo_service.optimize_route(pts, start=start)
    assert out["provider"] == "nearest-neighbour"
    assert [p["label"] for p in out["ordered"]] == ["A", "B", "C"]


def test_optimize_trivial_when_one_point():
    out = geo_service.optimize_route([{"lat": -1.1, "lng": 36.1}])
    assert out["provider"] == "trivial"
    assert len(out["ordered"]) == 1


def test_optimize_ignores_unlocated_points():
    pts = [{"lat": None, "lng": None, "label": "X"}, {"lat": -1.1, "lng": 36.1, "label": "Y"}]
    out = geo_service.optimize_route(pts)
    assert [p["label"] for p in out["ordered"]] == ["Y"]


def test_optimized_order_offline_anchors_on_school(no_keys):
    students = [
        {"lat": -1.33, "lng": 36.86, "label": "Far"},
        {"lat": -1.30, "lng": 36.83, "label": "Mid"},
        {"lat": -1.29, "lng": 36.82, "label": "Near"},
    ]
    school = {"lat": -1.286, "lng": 36.817, "is_school": True}
    ordered = geo_service.optimized_order(students, school)
    assert [s["label"] for s in ordered] == ["Near", "Mid", "Far"]


# Provider-signalling wrapper (U6/R10): geometry writes gate on the order
# provider, which the bare optimized_order list return cannot carry.

STUDENTS = [
    {"lat": -1.33, "lng": 36.86, "label": "Far"},
    {"lat": -1.30, "lng": 36.83, "label": "Mid"},
    {"lat": -1.29, "lng": 36.82, "label": "Near"},
]
SCHOOL = {"lat": -1.286, "lng": 36.817, "is_school": True}


def test_optimized_order_with_provider_offline_signals_nearest_neighbour(no_keys):
    out = geo_service.optimized_order_with_provider(STUDENTS, SCHOOL)
    assert out["provider"] == "nearest-neighbour"
    assert [s["label"] for s in out["ordered"]] == ["Near", "Mid", "Far"]


def test_optimized_order_with_provider_trivial_for_single_point(no_keys):
    out = geo_service.optimized_order_with_provider([STUDENTS[0]], SCHOOL)
    assert out["provider"] == "trivial"
    assert [s["label"] for s in out["ordered"]] == ["Far"]
    assert geo_service.optimized_order_with_provider([], SCHOOL) == {
        "ordered": [], "provider": "trivial",
    }


@pytest.fixture
def google_key(monkeypatch):
    class _Settings:
        google_maps_api_key = "test-key"
        mapbox_token = ""

    monkeypatch.setattr(geo_service, "get_settings", lambda: _Settings())


def test_optimized_order_with_provider_reports_google(google_key, monkeypatch):
    monkeypatch.setattr(
        geo_service, "_routes_call",
        lambda *a, **k: {"optimizedIntermediateWaypointIndex": [2, 0, 1]},
    )
    out = geo_service.optimized_order_with_provider(STUDENTS, SCHOOL)
    assert out["provider"] == "google"
    assert [s["label"] for s in out["ordered"]] == ["Near", "Far", "Mid"]
    # The legacy list API delegates to the wrapper: same sequence.
    assert geo_service.optimized_order(STUDENTS, SCHOOL) == out["ordered"]


def test_optimized_order_with_provider_falls_back_on_provider_error(google_key, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(geo_service, "_routes_call", boom)
    out = geo_service.optimized_order_with_provider(STUDENTS, SCHOOL)
    assert out["provider"] == "nearest-neighbour"


def test_optimized_order_with_provider_falls_back_on_short_order(google_key, monkeypatch):
    # A malformed optimiser response (wrong index count) must not be trusted.
    monkeypatch.setattr(
        geo_service, "_routes_call",
        lambda *a, **k: {"optimizedIntermediateWaypointIndex": [0]},
    )
    out = geo_service.optimized_order_with_provider(STUDENTS, SCHOOL)
    assert out["provider"] == "nearest-neighbour"


def test_optimized_order_with_provider_without_school_is_offline(google_key):
    # The Google optimiser is anchored on the school; without one there is no
    # google signal even with a key.
    out = geo_service.optimized_order_with_provider(STUDENTS, None)
    assert out["provider"] == "nearest-neighbour"


def test_route_geometry_offline_estimates_legs(no_keys):
    seq = [
        {"lat": -1.286, "lng": 36.817, "label": "A"},
        {"lat": -1.30, "lng": 36.82, "label": "B"},
        {"lat": -1.33, "lng": 36.86, "label": "C"},
    ]
    geom = geo_service.route_geometry(seq)
    assert geom["provider"] == "offline"
    assert geom["polyline"] is None
    assert len(geom["legs"]) == 2
    assert geom["total_distance_m"] > 0
    assert all(leg["duration_s"] > 0 for leg in geom["legs"])


def test_route_geometry_trivial_for_single_point(no_keys):
    geom = geo_service.route_geometry([{"lat": -1.1, "lng": 36.1, "label": "only"}])
    assert geom["provider"] == "trivial"
    assert geom["legs"] == []


def test_duration_parsing():
    assert geo_service._dur_s("456s") == 456
    assert geo_service._dur_s("12.4s") == 12
    assert geo_service._dur_s(90) == 90
    assert geo_service._dur_s(None) is None
    assert geo_service._dur_s("oops") is None


def test_next_departure_is_future_and_matches_time():
    when = geo_service.next_departure("07:30", default="07:00")
    now = dt.datetime.now(tz=geo_service._EAT)
    assert when > now
    assert (when.hour, when.minute) == (7, 30)


def test_next_departure_falls_back_to_default_on_garbage():
    when = geo_service.next_departure(None, default="15:30")
    assert (when.hour, when.minute) == (15, 30)


def test_reverse_geocode_not_found_without_key(no_keys):
    assert geo_service.reverse_geocode(-1.286, 36.817) == {"found": False}


def test_reverse_geocode_not_found_without_coordinates(no_keys):
    assert geo_service.reverse_geocode(None, 36.817) == {"found": False}
    assert geo_service.reverse_geocode(-1.286, None) == {"found": False}


def test_reverse_geocode_swallows_provider_errors(monkeypatch):
    class _Settings:
        google_maps_api_key = "test-key"
        mapbox_token = ""

    monkeypatch.setattr(geo_service, "get_settings", lambda: _Settings())

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(geo_service.httpx, "get", boom)
    assert geo_service.reverse_geocode(-1.286, 36.817) == {"found": False}


def test_places_autocomplete_empty_without_key(no_keys):
    assert geo_service.places_autocomplete("Yaya Centre") == []


def test_place_details_none_without_key(no_keys):
    assert geo_service.place_details("anything") is None


# --- cumulative_ride_seconds: the ONE direction-aware ride walk ---------------
# Shared by fleet_plan_dao._recompute_legs, fleet_plan_dao._materialize_slot_in
# and fleet_dao._plan_fixed_compute — these tests pin its arithmetic and its
# decided short-legs guard (pad observably, never crash or misalign).

def test_cumulative_rides_morning_walks_backward_with_leading_depot_offset():
    # Sequence depot -> s0 -> s1 -> gate: legs [depot->s0, s0->s1, s1->gate].
    # The depot leg never enters a ride; rides are cumulative TO the gate.
    rides, short = geo_service.cumulative_ride_seconds(
        [100, 200, 300], 2, is_afternoon=False, has_depot=True
    )
    assert rides == [500.0, 300.0]
    assert short is False


def test_cumulative_rides_morning_without_depot():
    rides, short = geo_service.cumulative_ride_seconds(
        [200, 300], 2, is_afternoon=False, has_depot=False
    )
    assert rides == [500.0, 300.0]
    assert short is False


def test_cumulative_rides_afternoon_walks_forward_ignoring_trailing_depot():
    # Sequence gate -> s0 -> s1 -> depot: the trailing depot leg (999) never
    # enters any ride.
    rides, short = geo_service.cumulative_ride_seconds(
        [100, 200, 999], 2, is_afternoon=True, has_depot=True
    )
    assert rides == [100.0, 300.0]
    assert short is False


def test_cumulative_rides_none_durations_count_zero():
    rides, short = geo_service.cumulative_ride_seconds(
        [None, 50], 2, is_afternoon=True, has_depot=False
    )
    assert rides == [0.0, 50.0]
    assert short is False


def test_cumulative_rides_short_legs_pad_and_flag_never_crash():
    # fixed_sequence_geometry can return fewer legs than sequence-1 when
    # points lack coordinates: the walk pads missing legs with 0 at the tail
    # and reports short=True — the caller raises its degraded flag. Before
    # consolidation the three call sites diverged here (IndexError / silent
    # zeros / zip misalignment).
    rides, short = geo_service.cumulative_ride_seconds(
        [120], 3, is_afternoon=False, has_depot=True
    )
    assert short is True
    assert rides == [0.0, 0.0, 0.0]  # the lone leg is the depot leg

    rides, short = geo_service.cumulative_ride_seconds(
        [100], 3, is_afternoon=True, has_depot=False
    )
    assert short is True
    assert rides == [100.0, 100.0, 100.0]  # earlier stops keep alignment
