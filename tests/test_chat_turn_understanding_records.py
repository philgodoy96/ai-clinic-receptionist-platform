from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.ai.llm_reliability import LLMFailureCategory, LLMFailureReason
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.receptionist_output import (
    ExtractedPatientIdentity,
    ReceptionistExtractedFields,
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
)
from app.models.chat_turn_understandings import ChatTurnUnderstanding
from app.services.chat_turn_understanding_records import (
    FORBIDDEN_PERSISTENCE_KEYS,
    ChatTurnUnderstandingRecordService,
)
from app.services.llm_receptionist import ReceptionistAnalysisResult
from app.services.slot_filling import (
    SlotFillingAppliedField,
    SlotFillingRejectedField,
    SlotFillingResult,
)

FORBIDDEN_RECORD_KEYS = FORBIDDEN_PERSISTENCE_KEYS | {
    "content",
    "user_message",
}


class FakeChatTurnUnderstandingRepository:
    def __init__(self) -> None:
        self.records: list[ChatTurnUnderstanding] = []
        self.best_effort_should_fail = False

    def add(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding:
        self.records.append(record)
        if record.id is None:
            record.id = uuid4()
        return record

    def add_best_effort(self, record: ChatTurnUnderstanding) -> ChatTurnUnderstanding | None:
        if self.best_effort_should_fail:
            return None
        return self.add(record)

    def get_by_id(self, record_id: UUID) -> ChatTurnUnderstanding | None:
        for record in self.records:
            if record.id == record_id:
                return record
        return None

    def get_by_user_message_id(self, user_message_id: UUID) -> ChatTurnUnderstanding | None:
        for record in self.records:
            if record.user_message_id == user_message_id:
                return record
        return None

    def list_by_conversation_id(
        self,
        conversation_id: UUID,
        *,
        limit: int = 50,
    ) -> list[ChatTurnUnderstanding]:
        return [
            record
            for record in self.records
            if record.conversation_id == conversation_id
        ][:limit]


def _analysis_result() -> ReceptionistAnalysisResult:
    return ReceptionistAnalysisResult(
        analysis=ReceptionistLLMAnalysis(
            intent=ReceptionistLLMIntent.APPOINTMENT_REQUEST,
            confidence=0.91,
            urgency=ReceptionistUrgency.NORMAL,
            requires_human=False,
            safety_flags=["possible_phi"],
            extracted=ReceptionistExtractedFields(
                specialty="Dermatology",
                doctor_name="Dr. Emily Carter",
                date="2026-07-02",
                time="10:30",
                patient_identity=ExtractedPatientIdentity(
                    full_name="Jane Doe",
                    date_of_birth="1990-01-15",
                    email="jane.doe@example.test",
                ),
            ),
        ),
        model="fake-model",
        input_tokens=120,
        output_tokens=45,
        estimated_cost_micros=87,
        latency_ms=321,
        attempt_count=2,
        primary_attempt_count=1,
        fallback_attempt_count=1,
        used_repair=True,
        used_fallback=True,
        used_fallback_provider=True,
        primary_provider="fake",
        fallback_provider="groq",
        provider="groq",
        failure_reason=LLMFailureReason.NONE,
        failure_category=LLMFailureCategory.NONE,
        prompt_version=get_current_receptionist_analysis_prompt_metadata().version,
        error=None,
    )


def _slot_filling_result() -> SlotFillingResult:
    return SlotFillingResult(
        updated_chat_context={"specialty": "Dermatology"},
        applied_fields=[
            SlotFillingAppliedField(field="specialty", value="Dermatology"),
            SlotFillingAppliedField(field="date", value="2026-07-02"),
        ],
        rejected_fields=[
            SlotFillingRejectedField(
                field="doctor_name",
                value="Dr. Unknown",
                reason="doctor_not_found",
            ),
        ],
        used_llm_analysis=True,
        date_parsing={"status": "parsed", "normalized_date": "2026-07-02"},
        time_preference_parsing={"status": "parsed", "normalized_time": "10:30"},
    )


def _record_kwargs() -> dict[str, Any]:
    return {
        "conversation_id": uuid4(),
        "user_message_id": uuid4(),
        "assistant_message_id": uuid4(),
        "request_id": "req-123",
        "correlation_id": "corr-456",
    }


def test_record_maps_analysis_provider_and_reliability_metadata() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)
    analysis_result = _analysis_result()

    record = service.record(
        **_record_kwargs(),
        analysis_result=analysis_result,
        slot_filling_result=None,
    )

    assert record.provider == "groq"
    assert record.model == "fake-model"
    assert record.primary_provider == "fake"
    assert record.fallback_provider == "groq"
    assert record.used_fallback_provider == "groq"
    assert record.prompt_version == analysis_result.prompt_version
    assert record.prompt_name == "receptionist-analysis"
    assert record.schema_name == "ReceptionistLLMAnalysis"
    assert record.latency_ms == 321
    assert record.input_tokens == 120
    assert record.output_tokens == 45
    assert record.estimated_cost_micros == 87
    assert record.attempt_count == 2
    assert record.primary_attempt_count == 1
    assert record.fallback_attempt_count == 1
    assert record.used_fallback is True
    assert record.used_repair is True
    assert record.failure_reason == "none"
    assert record.failure_category == "none"
    assert record.error is None


