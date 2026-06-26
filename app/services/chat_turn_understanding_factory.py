from __future__ import annotations

from app.core.config import Settings
from app.domain.chat_turn_understanding import ChatTurnUnderstandingInterpreterProvider
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)


def build_chat_turn_understanding_interpreter_from_settings(
    settings: Settings,
) -> ChatTurnUnderstandingInterpreter | None:
    provider = settings.chat_turn_understanding_interpreter

    if provider is ChatTurnUnderstandingInterpreterProvider.DISABLED:
        return None

    if provider is ChatTurnUnderstandingInterpreterProvider.FAKE:
        return FakeChatTurnUnderstandingInterpreter()

    return None
