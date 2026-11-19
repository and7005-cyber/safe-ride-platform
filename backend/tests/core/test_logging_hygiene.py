"""Logging hygiene (hardening D20). httpx logs every request URL at INFO, and
the geocoder sends the server Maps key and the searched home address as query
parameters; with Lambda logging at INFO both would reach CloudWatch. Importing
the app pins the httpx and httpcore loggers to WARNING on both entry paths
(uvicorn loads app.main; the Lambda handler imports it).

Each entry path is checked in a fresh interpreter with the root logger at
INFO, the way a cold start sees it, so the pin is proved to come from the
import itself rather than from whatever an earlier test configured. The same
child first makes the request without the app imported, so the test cannot
pass vacuously if httpx ever stops logging request URLs."""

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]
MAPS_KEY = "AIza-hygiene-test-key"
HOME_ADDRESS = "12 Riverside Drive, Nairobi"

_CHILD = """
import importlib, json, logging, sys
import httpx

records = []

class Collect(logging.Handler):
    def emit(self, record):
        records.append([record.name, record.getMessage()])

root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(Collect())

def geocode():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with httpx.Client(transport=transport) as client:
        client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": sys.argv[2], "key": sys.argv[3], "region": "ke"},
        )

geocode()
before = list(records)
records.clear()

importlib.import_module(sys.argv[1])
levels = {name: logging.getLogger(name).getEffectiveLevel() for name in ("httpx", "httpcore")}
records.clear()  # only what the libraries emit after the import counts

geocode()
logging.getLogger("httpx").info("probe key=%s", sys.argv[3])
logging.getLogger("httpcore").info("probe key=%s", sys.argv[3])
print(json.dumps({"levels": levels, "before": before, "after": records}))
"""


@pytest.fixture(scope="module", params=["app.main", "app.lambda_handler"])
def child(request):
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD, request.param, HOME_ADDRESS, MAPS_KEY],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_without_the_app_httpx_logs_the_key_and_address_at_info(child):
    # The hazard the pin exists for: a bare httpx client at INFO writes the
    # full request URL, key and address included.
    messages = [message for name, message in child["before"] if name == "httpx"]
    assert any(MAPS_KEY in message for message in messages), child["before"]
    assert any("Riverside" in message for message in messages), child["before"]


def test_importing_the_app_pins_httpx_and_httpcore_to_warning(child):
    assert child["levels"]["httpx"] >= logging.WARNING, child["levels"]
    assert child["levels"]["httpcore"] >= logging.WARNING, child["levels"]


def test_an_info_record_through_httpx_is_not_handled_after_the_import(child):
    assert [
        record for record in child["after"]
        if record[0].startswith(("httpx", "httpcore"))
    ] == []
    assert not any(MAPS_KEY in message for _, message in child["after"])
