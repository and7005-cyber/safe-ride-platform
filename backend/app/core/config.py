from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = Field(default="production", alias="APP_ENV")
    database_url: str = Field(
        default="postgresql://saferide:saferide@localhost:5432/saferide",
        alias="DATABASE_URL",
    )
    pin_pepper: str = Field(default="saferide-local-pin-pepper", alias="PIN_PEPPER")
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
    # Plain Web Push (VAPID) fallback, independent of Firebase.
    vapid_public_key: str = Field(default="", alias="VAPID_PUBLIC_KEY")
    vapid_private_key: str = Field(default="", alias="VAPID_PRIVATE_KEY")
    vapid_subject: str = Field(default="mailto:admin@saferidekenya.com", alias="VAPID_SUBJECT")
    bus_approaching_radius_m: int = Field(default=1000, alias="BUS_APPROACHING_RADIUS_M")

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
