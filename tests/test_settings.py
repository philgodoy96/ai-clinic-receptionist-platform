from collections.abc import Generator

import pytest
from pydantic import ValidationError

from app.ai.llm_provider import LLMProviderName
from app.core.config import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_local_defaults_keep_public_demo_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_DEMO_MODE", raising=False)
    monkeypatch.delenv("PUBLIC_DEMO_GUARDRAILS_ENABLED", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.public_demo_mode is False
    assert settings.public_demo_guardrails_enabled is False
    assert settings.trust_proxy_headers is False


def test_settings_without_env_file_default_to_local_demo_disabled() -> None:
    settings = Settings(_env_file=None)

    assert settings.public_demo_mode is False
    assert settings.public_demo_guardrails_enabled is False


@pytest.mark.parametrize(
    ("env_key", "invalid_value"),
    [
        ("DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP", "0"),
        ("DEMO_CHAT_MESSAGES_PER_DAY_PER_IP", "-1"),
        ("DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP", "0"),
        ("DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP", "0"),
        ("DEMO_APPOINTMENTS_PER_DAY_PER_IP", "0"),
        ("DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP", "0"),
        ("DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY", "0"),
        ("DEMO_GLOBAL_APPOINTMENTS_PER_DAY", "0"),
        ("DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY", "0"),
    ],
)
def test_demo_numeric_limits_reject_zero_or_negative(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    invalid_value: str,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, **{env_key: invalid_value})


def test_public_demo_mode_can_be_enabled_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        PUBLIC_DEMO_MODE="true",
        PUBLIC_DEMO_GUARDRAILS_ENABLED="true",
        TRUST_PROXY_HEADERS="true",
    )

    assert settings.public_demo_mode is True
    assert settings.public_demo_guardrails_enabled is True
    assert settings.trust_proxy_headers is True


def _production_public_demo_env(**overrides: str) -> dict[str, str]:
    env = {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "PUBLIC_DEMO_MODE": "true",
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": "true",
        "DATABASE_URL": "postgresql+psycopg://clinic:clinic@db.example.com:5432/clinic_receptionist",
        "REDIS_URL": "redis://redis.example.com:6379/0",
    }
    env.update(overrides)
    return env


def test_production_public_demo_requires_critical_env_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL"):
        load_settings(
            monkeypatch,
            **_production_public_demo_env(REDIS_URL="redis://localhost:6379/0"),
        )

    with pytest.raises(ValidationError, match="PUBLIC_DEMO_GUARDRAILS_ENABLED"):
        load_settings(
            monkeypatch,
            **_production_public_demo_env(PUBLIC_DEMO_GUARDRAILS_ENABLED="false"),
        )


def test_insecure_retell_webhooks_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_ALLOW_INSECURE_WEBHOOKS"):
        load_settings(
            monkeypatch,
            APP_ENV="production",
            RETELL_ALLOW_INSECURE_WEBHOOKS="true",
        )


def test_fake_local_mode_starts_without_real_provider_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    settings = load_settings(
        monkeypatch,
        APP_ENV="local",
        LLM_PROVIDER="fake",
        EMAIL_PROVIDER="fake",
    )

    assert settings.llm_provider == LLMProviderName.FAKE
    assert settings.email_provider == "fake"
    assert settings.groq_api_key == ""
    assert settings.resend_api_key == ""


def test_public_demo_guardrails_enabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_DEMO_GUARDRAILS_ENABLED", raising=False)

    settings = load_settings(monkeypatch, APP_ENV="local", PUBLIC_DEMO_MODE="true")

    assert settings.public_demo_mode is True
    assert settings.public_demo_guardrails_enabled is True


def test_missing_groq_key_rejected_only_when_groq_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, LLM_PROVIDER="fake")
    assert settings.groq_api_key == ""

    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(monkeypatch, LLM_PROVIDER="groq", GROQ_MODEL="llama-3.3-70b-versatile")


def test_missing_resend_key_rejected_only_when_resend_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, EMAIL_PROVIDER="fake")
    assert settings.resend_api_key == ""

    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        load_settings(
            monkeypatch,
            EMAIL_PROVIDER="resend",
            EMAIL_FROM_ADDRESS="clinic-demo@example.test",
        )


def test_settings_repr_does_not_expose_secret_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        GROQ_API_KEY="gsk_super_secret_key",
        RESEND_API_KEY="re_super_secret_key",
        RETELL_API_KEY="key_super_secret",
        RETELL_WEBHOOK_SECRET="whsec_super_secret",
    )

    rendered = repr(settings)

    assert "gsk_super_secret_key" not in rendered
    assert "re_super_secret_key" not in rendered
    assert "key_super_secret" not in rendered
    assert "whsec_super_secret" not in rendered
