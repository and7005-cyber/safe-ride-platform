"""JSON push secrets survive base64url transit (deploy) and raw local .env.

The CloudFormation --parameter-overrides shorthand mangles values with
quotes/commas/braces, so deploy-backend.sh base64url-encodes the Firebase
JSON blobs; config._maybe_b64_json decodes them while leaving raw JSON and
file paths untouched.
"""
import base64
import json

from app.core.config import Settings, _maybe_b64_json

SAMPLE = '{"apiKey":"AIzaSyExample","authDomain":"safe-ride-kenya.firebaseapp.com","projectId":"safe-ride-kenya"}'


def _b64url(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def test_raw_json_passthrough():
    assert _maybe_b64_json(SAMPLE) == SAMPLE


def test_base64url_json_is_decoded():
    decoded = _maybe_b64_json(_b64url(SAMPLE))
    assert decoded == SAMPLE
    assert json.loads(decoded)["projectId"] == "safe-ride-kenya"


def test_padded_and_unpadded_base64url_both_decode():
    padded = base64.urlsafe_b64encode(SAMPLE.encode()).decode()  # keeps '='
    assert _maybe_b64_json(padded) == SAMPLE
    assert _maybe_b64_json(_b64url(SAMPLE)) == SAMPLE  # stripped '='


def test_empty_stays_empty():
    assert _maybe_b64_json("") == ""


def test_file_path_untouched():
    # A real deployment might pass a path; not base64-of-JSON, so left as-is.
    assert _maybe_b64_json("/etc/secrets/sa.json") == "/etc/secrets/sa.json"


def test_settings_validator_decodes_both_json_fields():
    s = Settings(
        FIREBASE_SERVICE_ACCOUNT_JSON=_b64url(SAMPLE),
        FIREBASE_WEB_CONFIG_JSON=_b64url(SAMPLE),
        VAPID_PUBLIC_KEY="untouched-key",
    )
    assert s.firebase_service_account_json == SAMPLE
    assert s.firebase_web_config_json == SAMPLE
    assert s.vapid_public_key == "untouched-key"  # non-JSON fields not decoded


class _FakeSSM:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def get_parameter(self, Name, WithDecryption):  # noqa: N803 — boto3 kwarg name
        self.calls.append((Name, WithDecryption))
        return {"Parameter": {"Value": self.values[Name]}}


def _inject_fake_boto3(monkeypatch, ssm):
    import sys
    import types

    fake = types.ModuleType("boto3")
    fake.client = lambda service: ssm  # noqa: ARG005
    monkeypatch.setitem(sys.modules, "boto3", fake)


def test_ssm_fetch_populates_empty_json_fields(monkeypatch):
    ssm = _FakeSSM({
        "/saferide/firebase-service-account-json": SAMPLE,
        "/saferide/firebase-web-config-json": SAMPLE,
    })
    _inject_fake_boto3(monkeypatch, ssm)
    s = Settings(
        FIREBASE_SERVICE_ACCOUNT_SSM="/saferide/firebase-service-account-json",
        FIREBASE_WEB_CONFIG_SSM="/saferide/firebase-web-config-json",
    )
    assert s.firebase_service_account_json == SAMPLE
    assert s.firebase_web_config_json == SAMPLE
    assert all(decrypt is True for _, decrypt in ssm.calls)  # SecureString


def test_direct_json_wins_over_ssm_name(monkeypatch):
    # When the JSON is already provided (local .env), SSM is never consulted.
    ssm = _FakeSSM({"/x": "should-not-be-read"})
    _inject_fake_boto3(monkeypatch, ssm)
    s = Settings(
        FIREBASE_SERVICE_ACCOUNT_JSON=SAMPLE,
        FIREBASE_SERVICE_ACCOUNT_SSM="/x",
    )
    assert s.firebase_service_account_json == SAMPLE
    assert ssm.calls == []


def test_ssm_fetch_failure_degrades_to_empty(monkeypatch):
    class _Boom:
        def get_parameter(self, **_):
            raise RuntimeError("ssm unavailable")

    _inject_fake_boto3(monkeypatch, _Boom())
    s = Settings(FIREBASE_SERVICE_ACCOUNT_SSM="/saferide/firebase-service-account-json")
    assert s.firebase_service_account_json == ""  # boot survives; FCM stays off


def test_no_ssm_name_never_imports_boto3(monkeypatch):
    # The common local case: no SSM names set → the boto3 path is never taken.
    import sys

    monkeypatch.setitem(sys.modules, "boto3", None)  # would raise if imported
    s = Settings(FIREBASE_SERVICE_ACCOUNT_JSON=SAMPLE)
    assert s.firebase_service_account_json == SAMPLE
