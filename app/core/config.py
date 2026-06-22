from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.llm_provider import LLMProviderName


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
    rabbitmq_url: str = Field(
        default="amqp://clinic:clinic@localhost:5672/",
        alias="RABBITMQ_URL",
    )
    email_job_queue_name: str = Field(default="email_jobs", alias="EMAIL_JOB_QUEUE_NAME")
    email_job_dispatch_enabled: bool = Field(default=False, alias="EMAIL_JOB_DISPATCH_ENABLED")

    retell_api_key: str = Field(default="", alias="RETELL_API_KEY")
    retell_webhook_secret: str = Field(default="", alias="RETELL_WEBHOOK_SECRET")

    email_provider: str = Field(default="fake", alias="EMAIL_PROVIDER")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")
    human_escalation_notification_email: str = Field(
        default="clinic-staff@example.test",
        alias="HUMAN_ESCALATION_NOTIFICATION_EMAIL",
    )

    llm_provider: LLMProviderName = Field(default=LLMProviderName.FAKE, alias="LLM_PROVIDER")
    llm_enabled: bool = Field(default=True, alias="LLM_ENABLED")
    llm_max_primary_attempts: int = Field(
        default=2,
        ge=1,
        le=3,
        alias="LLM_MAX_PRIMARY_ATTEMPTS",
    )
    bedrock_model_id: str = Field(default="", alias="BEDROCK_MODEL_ID")
    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    bedrock_request_timeout_seconds: int = Field(
        default=10,
        alias="BEDROCK_REQUEST_TIMEOUT_SECONDS",
    )
    bedrock_max_retries: int = Field(default=0, alias="BEDROCK_MAX_RETRIES")
    bedrock_temperature: float = Field(default=0.0, alias="BEDROCK_TEMPERATURE")
    bedrock_max_tokens: int = Field(default=800, alias="BEDROCK_MAX_TOKENS")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")

    public_demo_mode: bool = Field(default=False, alias="PUBLIC_DEMO_MODE")
    # When public_demo_mode is true without guardrails, the demo is exposed without
    # rate limits — unsafe for production; enable PUBLIC_DEMO_GUARDRAILS_ENABLED instead.
    public_demo_guardrails_enabled: bool = Field(
        default=False,
        alias="PUBLIC_DEMO_GUARDRAILS_ENABLED",
    )
    trust_proxy_headers: bool = Field(default=False, alias="TRUST_PROXY_HEADERS")

    demo_chat_messages_per_minute_per_ip: int = Field(
        default=10,
        ge=1,
        alias="DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP",
    )
    demo_chat_messages_per_day_per_ip: int = Field(
        default=100,
        ge=1,
        alias="DEMO_CHAT_MESSAGES_PER_DAY_PER_IP",
    )
    demo_retell_tool_calls_per_minute_per_ip: int = Field(
        default=30,
        ge=1,
        alias="DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP",
    )
    demo_retell_tool_calls_per_day_per_ip: int = Field(
        default=300,
        ge=1,
        alias="DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP",
    )
    demo_appointments_per_day_per_ip: int = Field(
        default=5,
        ge=1,
        alias="DEMO_APPOINTMENTS_PER_DAY_PER_IP",
    )
    demo_confirmation_emails_per_day_per_ip: int = Field(
        default=5,
        ge=1,
        alias="DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP",
    )
    demo_global_chat_messages_per_day: int = Field(
        default=2000,
        ge=1,
        alias="DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY",
    )
    demo_global_appointments_per_day: int = Field(
        default=200,
        ge=1,
        alias="DEMO_GLOBAL_APPOINTMENTS_PER_DAY",
    )
    demo_global_confirmation_emails_per_day: int = Field(
        default=200,
        ge=1,
        alias="DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_bedrock_settings(self) -> "Settings":
        if self.llm_provider == LLMProviderName.BEDROCK and not self.bedrock_model_id.strip():
            raise ValueError("BEDROCK_MODEL_ID is required when LLM_PROVIDER is bedrock")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
