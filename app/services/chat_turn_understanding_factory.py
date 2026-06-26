from __future__ import annotations

from app.ai.chat_turn_understanding_schema import build_chat_turn_understanding_openai_json_schema
from app.ai.groq_provider import GroqLLMProvider
from app.core.config import Settings
from app.domain.chat_turn_understanding import ChatTurnUnderstandingInterpreterProvider
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.fake_chat_turn_understanding_interpreter import (
    FakeChatTurnUnderstandingInterpreter,
)
from app.services.llm_chat_turn_understanding_interpreter import (
    LLMChatTurnUnderstandingInterpreter,
)


def build_chat_turn_understanding_interpreter_from_settings(
    settings: Settings,
) -> ChatTurnUnderstandingInterpreter | None:
    provider = settings.chat_turn_understanding_interpreter

    if provider is ChatTurnUnderstandingInterpreterProvider.DISABLED:
        return None

    if provider is ChatTurnUnderstandingInterpreterProvider.FAKE:
        return FakeChatTurnUnderstandingInterpreter()

    if provider is ChatTurnUnderstandingInterpreterProvider.GROQ:
        groq_provider = GroqLLMProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model,
            base_url=settings.groq_base_url,
            timeout_seconds=settings.groq_request_timeout_seconds,
            temperature=settings.groq_temperature,
            max_output_tokens=settings.groq_max_output_tokens,
            response_format=settings.groq_response_format,
            json_schema_builder=build_chat_turn_understanding_openai_json_schema,
        )
        return LLMChatTurnUnderstandingInterpreter(primary_provider=groq_provider)

    return None
