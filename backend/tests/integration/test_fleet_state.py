"""Derived bus status and office-set availability (plan 2026-07-28-001, U9/R22-R24).

Bus status was never written by any run event — only by the admin form — so a
bus mid-route read 'idle' until someone remembered to change it, and the
Dashboard's fleet tiles counted that hand-maintained field. It is now derived at
read time from the bus's current run, and the stored column is written by
nothing.

Availability is the one bus value the office still sets, because whether a bus
is in the workshop is not a function of its runs. It overrides the derivation on
every surface and carries the retired column's 'offline' meaning.

Covers:
- R22: derived from the current run; no admin action keeps it true
- R23: availability is office-set, overrides the derivation, and survives a
  depot-only save (the fleet map re-sends the bus's whole field set)
- R24: run delay is inherited by the bus and released when the run ends

Run with the stack up: RUN_INTEGRATION=1 pytest tests/integration/test_fleet_state.py -q
"""

import os
import random
import uuid

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="needs the local stack; set RUN_INTEGRATION=1",
)

BASE = os.environ.get("INTEGRATION_API_URL", "http://localhost:9001")
ADMIN = {"email": "admin@test.com", "password": "test1234."}


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE, timeout=20) as c:
        yield c


@pytest.fixture(scope="module")
def admin_headers(client):
    response = client.post("/api/auth/login", json=ADMIN)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _bus(client, admin_headers, bus_id: str) -> dict:
    response = client.get("/api/fleet/buses", headers=admin_headers)
    assert response.status_code == 200, response.text
    match = [b for b in response.json() if b["id"] == bus_id]
    assert match, f"bus {bus_id} missing from listing"
    return match[0]


