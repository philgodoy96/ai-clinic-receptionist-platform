from collections.abc import Generator

import pytest
from pydantic import ValidationError

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


def test_public_demo_defaults_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUBLIC_DEMO_MODE", raising=False)
    monkeypatch.delenv("PUBLIC_DEMO_GUARDRAILS_ENABLED", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    settings = load_settings(monkeypatch)
    assert settings.public_demo_mode is False
    assert settings.public_demo_guardrails_enabled is False
    assert settings.trust_proxy_headers is False


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
