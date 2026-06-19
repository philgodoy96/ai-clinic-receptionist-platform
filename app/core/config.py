from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="ai-clinic-receptionist-platform", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")

    database_url: str = Field(
        default="postgresql+psycopg://clinic:clinic@localhost:5432/clinic_receptionist",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    appointment_hold_ttl_seconds: int = Field(
        default=300,
        alias="APPOINTMENT_HOLD_TTL_SECONDS",
    )
    rabbitmq_url: str = Field(default="amqp://clinic:clinic@localhost:5672/", alias="RABBITMQ_URL")

    retell_api_key: str = Field(default="", alias="RETELL_API_KEY")
    retell_webhook_secret: str = Field(default="", alias="RETELL_WEBHOOK_SECRET")

    email_provider: str = Field(default="fake", alias="EMAIL_PROVIDER")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")

    llm_provider: str = Field(default="fake", alias="LLM_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()