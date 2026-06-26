from __future__ import annotations

from app.ai.prompt_versions import get_current_chat_turn_understanding_prompt_metadata
from app.ai.prompts.chat_turn_understanding_v1 import (
    build_chat_turn_understanding_system_prompt as build_chat_turn_understanding_system_prompt_v1,
)


def get_chat_turn_understanding_prompt_version() -> str:
    return get_current_chat_turn_understanding_prompt_metadata().version


def build_chat_turn_understanding_system_prompt() -> str:
    return build_chat_turn_understanding_system_prompt_v1()
