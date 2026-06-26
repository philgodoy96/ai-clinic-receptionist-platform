from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from app.ai.prompt_versions import get_prompt_metadata
from app.ai.receptionist_output import ReceptionistLLMAnalysis
from app.models.chat_turn_understandings import ChatTurnUnderstanding
from app.repositories.chat_turn_understandings import ChatTurnUnderstandingRepository
from app.services.llm_receptionist import ReceptionistAnalysisResult
from app.services.slot_filling import SlotFillingResult

RECEPTIONIST_ANALYSIS_SCHEMA_NAME = "ReceptionistLLMAnalysis"

FORBIDDEN_PERSISTENCE_KEYS = frozenset(
    {
        "raw_prompt",
        "system_prompt",
        "prompt",
        "provider_output",
        "raw_provider_output",
    },
)


def _safety_flags_payload(analysis: ReceptionistLLMAnalysis) -> dict[str, Any]:
    if not analysis.safety_flags:
        return {}

    return {"flags": list(analysis.safety_flags)}


def _applied_fields_payload(slot_filling_result: SlotFillingResult) -> dict[str, Any]:
    return {
        field.field: field.value
        for field in slot_filling_result.applied_fields
    }


def _rejected_fields_payload(slot_filling_result: SlotFillingResult) -> dict[str, Any]:
    return {
        field.field: {
            "value": field.value,
            "reason": field.reason,
        }
        for field in slot_filling_result.rejected_fields
    }


def _normalized_fields_payload(slot_filling_result: SlotFillingResult) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    if slot_filling_result.date_parsing is not None:
        normalized["date_parsing"] = slot_filling_result.date_parsing

    if slot_filling_result.time_preference_parsing is not None:
        normalized["time_preference_parsing"] = slot_filling_result.time_preference_parsing

    return normalized


def _validation_outcome(slot_filling_result: SlotFillingResult | None) -> str:
    if slot_filling_result is None or not slot_filling_result.used_llm_analysis:
        return "skipped"

    has_applied = bool(slot_filling_result.applied_fields)
    has_rejected = bool(slot_filling_result.rejected_fields)

    if has_applied and not has_rejected:
        return "applied"
    if has_applied and has_rejected:
        return "partial"
    if has_rejected and not has_applied:
        return "rejected"

    return "none"


def _assert_no_forbidden_persistence_keys(payload: dict[str, Any]) -> None:
    for key in payload:
        if key in FORBIDDEN_PERSISTENCE_KEYS:
            raise ValueError(f"forbidden persistence key: {key}")


