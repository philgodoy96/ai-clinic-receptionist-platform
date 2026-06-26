from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.api.dependencies import (
    get_chat_receptionist_service,
    get_chat_turn_understanding_interpreter,
)
from app.core.config import Settings, get_settings
from app.domain.chat_turn_understanding import ChatTurnUnderstandingInterpreterProvider
from app.services.chat_receptionist import ChatReceptionistService
from app.services.chat_turn_understanding_factory import (
    build_chat_turn_understanding_interpreter_from_settings,
)
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_chat_turn_understanding_interpreter_defaults_to_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAT_TURN_UNDERSTANDING_INTERPRETER", raising=False)

    settings = load_settings(monkeypatch)

    assert (
        settings.chat_turn_understanding_interpreter
        is ChatTurnUnderstandingInterpreterProvider.DISABLED
    )


def test_disabled_config_returns_no_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(monkeypatch)

    interpreter = get_chat_turn_understanding_interpreter(settings=settings)

    assert interpreter is None
    assert build_chat_turn_understanding_interpreter_from_settings(settings) is None


def test_fake_config_returns_fake_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(
        monkeypatch,
        CHAT_TURN_UNDERSTANDING_INTERPRETER="fake",
    )

    interpreter = get_chat_turn_understanding_interpreter(settings=settings)

    assert isinstance(interpreter, FakeChatTurnUnderstandingInterpreter)


def test_fake_config_does_not_construct_llm_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        CHAT_TURN_UNDERSTANDING_INTERPRETER="fake",
    )

    with patch(
        "app.services.llm_chat_turn_understanding_interpreter.LLMChatTurnUnderstandingInterpreter",
    ) as llm_interpreter_cls:
        interpreter = build_chat_turn_understanding_interpreter_from_settings(settings)

    llm_interpreter_cls.assert_not_called()
    assert isinstance(interpreter, FakeChatTurnUnderstandingInterpreter)


def test_invalid_interpreter_setting_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, CHAT_TURN_UNDERSTANDING_INTERPRETER="groq")


def test_get_chat_receptionist_service_receives_configured_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        CHAT_TURN_UNDERSTANDING_INTERPRETER="fake",
    )
    interpreter = get_chat_turn_understanding_interpreter(settings=settings)
    assert isinstance(interpreter, FakeChatTurnUnderstandingInterpreter)

    service = get_chat_receptionist_service(
        conversation_service=MagicMock(),
        scheduling_service=MagicMock(),
        hold_service=MagicMock(),
        booking_service=MagicMock(),
        llm_analysis=None,
        slot_filling=MagicMock(),
        conversation_health=MagicMock(),
        human_escalations=MagicMock(),
        human_handoff_notifications=MagicMock(),
        date_parser=MagicMock(),
        time_preference_parser=MagicMock(),
        response_generator=MagicMock(),
        clinic_time_service=MagicMock(),
        settings=settings,
        patient_identity_resolution=MagicMock(),
        chat_turn_understanding_records=MagicMock(),
        chat_turn_understanding_interpreter=interpreter,
    )

    assert isinstance(service, ChatReceptionistService)
    assert isinstance(
        service._booking_identity.chat_turn_understanding_interpreter,
        FakeChatTurnUnderstandingInterpreter,
    )


def test_get_chat_receptionist_service_without_interpreter_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch)

    service = get_chat_receptionist_service(
        conversation_service=MagicMock(),
        scheduling_service=MagicMock(),
        hold_service=MagicMock(),
        booking_service=MagicMock(),
        llm_analysis=None,
        slot_filling=MagicMock(),
        conversation_health=MagicMock(),
        human_escalations=MagicMock(),
        human_handoff_notifications=MagicMock(),
        date_parser=MagicMock(),
        time_preference_parser=MagicMock(),
        response_generator=MagicMock(),
        clinic_time_service=MagicMock(),
        settings=settings,
        patient_identity_resolution=MagicMock(),
        chat_turn_understanding_records=MagicMock(),
        chat_turn_understanding_interpreter=None,
    )

    assert service._booking_identity.chat_turn_understanding_interpreter is None
