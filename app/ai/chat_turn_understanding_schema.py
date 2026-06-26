from __future__ import annotations

from typing import Any

from app.domain.chat_turn_understanding import ChatTurnUnderstandingResult


def build_chat_turn_understanding_openai_json_schema() -> dict[str, Any]:
    return {
        "name": "ChatTurnUnderstandingResult",
        "strict": True,
        "schema": ChatTurnUnderstandingResult.model_json_schema(),
    }
