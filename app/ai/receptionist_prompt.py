from __future__ import annotations

from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.prompts.receptionist_analysis_v1 import build_receptionist_analysis_system_prompt


def get_receptionist_analysis_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


def build_receptionist_system_prompt() -> str:
    return build_receptionist_analysis_system_prompt()