class ChatTurnUnderstandingRecordService:
    def __init__(self, repository: ChatTurnUnderstandingRepository) -> None:
        self.repository = repository

    def record(
        self,
        *,
        conversation_id: UUID,
        user_message_id: UUID,
        assistant_message_id: UUID | None,
        request_id: str | None,
        correlation_id: str | None,
        analysis_result: ReceptionistAnalysisResult | None,
        slot_filling_result: SlotFillingResult | None,
    ) -> ChatTurnUnderstanding:
        understanding = self._build_record(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            request_id=request_id,
            correlation_id=correlation_id,
            analysis_result=analysis_result,
            slot_filling_result=slot_filling_result,
        )

        return self.repository.add(understanding)

    def record_best_effort(
        self,
        *,
        conversation_id: UUID,
        user_message_id: UUID,
        assistant_message_id: UUID | None,
        request_id: str | None,
        correlation_id: str | None,
        analysis_result: ReceptionistAnalysisResult | None,
        slot_filling_result: SlotFillingResult | None,
    ) -> ChatTurnUnderstanding | None:
        understanding = self._build_record(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            request_id=request_id,
            correlation_id=correlation_id,
            analysis_result=analysis_result,
            slot_filling_result=slot_filling_result,
        )

        add_best_effort = getattr(self.repository, "add_best_effort", None)
        if callable(add_best_effort):
            return cast(
                ChatTurnUnderstanding | None,
                add_best_effort(understanding),
            )

        try:
            return self.repository.add(understanding)
        except Exception:
            return None

    def _build_record(
        self,
        *,
        conversation_id: UUID,
        user_message_id: UUID,
        assistant_message_id: UUID | None,
        request_id: str | None,
        correlation_id: str | None,
        analysis_result: ReceptionistAnalysisResult | None,
        slot_filling_result: SlotFillingResult | None,
    ) -> ChatTurnUnderstanding:
        extracted_fields: dict[str, Any] = {}
        safety_flags: dict[str, Any] = {}
        normalized_fields: dict[str, Any] = {}
        applied_fields: dict[str, Any] = {}
        rejected_fields: dict[str, Any] = {}

        provider: str | None = None
        model: str | None = None
        primary_provider: str | None = None
        fallback_provider: str | None = None
        used_fallback_provider: str | None = None
        prompt_name: str | None = None
        prompt_version: str | None = None
        schema_name: str | None = None
        schema_version: str | None = None
        llm_intent: str | None = None
        llm_confidence: float | None = None
        llm_urgency: str | None = None
        requires_human: bool | None = None
        latency_ms: int | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        estimated_cost_micros: int | None = None
        attempt_count: int | None = None
        primary_attempt_count: int | None = None
        fallback_attempt_count: int | None = None
        used_fallback: bool | None = None
        used_repair: bool | None = None
        failure_reason: str | None = None
        failure_category: str | None = None
        error: str | None = None

        if analysis_result is not None:
            analysis = analysis_result.analysis
            prompt_metadata = get_prompt_metadata(analysis_result.prompt_version)

            provider = analysis_result.provider
            model = analysis_result.model
            primary_provider = analysis_result.primary_provider
            fallback_provider = analysis_result.fallback_provider
            if analysis_result.used_fallback_provider:
                used_fallback_provider = analysis_result.fallback_provider
            prompt_name = prompt_metadata.name
            prompt_version = analysis_result.prompt_version
            schema_name = prompt_metadata.schema_name or RECEPTIONIST_ANALYSIS_SCHEMA_NAME
            llm_intent = analysis.intent.value
            llm_confidence = analysis.confidence
            llm_urgency = analysis.urgency.value
            requires_human = analysis.requires_human
            safety_flags = _safety_flags_payload(analysis)
            extracted_fields = analysis.extracted.model_dump(mode="json")
            latency_ms = analysis_result.latency_ms
            input_tokens = analysis_result.input_tokens
            output_tokens = analysis_result.output_tokens
            estimated_cost_micros = analysis_result.estimated_cost_micros
            attempt_count = analysis_result.attempt_count
            primary_attempt_count = analysis_result.primary_attempt_count
            fallback_attempt_count = analysis_result.fallback_attempt_count
            used_fallback = analysis_result.used_fallback
            used_repair = analysis_result.used_repair
            failure_reason = analysis_result.failure_reason.value
            failure_category = analysis_result.failure_category.value
            error = analysis_result.error

        if slot_filling_result is not None:
            normalized_fields = _normalized_fields_payload(slot_filling_result)
            applied_fields = _applied_fields_payload(slot_filling_result)
            rejected_fields = _rejected_fields_payload(slot_filling_result)

        for payload in (
            extracted_fields,
            safety_flags,
            normalized_fields,
            applied_fields,
            rejected_fields,
        ):
            _assert_no_forbidden_persistence_keys(payload)

        return ChatTurnUnderstanding(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            request_id=request_id,
            correlation_id=correlation_id,
            provider=provider,
            model=model,
            primary_provider=primary_provider,
            fallback_provider=fallback_provider,
            used_fallback_provider=used_fallback_provider,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            schema_name=schema_name,
            schema_version=schema_version,
            llm_intent=llm_intent,
            llm_confidence=llm_confidence,
            llm_urgency=llm_urgency,
            requires_human=requires_human,
            safety_flags=safety_flags,
            extracted_fields=extracted_fields,
            normalized_fields=normalized_fields,
            applied_fields=applied_fields,
            rejected_fields=rejected_fields,
            validation_outcome=_validation_outcome(slot_filling_result),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_micros=estimated_cost_micros,
            attempt_count=attempt_count,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=fallback_attempt_count,
            used_fallback=used_fallback,
            used_repair=used_repair,
            failure_reason=failure_reason,
            failure_category=failure_category,
            error=error,
        )
