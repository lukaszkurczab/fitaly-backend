from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App metadata
    APP_NAME: str = "Fitaly Food Scanner API"
    DESCRIPTION: str = "Backend API for Fitaly mobile application."
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"

    # Runtime environment
    ENVIRONMENT: Literal["local", "development", "staging", "production"] = "local"

    # Integrations
    OPENAI_API_KEY: str = ""
    FIREBASE_PROJECT_ID: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""
    FIRESTORE_DATABASE_ID: str = "(default)"
    EAGER_FIREBASE_INIT: bool = True
    CORS_ORIGINS: str = ""
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "development"
    SENTRY_TRACES_SAMPLE_RATE: float = Field(default=0.01, ge=0.0, le=1.0)

    # Product limits
    AI_CREDITS_FREE: int = Field(default=100, ge=0)
    AI_CREDITS_PREMIUM: int = Field(default=800, ge=0)
    AI_CREDIT_COST_CHAT: int = Field(default=1, ge=0)
    AI_CREDIT_COST_TEXT_MEAL: int = Field(default=1, ge=0)
    AI_CREDIT_COST_PHOTO: int = Field(default=5, ge=0)
    AI_CHAT_ENABLED: bool = True
    AI_GATEWAY_ENABLED: bool = True
    TELEMETRY_ENABLED: bool = False
    STATE_ENABLED: bool = True
    HABITS_ENABLED: bool = True
    SMART_REMINDERS_ENABLED: bool = True
    WEEKLY_REPORTS_ENABLED: bool = True
    AI_REJECT_COST: float = Field(default=0.2, ge=0.0)
    AI_LOCAL_COST: float = Field(default=0.5, ge=0.0)

    # Billing integrations
    REVENUECAT_WEBHOOK_SECRET: str = ""
    REVENUECAT_API_KEY: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip().lower()
        if normalized in {"prod", "smoke"}:
            return "production"
        if normalized == "dev":
            return "development"
        return normalized


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
