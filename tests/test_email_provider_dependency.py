from __future__ import annotations

import pytest

from app.core.config import Settings
from app.email.factory import EmailProviderConfigurationError, create_email_provider_from_settings
from app.email.fake_provider import FakeEmailProvider
from app.email.resend_provider import ResendEmailProvider


def test_settings_default_email_provider_is_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)

    settings = Settings(_env_file=None)

    assert settings.email_provider == "fake"


def test_settings_fake_provider_does_not_require_resend_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_FROM_ADDRESS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.email_provider == "fake"
    assert settings.resend_api_key == ""


def test_settings_resend_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.test")

    with pytest.raises(ValueError, match="RESEND_API_KEY is required"):
        Settings(_env_file=None)


def test_settings_resend_provider_requires_from_address(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "")

    with pytest.raises(ValueError, match="EMAIL_FROM_ADDRESS is required"):
        Settings(_env_file=None)


def test_create_email_provider_from_settings_returns_fake_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)

    settings = Settings(_env_file=None)
    provider = create_email_provider_from_settings(settings)

    assert isinstance(provider, FakeEmailProvider)


def test_create_email_provider_from_settings_returns_resend_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.test")

    settings = Settings(_env_file=None)
    provider = create_email_provider_from_settings(settings)

    assert isinstance(provider, ResendEmailProvider)


def test_create_email_provider_from_settings_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMAIL_PROVIDER", "smtp")

    settings = Settings(_env_file=None)

    with pytest.raises(EmailProviderConfigurationError, match="Unsupported email provider"):
        create_email_provider_from_settings(settings)
