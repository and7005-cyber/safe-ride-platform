import base64
import binascii
import logging
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("saferide.config")

# GPS tracking system defaults (GPS plan U7). Module constants, deliberately
# NOT Settings fields yet: U11 makes each env-overridable and threads it
# through the SAM template per the live-parity rule, and adds the per-school
# resolver on top. Until then every reader takes these.
GPS_POSITION_RETENTION_DAYS = 90     # trail retention when the school has none set
GPS_PURGE_BATCH_ROWS = 2000          # trail rows deleted per Start Run purge pass
GPS_PURGE_STATEMENT_TIMEOUT_MS = 2000
GPS_ACTION_KEY_TTL_DAYS = 7          # idempotency keys purged after this
GPS_KEY_LOCK_TIMEOUT_MS = 2000       # wait on an in-flight duplicate before 409
GPS_FIX_ACCURACY_CAP_M = 200.0       # accuracy above this is `coarse`
GPS_CLOCK_SKEW_TOLERANCE_S = 30      # capture time this far ahead of receipt is skew


def _maybe_b64_json(value: str) -> str:
    """Accept a JSON secret either raw or base64url-encoded.

    Raw JSON (starts with ``{``) is used as-is — that is what ``backend/.env``
    and a file path both look like. CloudFormation's ``--parameter-overrides``
    shorthand mangles values containing quotes/commas/braces, so the deploy
    script base64url-encodes these JSON blobs before injecting them into the
    Lambda env; here we decode them back. Anything that is not valid
    base64url-of-JSON is returned untouched (e.g. a real file path)."""
    v = value.strip()
    if not v or v.startswith("{"):
        return v
    try:
        decoded = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4)).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return v
    return decoded if decoded.lstrip().startswith("{") else v


class Settings(BaseSettings):
    app_env: str = Field(default="production", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql://saferide:saferide@localhost:5432/saferide",
        alias="DATABASE_URL",
    )
    pin_pepper: str = Field(default="saferide-local-pin-pepper", alias="PIN_PEPPER")
    # Provider second factor (U10): TOTP secrets are derived per account from
    # this pepper + the row's salt and never stored. Production holds it in
    # SSM (/saferide/totp-pepper); rotating it forces every provider to
    # re-enrol (the stored pepper key id names the cause).
    totp_pepper: str = Field(default="saferide-local-totp-pepper", alias="TOTP_PEPPER")
    app_base_url: str = Field(default="http://localhost:5173", alias="APP_BASE_URL")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        alias="CORS_ORIGINS",
    )
    demo_school_id: str = Field(
        default="11111111-1111-1111-1111-111111111111",
        alias="DEMO_SCHOOL_ID",
    )
    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")
    # Tenancy compatibility window (U5): while False, a school-scoped request
    # without an X-School-Id header falls back to the caller's only (or last
    # used) school; flipping to True makes the header mandatory (400).
    scope_header_required: bool = Field(default=False, alias="SCOPE_HEADER_REQUIRED")
    # Minimum turnaround between a bus's back-to-back trips in one period (U6/R20):
    # stands in for the unmodeled gate<->wave deadhead. Global for this pass.
    turnaround_buffer_min: int = Field(default=15, alias="TURNAROUND_BUFFER_MIN")
    # Multiplies only the coarse per-IP auth budgets (login/signup safety nets
    # sized for shared NATs). Local test stacks raise it so one certification
    # run's account churn fits a single budget window; the default of 1 is the
    # production posture. Per-account and PIN budgets never scale.
    auth_ip_rate_multiplier: int = Field(default=1, alias="AUTH_IP_RATE_MULTIPLIER")
    # Maps provider for geocoding + route optimisation (#4, #9). When neither
    # key is set the app falls back to free OSM Nominatim geocoding and an
    # offline nearest-neighbour optimiser.
    google_maps_api_key: str = Field(default="", alias="GOOGLE_MAPS_API_KEY")
    mapbox_token: str = Field(default="", alias="MAPBOX_TOKEN")
    # Firebase Cloud Messaging. Service account JSON (inline or a file path)
    # enables backend sends; the web config + VAPID key are served to the
    # frontend so browsers can register device tokens.
    firebase_service_account_json: str = Field(default="", alias="FIREBASE_SERVICE_ACCOUNT_JSON")
    firebase_web_config_json: str = Field(default="", alias="FIREBASE_WEB_CONFIG_JSON")
    firebase_vapid_key: str = Field(default="", alias="FIREBASE_VAPID_KEY")
    # SSM SecureString parameter names for the two JSON blobs above. Set on the
    # Lambda (where the JSON is too large / special-char-laden to pass through a
    # CloudFormation parameter); the blob is fetched once at startup. Locally the
    # JSON fields are set directly and these stay empty.
    firebase_service_account_ssm: str = Field(default="", alias="FIREBASE_SERVICE_ACCOUNT_SSM")
    firebase_web_config_ssm: str = Field(default="", alias="FIREBASE_WEB_CONFIG_SSM")
    # Plain Web Push (VAPID) fallback, independent of Firebase.
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field(default="mailto:admin@saferidekenya.com", alias="VAPID_SUBJECT")

    @field_validator("firebase_service_account_json", "firebase_web_config_json", mode="after")
    @classmethod
    def _decode_json_secret(cls, v: str) -> str:
        return _maybe_b64_json(v)

    @model_validator(mode="after")
    def _resolve_ssm_secrets(self) -> "Settings":
        """Fetch the Firebase JSON blobs from SSM when only a param name is set.

        The service-account JSON (~2.3 KB, with a private key) does not fit the
        4 KB Lambda env budget once base64-encoded and cannot pass through a
        CloudFormation --parameter-overrides value intact, so on the Lambda it
        lives in an SSM SecureString and is pulled in here at first settings
        load (cached by get_settings). Best-effort: a fetch failure leaves the
        field empty and push degrades to simulated / web-push, never crashes."""
        pairs = (
            ("firebase_service_account_json", self.firebase_service_account_ssm),
            ("firebase_web_config_json", self.firebase_web_config_ssm),
        )
        if not any(name for _, name in pairs):
            return self
        client = None
        for attr, name in pairs:
            if getattr(self, attr) or not name:
                continue
            try:
                if client is None:
                    import boto3  # lazy: only imported on the Lambda path

                    client = boto3.client("ssm")
                value = client.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
                object.__setattr__(self, attr, _maybe_b64_json(value))
            except Exception:  # noqa: BLE001 — degrade to no-FCM, never crash boot
                logger.exception("Could not load %s from SSM %s; that channel stays off", attr, name)
        return self

    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_local(self) -> bool:
        return self.app_env.lower() == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
