from app.services import geo_service


def test_haversine_known_distance():
    # Nairobi CBD to ~1.5 km away.
    d = geo_service.haversine_m((-1.286389, 36.817223), (-1.300000, 36.820000))
    assert 1000 < d < 2500


def test_nearest_neighbour_starts_at_anchor():
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