@pytest.fixture(scope="module")
def fleet(client, admin_headers):
    """Throwaway bus + driver + school + morning route with one student.

    Seeded data holds none of the interesting states, and a completed run
    blocks its route for the rest of the day — so every lifecycle test builds
    its own fleet and tears it down.
    """
    marker = uuid.uuid4().hex[:6]
    created: dict = {"marker": marker}

    pin = str(random.randint(100000, 999999))
    response = client.post(
        "/api/accounts/drivers",
        json={"full_name": f"IT Fleet Driver {marker}",
              "email": f"it-fleet-driver-{marker}@test.local",
              "password": "test1234.", "phone": "+254711000900", "pin": pin},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["driver"] = response.json()
    created["pin"] = pin

    response = client.post(
        "/api/fleet/buses",
        json={"name": f"IT Fleet Bus {marker}", "capacity": 20,
              "driver_id": created["driver"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["bus"] = response.json()

    response = client.post(
        "/api/fleet/schools",
        json={"name": f"IT Fleet School {marker}", "lat": -1.29, "lng": 36.82},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["school"] = response.json()

    response = client.post(
        "/api/fleet/routes",
        json={"name": f"IT Fleet Route {marker}", "type": "morning",
              "bus_id": created["bus"]["id"], "school_id": created["school"]["id"]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["route"] = response.json()

    response = client.post(
        "/api/students",
        json={"name": f"IT Fleet Kid {marker}", "parent_name": "IT Fleet Parent",
              "parent_phone": "+254711000901",
              "parent_email": f"it-fleet-{marker}@test.local",
              "school_id": created["school"]["id"],
              "home_lat": -1.30, "home_lng": 36.79, "pickup_time": "06:30",
              "route_ids": [created["route"]["id"]]},
        headers=admin_headers,
    )
    assert response.status_code in (200, 201), response.text
    created["student"] = response.json()

    try:
        yield created
    finally:
        for run in client.get("/api/runs", headers=admin_headers).json():
            if run.get("bus_id") == created["bus"]["id"]:
                client.delete(f"/api/runs/{run['id']}", headers=admin_headers)
        client.delete(f"/api/students/{created['student']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/routes/{created['route']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/buses/{created['bus']['id']}", headers=admin_headers)
        client.delete(f"/api/fleet/schools/{created['school']['id']}", headers=admin_headers)
        client.delete(f"/api/accounts/drivers/{created['driver']['id']}", headers=admin_headers)


def test_new_bus_is_idle_and_in_service(client, admin_headers, fleet):
    """R22: a bus with no run derives idle, with nobody having set anything."""
    bus = _bus(client, admin_headers, fleet["bus"]["id"])
    assert bus["derived_status"] == "idle"
    assert bus["availability"] == "in-service"


def test_bus_derives_active_while_its_run_is_open(client, admin_headers, fleet):
    """R22: starting a run makes the bus active; ending it releases the bus.

    No admin action in either direction — this is the defect the derivation
    exists to fix.
    """
    driver_headers = {"Authorization": f"Bearer {client.post('/api/auth/pin-login', json={'pin': fleet['pin']}).json()['token']}"}

    response = client.post("/api/runs/driver/start",
                           json={"route_id": fleet["route"]["id"]}, headers=driver_headers)
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    try:
        assert _bus(client, admin_headers, fleet["bus"]["id"])["derived_status"] == "active"

        # update_run overwrites every column from the payload, so a status-only
        # PUT would null the run's bus and date. Resend the run's own fields —
        # this is what the admin Runs page does. (Closing that overwrite hazard
        # is U7's job, not this unit's.)
        started = client.get("/api/runs", headers=admin_headers).json()
        run = next(r for r in started if r["id"] == run_id)
        response = client.put(
            f"/api/runs/{run_id}",
            json={"bus_id": run["bus_id"], "route_id": run["route_id"],
                  "school_id": run["school_id"], "type": run["type"], "date": run["date"],
                  "start_time": run["start_time"], "status": "delayed",
                  "total_stops": run["total_stops"], "stops_completed": run["stops_completed"],
                  "total_students": run["total_students"]},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        # R24: delay is set once, on the run, and the bus inherits it.
        assert _bus(client, admin_headers, fleet["bus"]["id"])["derived_status"] == "delayed"
    finally:
        client.post("/api/runs/driver/end", json={"run_id": run_id}, headers=driver_headers)
        client.delete(f"/api/runs/{run_id}", headers=admin_headers)

    # R24: ending the run clears the inherited delay with nobody clearing it.
    assert _bus(client, admin_headers, fleet["bus"]["id"])["derived_status"] == "idle"


def test_availability_overrides_the_derivation(client, admin_headers, fleet):
    """R23: an out-of-service bus reads out of service regardless of its runs,
    and is distinguishable from one that simply has no run today."""
    bus = fleet["bus"]
    response = client.put(
        f"/api/fleet/buses/{bus['id']}",
        json={"name": bus["name"], "capacity": 20, "availability": "out-of-service"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    try:
        assert _bus(client, admin_headers, bus["id"])["derived_status"] == "out-of-service"
    finally:
        client.put(f"/api/fleet/buses/{bus['id']}",
                   json={"name": bus["name"], "capacity": 20, "availability": "in-service"},
                   headers=admin_headers)
    assert _bus(client, admin_headers, bus["id"])["derived_status"] == "idle"


def test_depot_only_save_does_not_reset_availability(client, admin_headers, fleet):
    """R23: the fleet map saves a depot by re-sending the bus's whole field set.

    Omitting availability must leave it alone — coalescing it to a default would
    silently return an out-of-service bus to service on every depot move.
    """
    bus = fleet["bus"]
    client.put(f"/api/fleet/buses/{bus['id']}",
               json={"name": bus["name"], "capacity": 20, "availability": "out-of-service"},
               headers=admin_headers)
    try:
        # Exactly the payload FleetMapPage sends: no availability key.
        response = client.put(
            f"/api/fleet/buses/{bus['id']}",
            json={"name": bus["name"], "plate_number": None, "driver_id": fleet["driver"]["id"],
                  "capacity": 20, "depot_lat": -1.31, "depot_lng": 36.80,
                  "depot_address": "Depot", "depot_provenance": "typed"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert _bus(client, admin_headers, bus["id"])["availability"] == "out-of-service"
    finally:
        client.put(f"/api/fleet/buses/{bus['id']}",
                   json={"name": bus["name"], "capacity": 20, "availability": "in-service"},
                   headers=admin_headers)


def test_admin_writes_never_touch_the_retired_status_column(client, admin_headers, fleet):
    """R22: no CRUD path writes live_buses.status any more.

    A client still sending it (mid-rollout) must not 422 and must not move the
    stored column.
    """
    bus = fleet["bus"]
    before = _bus(client, admin_headers, bus["id"])["status"]
    response = client.put(
        f"/api/fleet/buses/{bus['id']}",
        json={"name": bus["name"], "capacity": 20, "status": "delayed"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    after = _bus(client, admin_headers, bus["id"])
    assert after["status"] == before, "the retired column was written"
    assert after["derived_status"] == "idle", "a stale column value leaked into the derived status"
