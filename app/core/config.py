import os
import re
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.ai.llm_provider import GroqResponseFormat, LLMProviderName
from app.domain.receptionist.enums import ReceptionistResponseMode

_CLINIC_TIME_HH_MM_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_CLINIC_BUSINESS_WEEKDAYS = frozenset(
    {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"},
)
_NON_PRODUCTION_ENVS = frozenset({"local", "test", "development", "dev", "ci"})
_LOCALHOST_URL_MARKERS = ("localhost", "127.0.0.1")


class Settings(BaseSettings):
    app_name: str = Field(default="ai-clinic-receptionist-platform", alias="APP_NAME")
    app_env: str = Field(default="local", alias="APP_ENV")
    app_debug: bool = Field(default=True, alias="APP_DEBUG")
    api_v1_prefix: str = Field(default="/api/v1", alias="API_V1_PREFIX")

    clinic_timezone: str = Field(default="America/New_York", alias="CLINIC_TIMEZONE")
    clinic_business_days: str = Field(
        default="monday,tuesday,wednesday,thursday,friday",
        alias="CLINIC_BUSINESS_DAYS",
    )
    clinic_business_hours_start: str = Field(
        default="09:00",
        alias="CLINIC_BUSINESS_HOURS_START",
    )
    clinic_business_hours_end: str = Field(default="17:00", alias="CLINIC_BUSINESS_HOURS_END")
    clinic_name: str = Field(default="Demo Clinic", alias="CLINIC_NAME")
    clinic_locale: str = Field(default="en-US", alias="CLINIC_LOCALE")

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
    email_job_max_attempts: int = Field(default=3, ge=1, alias="EMAIL_JOB_MAX_ATTEMPTS")
    email_job_backoff_base_seconds: int = Field(
        default=30,
        ge=1,
        alias="EMAIL_JOB_BACKOFF_BASE_SECONDS",
    )
    email_job_backoff_max_seconds: int = Field(
        default=900,
        ge=1,
        alias="EMAIL_JOB_BACKOFF_MAX_SECONDS",
    )
    email_job_lock_ttl_seconds: int = Field(
        default=300,
        ge=1,
        alias="EMAIL_JOB_LOCK_TTL_SECONDS",
    )

    retell_enabled: bool = Field(default=False, alias="RETELL_ENABLED")
    retell_api_key: str = Field(default="", alias="RETELL_API_KEY", repr=False)
    retell_webhook_verification_enabled: bool = Field(
        default=True,
        alias="RETELL_WEBHOOK_VERIFICATION_ENABLED",
    )
    retell_webhook_secret: str | None = Field(
        default=None,
        alias="RETELL_WEBHOOK_SECRET",
        repr=False,
    )
    retell_allow_insecure_webhooks: bool = Field(
        default=False,
        alias="RETELL_ALLOW_INSECURE_WEBHOOKS",
    )
    retell_signature_header_name: str = Field(
        default="x-retell-signature",
        alias="RETELL_SIGNATURE_HEADER_NAME",
    )
    retell_request_max_body_bytes: int = Field(
        default=262144,
        ge=1,
        alias="RETELL_REQUEST_MAX_BODY_BYTES",
    )

    email_provider: str = Field(default="fake", alias="EMAIL_PROVIDER")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY", repr=False)
    email_from_address: str = Field(
        default="clinic-demo@example.test",
        alias="EMAIL_FROM_ADDRESS",
    )
    email_reply_to: str = Field(default="", alias="EMAIL_REPLY_TO")
    email_provider_request_timeout_seconds: int = Field(
        default=10,
        ge=1,
        alias="EMAIL_PROVIDER_REQUEST_TIMEOUT_SECONDS",
    )
    human_escalation_notification_email: str = Field(
        default="clinic-staff@example.test",
        alias="HUMAN_ESCALATION_NOTIFICATION_EMAIL",
    )

    llm_provider: LLMProviderName = Field(default=LLMProviderName.FAKE, alias="LLM_PROVIDER")
    llm_primary_provider: LLMProviderName | None = Field(
        default=None,
        alias="LLM_PRIMARY_PROVIDER",
    )
    llm_enabled: bool = Field(default=True, alias="LLM_ENABLED")
    llm_max_primary_attempts: int = Field(
        default=2,
        ge=1,
        le=3,
        alias="LLM_MAX_PRIMARY_ATTEMPTS",
    )
    llm_fallback_enabled: bool = Field(default=False, alias="LLM_FALLBACK_ENABLED")
    llm_fallback_provider: LLMProviderName | None = Field(
        default=None,
        alias="LLM_FALLBACK_PROVIDER",
    )
    llm_max_fallback_attempts: int = Field(
        default=1,
        ge=1,
        le=2,
        alias="LLM_MAX_FALLBACK_ATTEMPTS",
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
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY", repr=False)
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY", repr=False)
    groq_model: str = Field(default="", alias="GROQ_MODEL")
    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        alias="GROQ_BASE_URL",
    )
    groq_request_timeout_seconds: int = Field(
        default=10,
        ge=1,
        alias="GROQ_REQUEST_TIMEOUT_SECONDS",
    )
    groq_max_output_tokens: int = Field(
        default=800,
        ge=1,
        alias="GROQ_MAX_OUTPUT_TOKENS",
    )
    groq_temperature: float = Field(default=0.0, ge=0.0, le=2.0, alias="GROQ_TEMPERATURE")
    groq_response_format: GroqResponseFormat = Field(
        default=GroqResponseFormat.JSON_SCHEMA,
        alias="GROQ_RESPONSE_FORMAT",
    )

    receptionist_response_mode: ReceptionistResponseMode = Field(
        default=ReceptionistResponseMode.DETERMINISTIC,
        alias="RECEPTIONIST_RESPONSE_MODE",
    )
    receptionist_response_llm_provider: LLMProviderName | None = Field(
        default=None,
        alias="RECEPTIONIST_RESPONSE_LLM_PROVIDER",
    )
    receptionist_response_max_tokens: int = Field(
        default=400,
        ge=1,
        alias="RECEPTIONIST_RESPONSE_MAX_TOKENS",
    )
    receptionist_response_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        alias="RECEPTIONIST_RESPONSE_TEMPERATURE",
    )
    receptionist_response_validate_output: bool = Field(
        default=True,
        alias="RECEPTIONIST_RESPONSE_VALIDATE_OUTPUT",
    )

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

    @property
    def resolved_llm_primary_provider(self) -> LLMProviderName:
        return self.llm_primary_provider or self.llm_provider

    @property
    def resolved_receptionist_response_llm_provider(self) -> LLMProviderName:
        return self.receptionist_response_llm_provider or self.resolved_llm_primary_provider

    @property
    def is_production_like(self) -> bool:
        return self.app_env.strip().lower() not in _NON_PRODUCTION_ENVS

    @field_validator("clinic_timezone")
    @classmethod
    def validate_clinic_timezone(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("CLINIC_TIMEZONE must be a non-empty IANA timezone")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"CLINIC_TIMEZONE must be a valid IANA timezone: {normalized}",
            ) from exc
        return normalized

    @field_validator("clinic_business_days")
    @classmethod
    def validate_clinic_business_days(cls, value: str) -> str:
        days = [day.strip().lower() for day in value.split(",") if day.strip()]
        if not days:
            raise ValueError("CLINIC_BUSINESS_DAYS must contain at least one weekday")
        invalid_days = [day for day in days if day not in _CLINIC_BUSINESS_WEEKDAYS]
        if invalid_days:
            invalid_list = ", ".join(invalid_days)
            raise ValueError(f"CLINIC_BUSINESS_DAYS contains invalid weekday(s): {invalid_list}")
        if len(days) != len(set(days)):
            raise ValueError("CLINIC_BUSINESS_DAYS must not contain duplicate weekdays")
        return ",".join(days)

    @field_validator("clinic_business_hours_start", "clinic_business_hours_end")
    @classmethod
    def validate_clinic_business_hours_time(cls, value: str) -> str:
        normalized = value.strip()
        if _CLINIC_TIME_HH_MM_PATTERN.fullmatch(normalized) is None:
            raise ValueError("Clinic business hours must use HH:MM format (24-hour)")
        return normalized

    @model_validator(mode="after")
    def validate_clinic_business_hours_order(self) -> "Settings":
        start_hour, start_minute = map(int, self.clinic_business_hours_start.split(":"))
        end_hour, end_minute = map(int, self.clinic_business_hours_end.split(":"))
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute
        if start_minutes >= end_minutes:
            raise ValueError(
                "CLINIC_BUSINESS_HOURS_START must be before CLINIC_BUSINESS_HOURS_END",
            )
        return self

    @model_validator(mode="after")
    def apply_public_demo_defaults(self) -> "Settings":
        if self.public_demo_mode and "PUBLIC_DEMO_GUARDRAILS_ENABLED" not in os.environ:
            self.public_demo_guardrails_enabled = True
        return self

    @model_validator(mode="after")
    def validate_llm_provider_settings(self) -> "Settings":
        self._validate_llm_provider_config(self.resolved_llm_primary_provider)

        if self.receptionist_response_mode == ReceptionistResponseMode.LLM:
            self._validate_llm_provider_config(self.resolved_receptionist_response_llm_provider)

        if self.llm_fallback_enabled:
            if self.llm_fallback_provider is None:
                raise ValueError(
                    "LLM_FALLBACK_PROVIDER is required when LLM_FALLBACK_ENABLED is true",
                )
            self._validate_llm_provider_config(self.llm_fallback_provider)

        self._validate_email_provider_settings()
        self._validate_retell_settings()
        self._validate_production_public_demo_settings()

        return self

    def _validate_retell_settings(self) -> None:
        if (
            self.retell_enabled
            and self.retell_webhook_verification_enabled
            and not (self.retell_webhook_secret or "").strip()
        ):
            raise ValueError(
                "RETELL_WEBHOOK_SECRET is required when RETELL_ENABLED and "
                "RETELL_WEBHOOK_VERIFICATION_ENABLED are true",
            )

        if self.retell_allow_insecure_webhooks and self.is_production_like:
            raise ValueError(
                "RETELL_ALLOW_INSECURE_WEBHOOKS cannot be true in production-like APP_ENV",
            )

    @staticmethod
    def _is_localhost_url(url: str) -> bool:
        normalized = url.strip().lower()
        return any(marker in normalized for marker in _LOCALHOST_URL_MARKERS)

    def _validate_production_public_demo_settings(self) -> None:
        if not self.is_production_like or not self.public_demo_mode:
            return

        if not self.public_demo_guardrails_enabled:
            raise ValueError(
                "PUBLIC_DEMO_GUARDRAILS_ENABLED must be true when PUBLIC_DEMO_MODE is "
                "enabled in production-like APP_ENV",
            )

        if self.app_debug:
            raise ValueError(
                "APP_DEBUG must be false when PUBLIC_DEMO_MODE is enabled in "
                "production-like APP_ENV",
            )

        if not self.database_url.strip() or self._is_localhost_url(self.database_url):
            raise ValueError(
                "DATABASE_URL must point to a non-localhost database when PUBLIC_DEMO_MODE "
                "is enabled in production-like APP_ENV",
            )

        if not self.redis_url.strip() or self._is_localhost_url(self.redis_url):
            raise ValueError(
                "REDIS_URL must point to a non-localhost Redis instance when PUBLIC_DEMO_MODE "
                "is enabled in production-like APP_ENV",
            )

    def _validate_email_provider_settings(self) -> None:
        provider_name = self.email_provider.strip().lower()

        if provider_name != "resend":
            return

        if not self.resend_api_key.strip():
            raise ValueError("RESEND_API_KEY is required when EMAIL_PROVIDER is resend")

        if not self.email_from_address.strip():
            raise ValueError("EMAIL_FROM_ADDRESS is required when EMAIL_PROVIDER is resend")

    def _validate_llm_provider_config(self, provider: LLMProviderName) -> None:
        if provider == LLMProviderName.BEDROCK and not self.bedrock_model_id.strip():
            raise ValueError(
                "BEDROCK_MODEL_ID is required when a configured LLM provider is bedrock",
            )

        if provider == LLMProviderName.GROQ:
            if not self.groq_api_key.strip():
                raise ValueError(
                    "GROQ_API_KEY is required when a configured LLM provider is groq",
                )
            if not self.groq_model.strip():
                raise ValueError(
                    "GROQ_MODEL is required when a configured LLM provider is groq",
                )


@lru_cache
def get_settings() -> Settings:
    return Settings()
