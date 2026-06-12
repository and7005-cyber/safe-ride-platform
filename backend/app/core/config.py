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
    africas_talking_api_key: str = Field(default="", alias="AFRICAS_TALKING_API_KEY")
    africas_talking_username: str = Field(default="", alias="AFRICAS_TALKING_USERNAME")

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
