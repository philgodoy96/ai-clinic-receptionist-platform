from __future__ import annotations

from typing import Any

from app.ai.llm_provider import (
    LLMFinishReason,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
)
from app.ai.receptionist_prompt import build_receptionist_system_prompt


class BedrockLLMProvider:
    def __init__(
        self,
        *,
        model_id: str,
        region_name: str,
        timeout_seconds: int,
        max_retries: int,
        temperature: float,
        max_tokens: int,
        client: Any | None = None,
    ) -> None:
        self._model_id = model_id
        self._region_name = region_name
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._default_temperature = temperature
        self._default_max_tokens = max_tokens
        self._client = client if client is not None else self._create_client()

    def complete(self, request: LLMRequest) -> LLMResponse:
        system_blocks, converse_messages = self._build_converse_input(request)
        temperature = request.temperature if request.temperature >= 0 else self._default_temperature
        max_tokens = request.max_tokens or self._default_max_tokens

        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=system_blocks,
                messages=converse_messages,
                inferenceConfig={
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                },
            )
        except Exception as exc:
            if self._is_bedrock_client_error(exc):
                raise LLMProviderError(str(exc)) from exc
            raise

        try:
            content, input_tokens, output_tokens, finish_reason = self._parse_converse_response(
                response,
            )
        except LLMProviderError:
            raise
        except Exception as exc:
            raise LLMProviderError(f"Failed to parse Bedrock response: {exc}") from exc

        return LLMResponse(
            content=content,
            model=self._model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_micros=0,
            finish_reason=finish_reason,
        )

    def _create_client(self) -> Any:
        import boto3
        from botocore.config import Config

        config = Config(
            read_timeout=self._timeout_seconds,
            connect_timeout=self._timeout_seconds,
            retries={"max_attempts": self._max_retries},
        )
        return boto3.client(
            "bedrock-runtime",
            region_name=self._region_name,
            config=config,
        )

    def _build_converse_input(
        self,
        request: LLMRequest,
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        system_blocks = [{"text": build_receptionist_system_prompt()}]
        for message in request.messages:
            if message.role == "system" and message.content.strip():
                system_blocks.append({"text": message.content})

        converse_messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role not in {"user", "assistant"}:
                continue

            converse_messages.append(
                {
                    "role": message.role,
                    "content": [{"text": message.content}],
                },
            )

        if not converse_messages:
            raise LLMProviderError("Bedrock request must include at least one user message")

        return system_blocks, converse_messages

    def _parse_converse_response(
        self,
        response: dict[str, Any],
    ) -> tuple[str, int, int, LLMFinishReason]:
        content = self._extract_text_from_converse_response(response)
        input_tokens, output_tokens = self._extract_usage(response)
        finish_reason = self._map_finish_reason(response.get("stopReason"))

        if not content.strip():
            raise LLMProviderError("Bedrock response did not include text content")

        return content, input_tokens, output_tokens, finish_reason

    def _extract_text_from_converse_response(self, response: dict[str, Any]) -> str:
        output = response.get("output")
        if not isinstance(output, dict):
            raise LLMProviderError("Bedrock response missing output object")

        message = output.get("message")
        if not isinstance(message, dict):
            raise LLMProviderError("Bedrock response missing assistant message")

        content_blocks = message.get("content")
        if not isinstance(content_blocks, list):
            raise LLMProviderError("Bedrock response missing content blocks")

        return self._extract_text_from_content_blocks(content_blocks)

    def _extract_text_from_content_blocks(self, content_blocks: list[Any]) -> str:
        text_parts: list[str] = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue

            text = block.get("text")
            if isinstance(text, str) and text:
                text_parts.append(text)
                continue

            if block.get("type") == "text":
                nested_text = block.get("text")
                if isinstance(nested_text, str) and nested_text:
                    text_parts.append(nested_text)

        if not text_parts:
            raise LLMProviderError("Bedrock response content blocks did not include text")

        return "".join(text_parts)

    def _extract_usage(self, response: dict[str, Any]) -> tuple[int, int]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return 0, 0

        input_tokens = usage.get("inputTokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("outputTokens", usage.get("output_tokens", 0))

        return (
            input_tokens if isinstance(input_tokens, int) else 0,
            output_tokens if isinstance(output_tokens, int) else 0,
        )

    def _map_finish_reason(self, stop_reason: Any) -> LLMFinishReason:
        if not isinstance(stop_reason, str):
            return LLMFinishReason.STOP

        normalized = stop_reason.lower()
        if normalized in {"end_turn", "stop", "stop_sequence"}:
            return LLMFinishReason.STOP
        if normalized in {"max_tokens", "length"}:
            return LLMFinishReason.LENGTH
        if normalized in {"tool_use", "tool_call"}:
            return LLMFinishReason.TOOL_CALL

        return LLMFinishReason.ERROR

    def _is_bedrock_client_error(self, exc: Exception) -> bool:
        module_name = exc.__class__.__module__
        class_name = exc.__class__.__name__
        return module_name.startswith("botocore") or class_name in {
            "ClientError",
            "BotoCoreError",
        }
