from __future__ import annotations

from typing import Protocol

from app.domain.chat_turn_understanding import (
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
)


class ChatTurnUnderstandingInterpreter(Protocol):
    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        raise NotImplementedError
