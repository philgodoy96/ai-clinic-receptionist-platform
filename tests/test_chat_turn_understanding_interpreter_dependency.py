from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.ai.chat_turn_understanding_schema import build_chat_turn_understanding_openai_json_schema
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
from app.services.llm_chat_turn_understanding_interpreter import (
    LLMChatTurnUnderstandingInterpreter,
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


def groq_ctu_env(**overrides: str) -> dict[str, str]:
    env = {
        "CHAT_TURN_UNDERSTANDING_INTERPRETER": "groq",
        "GROQ_API_KEY": "gsk_test",
        "GROQ_MODEL": "llama-3.3-70b-versatile",
    }
    env.update(overrides)
    return env


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


def test_groq_config_without_api_key_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(
            monkeypatch,
            CHAT_TURN_UNDERSTANDING_INTERPRETER="groq",
            GROQ_MODEL="llama-3.3-70b-versatile",
        )


def test_groq_config_without_model_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="GROQ_MODEL"):
        load_settings(
            monkeypatch,
            CHAT_TURN_UNDERSTANDING_INTERPRETER="groq",
            GROQ_API_KEY="gsk_test",
        )


def test_groq_config_does_not_require_llm_primary_provider_groq(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        **groq_ctu_env(LLM_PROVIDER="fake"),
    )

    assert (
        settings.chat_turn_understanding_interpreter
        is ChatTurnUnderstandingInterpreterProvider.GROQ
    )
    assert settings.llm_provider.value == "fake"


def test_groq_config_returns_llm_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings(monkeypatch, **groq_ctu_env())
    stub_provider = MagicMock()

    with patch(
        "app.services.chat_turn_understanding_factory.GroqLLMProvider",
        return_value=stub_provider,
    ) as groq_provider_cls:
        interpreter = build_chat_turn_understanding_interpreter_from_settings(settings)

    assert isinstance(interpreter, LLMChatTurnUnderstandingInterpreter)
    assert interpreter.primary_provider is stub_provider
    groq_provider_cls.assert_called_once()


def test_groq_provider_uses_chat_turn_understanding_schema_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch, **groq_ctu_env())

    with patch("app.services.chat_turn_understanding_factory.GroqLLMProvider") as groq_provider_cls:
        build_chat_turn_understanding_interpreter_from_settings(settings)

    _, kwargs = groq_provider_cls.call_args
    assert kwargs["json_schema_builder"] is build_chat_turn_understanding_openai_json_schema


def test_disabled_config_does_not_construct_groq_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(monkeypatch)

    with patch("app.services.chat_turn_understanding_factory.GroqLLMProvider") as groq_provider_cls:
        interpreter = build_chat_turn_understanding_interpreter_from_settings(settings)

    groq_provider_cls.assert_not_called()
    assert interpreter is None


def test_fake_config_does_not_construct_groq_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        CHAT_TURN_UNDERSTANDING_INTERPRETER="fake",
    )

    with patch("app.services.chat_turn_understanding_factory.GroqLLMProvider") as groq_provider_cls:
        interpreter = build_chat_turn_understanding_interpreter_from_settings(settings)

    groq_provider_cls.assert_not_called()
    assert isinstance(interpreter, FakeChatTurnUnderstandingInterpreter)


def test_invalid_interpreter_setting_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        load_settings(monkeypatch, CHAT_TURN_UNDERSTANDING_INTERPRETER="bedrock")


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
        appointment_cancellation=MagicMock(),
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
        appointment_cancellation=MagicMock(),
        chat_turn_understanding_records=MagicMock(),
        chat_turn_understanding_interpreter=None,
    )

    assert service._booking_identity.chat_turn_understanding_interpreter is None
