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


def test_clinic_time_defaults_are_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLINIC_TIMEZONE", raising=False)
    monkeypatch.delenv("CLINIC_BUSINESS_DAYS", raising=False)
    monkeypatch.delenv("CLINIC_BUSINESS_HOURS_START", raising=False)
    monkeypatch.delenv("CLINIC_BUSINESS_HOURS_END", raising=False)
    monkeypatch.delenv("CLINIC_NAME", raising=False)
    monkeypatch.delenv("CLINIC_LOCALE", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.clinic_timezone == "America/New_York"
    assert settings.clinic_business_days == "monday,tuesday,wednesday,thursday,friday"
    assert settings.clinic_business_hours_start == "09:00"
    assert settings.clinic_business_hours_end == "17:00"
    assert settings.clinic_name == "Demo Clinic"
    assert settings.clinic_locale == "en-US"


def test_invalid_clinic_timezone_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="CLINIC_TIMEZONE"):
        load_settings(monkeypatch, CLINIC_TIMEZONE="Not/A_Real_Zone")


def test_invalid_clinic_business_day_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="CLINIC_BUSINESS_DAYS"):
        load_settings(monkeypatch, CLINIC_BUSINESS_DAYS="monday,funday")


def test_invalid_clinic_business_hours_time_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        load_settings(monkeypatch, CLINIC_BUSINESS_HOURS_START="9:00am")


def test_clinic_business_hours_start_after_end_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="CLINIC_BUSINESS_HOURS_START"):
        load_settings(
            monkeypatch,
            CLINIC_BUSINESS_HOURS_START="17:00",
            CLINIC_BUSINESS_HOURS_END="09:00",
        )