def test_record_maps_intent_confidence_urgency_and_safety_flags() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)

    record = service.record(
        **_record_kwargs(),
        analysis_result=_analysis_result(),
        slot_filling_result=None,
    )

    assert record.llm_intent == "appointment_request"
    assert record.llm_confidence == 0.91
    assert record.llm_urgency == "normal"
    assert record.requires_human is False
    assert record.safety_flags == {"flags": ["possible_phi"]}


def test_record_maps_extracted_fields_without_raw_user_text() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)
    user_message_id = uuid4()

    record = service.record(
        conversation_id=uuid4(),
        user_message_id=user_message_id,
        assistant_message_id=uuid4(),
        request_id="req-123",
        correlation_id="corr-456",
        analysis_result=_analysis_result(),
        slot_filling_result=None,
    )

    assert record.extracted_fields["specialty"] == "Dermatology"
    assert record.extracted_fields["patient_identity"]["full_name"] == "Jane Doe"
    serialized = json.dumps(record.__dict__, default=str)
    assert "I need an appointment tomorrow" not in serialized
    assert record.user_message_id == user_message_id


def test_record_maps_slot_filling_fields_and_validation_outcome() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)
    slot_filling_result = _slot_filling_result()

    record = service.record(
        **_record_kwargs(),
        analysis_result=_analysis_result(),
        slot_filling_result=slot_filling_result,
    )

    assert record.applied_fields == {
        "specialty": "Dermatology",
        "date": "2026-07-02",
    }
    assert record.rejected_fields == {
        "doctor_name": {
            "value": "Dr. Unknown",
            "reason": "doctor_not_found",
        },
    }
    assert record.normalized_fields == {
        "date_parsing": {"status": "parsed", "normalized_date": "2026-07-02"},
        "time_preference_parsing": {"status": "parsed", "normalized_time": "10:30"},
    }
    assert record.validation_outcome == "partial"


@pytest.mark.parametrize(
    ("slot_filling_result", "expected_outcome"),
    [
        (None, "skipped"),
        (
            SlotFillingResult(
                updated_chat_context={},
                used_llm_analysis=False,
            ),
            "skipped",
        ),
        (
            SlotFillingResult(
                updated_chat_context={},
                applied_fields=[SlotFillingAppliedField(field="specialty", value="Dermatology")],
                used_llm_analysis=True,
            ),
            "applied",
        ),
        (
            SlotFillingResult(
                updated_chat_context={},
                rejected_fields=[
                    SlotFillingRejectedField(
                        field="doctor_name",
                        value="Dr. Unknown",
                        reason="doctor_not_found",
                    ),
                ],
                used_llm_analysis=True,
            ),
            "rejected",
        ),
        (
            SlotFillingResult(
                updated_chat_context={},
                used_llm_analysis=True,
            ),
            "none",
        ),
    ],
)
def test_record_validation_outcome_cases(
    slot_filling_result: SlotFillingResult | None,
    expected_outcome: str,
) -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)

    record = service.record(
        **_record_kwargs(),
        analysis_result=None,
        slot_filling_result=slot_filling_result,
    )

    assert record.validation_outcome == expected_outcome


def test_record_handles_missing_analysis_result() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)

    record = service.record(
        **_record_kwargs(),
        analysis_result=None,
        slot_filling_result=_slot_filling_result(),
    )

    assert record.provider is None
    assert record.llm_intent is None
    assert record.extracted_fields == {}
    assert record.safety_flags == {}
    assert record.validation_outcome == "partial"


def test_record_handles_missing_slot_filling_result() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)

    record = service.record(
        **_record_kwargs(),
        analysis_result=_analysis_result(),
        slot_filling_result=None,
    )

    assert record.applied_fields == {}
    assert record.rejected_fields == {}
    assert record.normalized_fields == {}
    assert record.validation_outcome == "skipped"


def test_record_does_not_persist_forbidden_keys() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    service = ChatTurnUnderstandingRecordService(repository=repository)

    record = service.record(
        **_record_kwargs(),
        analysis_result=_analysis_result(),
        slot_filling_result=_slot_filling_result(),
    )

    serialized = json.dumps(
        {
            "extracted_fields": record.extracted_fields,
            "safety_flags": record.safety_flags,
            "normalized_fields": record.normalized_fields,
            "applied_fields": record.applied_fields,
            "rejected_fields": record.rejected_fields,
        },
    )

    for forbidden_key in FORBIDDEN_RECORD_KEYS:
        assert forbidden_key not in serialized


def test_record_best_effort_returns_none_when_repository_fails() -> None:
    repository = FakeChatTurnUnderstandingRepository()
    repository.best_effort_should_fail = True
    service = ChatTurnUnderstandingRecordService(repository=repository)

    record = service.record_best_effort(
        **_record_kwargs(),
        analysis_result=_analysis_result(),
        slot_filling_result=None,
    )

    assert record is None
    assert repository.records == []
