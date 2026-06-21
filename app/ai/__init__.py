from __future__ import annotations

import logging

from app.ai.bedrock_llm_provider import BedrockLLMProvider
from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProvider, LLMProviderName
from app.ai.prompt_versions import (
    CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION,
    get_current_receptionist_analysis_prompt_metadata,
)
from app.ai.receptionist_prompt import (
    build_receptionist_system_prompt,
    get_receptionist_analysis_prompt_version,
)
from app.core.config import Settings

logger = logging.getLogger("app.llm_provider")


class LLMProviderConfigurationError(RuntimeError):
    pass


def build_llm_provider(settings: Settings) -> LLMProvider:
    provider_name = settings.llm_provider.value
    logger.info(
        "llm_provider_selected",
        extra={
            "event": "llm_provider_selected",
            "provider": provider_name,
        },
    )

    if settings.llm_provider == LLMProviderName.FAKE:
        return FakeLLMProvider()

    if settings.llm_provider == LLMProviderName.BEDROCK:
        return BedrockLLMProvider(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            timeout_seconds=settings.bedrock_request_timeout_seconds,
            max_retries=settings.bedrock_max_retries,
            temperature=settings.bedrock_temperature,
            max_tokens=settings.bedrock_max_tokens,
        )

    raise LLMProviderConfigurationError(
        f"Unsupported LLM provider: {provider_name}",
    )


__all__ = [
    "CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION",
    "LLMProviderConfigurationError",
    "build_llm_provider",
    "build_receptionist_system_prompt",
    "get_current_receptionist_analysis_prompt_metadata",
    "get_receptionist_analysis_prompt_version",
]
