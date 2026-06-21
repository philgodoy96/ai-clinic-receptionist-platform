from __future__ import annotations

from app.ai.prompts.receptionist_analysis_v1 import (
    PROMPT_VERSION as RECEPTIONIST_ANALYSIS_V1_PROMPT_VERSION,
)
from app.ai.prompts.receptionist_analysis_v1 import (
    build_receptionist_analysis_system_prompt,
)

__all__ = [
    "RECEPTIONIST_ANALYSIS_V1_PROMPT_VERSION",
    "build_receptionist_analysis_system_prompt",
]
