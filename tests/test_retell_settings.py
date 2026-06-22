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


def test_retell_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETELL_ENABLED", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.retell_enabled is False


def test_retell_enabled_requires_webhook_secret_when_verification_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_WEBHOOK_SECRET"):
        load_settings(
            monkeypatch,
            RETELL_ENABLED="true",
            RETELL_WEBHOOK_VERIFICATION_ENABLED="true",
        )


def test_retell_insecure_mode_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETELL_ALLOW_INSECURE_WEBHOOKS", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.retell_allow_insecure_webhooks is False


@pytest.mark.parametrize(
    ("env_key", "invalid_value"),
    [
        ("RETELL_REQUEST_MAX_BODY_BYTES", "0"),
        ("RETELL_REQUEST_MAX_BODY_BYTES", "-1"),
    ],
)
def test_retell_request_max_body_bytes_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    env_key: str,
    invalid_value: str,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, **{env_key: invalid_value})


def test_retell_signature_header_name_defaults_to_x_retell_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RETELL_SIGNATURE_HEADER_NAME", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.retell_signature_header_name == "x-retell-signature"
