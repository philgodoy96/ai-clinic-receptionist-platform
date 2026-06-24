from __future__ import annotations

import pytest

from app.core.config import Settings
from app.domain.voice_patient_intake import VoicePatientIntakeMode


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_voice_patient_intake_mode_defaults_to_lookup_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOICE_PATIENT_INTAKE_MODE", raising=False)

    settings = load_settings(monkeypatch)

    assert settings.voice_patient_intake_mode is VoicePatientIntakeMode.LOOKUP_ONLY


def test_voice_patient_intake_mode_accepts_demo_auto_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, VOICE_PATIENT_INTAKE_MODE="demo_auto_create")

    assert settings.voice_patient_intake_mode is VoicePatientIntakeMode.DEMO_AUTO_CREATE
