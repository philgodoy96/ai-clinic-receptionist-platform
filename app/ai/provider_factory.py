from __future__ import annotations

import logging

from app.ai.bedrock_llm_provider import BedrockLLMProvider
from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProvider, LLMProviderName
from app.core.config import Settings

logger = logging.getLogger("app.llm_provider")


class LLMProviderConfigurationError(RuntimeError):
    pass


def create_llm_provider_from_settings(
    settings: Settings,
    provider_name: LLMProviderName,
) -> LLMProvider:
    logger.info(
        "llm_provider_selected",
        extra={
            "event": "llm_provider_selected",
            "provider": provider_name.value,
        },
    )

    if provider_name == LLMProviderName.FAKE:
        return FakeLLMProvider()

    if provider_name == LLMProviderName.BEDROCK:
        return BedrockLLMProvider(
            model_id=settings.bedrock_model_id,
            region_name=settings.aws_region,
            timeout_seconds=settings.bedrock_request_timeout_seconds,
            max_retries=settings.bedrock_max_retries,
            temperature=settings.bedrock_temperature,
            max_tokens=settings.bedrock_max_tokens,
        )

    raise LLMProviderConfigurationError(
        f"Unsupported LLM provider: {provider_name.value}",
    )


def build_llm_provider(settings: Settings) -> LLMProvider:
    return create_llm_provider_from_settings(
        settings,
        settings.resolved_llm_primary_provider,
    )
