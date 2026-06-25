from __future__ import annotations

import json

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMRequest, LLMResponse
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.reliability import LLMFailureReason
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
    sanitize_conversation_context_for_llm,
)
from tests.llm_provider_test_helpers import (
    RaisingLLMProvider,
    StaticContentLLMProvider,
    build_receptionist_analysis_payload,
)


def expected_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


class CapturingLLMProvider(StaticContentLLMProvider):
    def __init__(self, content: str) -> None:
        super().__init__(content)
        self.last_request: LLMRequest | None = None

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.last_request = request
        return super().complete(request)


def test_successful_llm_analysis_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(build_receptionist_analysis_payload()),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is False
    assert result.prompt_version == expected_prompt_version()


def test_fallback_llm_analysis_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.prompt_version == expected_prompt_version()


def test_llm_analysis_request_metadata_includes_prompt_version() -> None:
    provider = CapturingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)

    service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert provider.last_request is not None
    assert provider.last_request.metadata["prompt_version"] == expected_prompt_version()


def test_invalid_json_fallback_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("this is not valid json"),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.prompt_version == expected_prompt_version()


def test_safety_violation_fallback_includes_prompt_version() -> None:
    payload = build_receptionist_analysis_payload(
        intent="greeting",
        urgency="emergency",
    )
    service = LLMReceptionistAnalysisService(provider=StaticContentLLMProvider(payload))

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SAFETY_VIOLATION
    assert result.prompt_version == expected_prompt_version()


def test_fake_llm_provider_success_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(provider=FakeLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.prompt_version == expected_prompt_version()


def test_llm_receptionist_analysis_service_records_fallback_on_provider_error() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.PROVIDER_EXCEPTION
    assert result.prompt_version == expected_prompt_version()
    assert result.latency_ms >= 0
    assert result.attempt_count == 2
    assert result.primary_attempt_count == 2


def test_llm_receptionist_analysis_service_records_schema_validation_failure() -> None:
    invalid_payload = json.dumps(
        {
            "intent": "made_up_intent",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(invalid_payload),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.SCHEMA_VALIDATION_FAILED
    assert result.primary_attempt_count == 2
    assert result.prompt_version == expected_prompt_version()


def test_llm_receptionist_analysis_service_records_low_confidence() -> None:
    low_confidence_payload = json.dumps(
        {
            "intent": "fallback",
            "confidence": 0.4,
            "urgency": "normal",
        },
    )
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(low_confidence_payload),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is False
    assert result.failure_reason == LLMFailureReason.LOW_CONFIDENCE
    assert result.prompt_version == expected_prompt_version()


def _request_message_contents(request: LLMRequest) -> str:
    return "\n".join(message.content for message in request.messages)


def test_llm_request_includes_sanitized_context_snapshot_when_available() -> None:
    provider = CapturingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)

    service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="tomorrow morning",
            conversation_context={
                "selected_specialty_name": "Cardiology",
                "selected_doctor_name": "Dr. Emily Carter",
                "requested_date": "2026-06-25",
                "requested_time_window": {
                    "label": "morning",
                    "start_time": "08:00",
                    "end_time": "12:00",
                },
                "hold_id": "hold-123",
            },
        ),
    )

    assert provider.last_request is not None
    contents = _request_message_contents(provider.last_request)
    assert "Backend-provided conversation context snapshot" in contents
    assert '"selected_specialty":"Cardiology"' in contents
    assert '"requested_time_window":"morning"' in contents
    assert '"has_active_hold":true' in contents
    assert '"patient_identity_status":"missing"' in contents
    assert '"has_patient_name":false' in contents
    assert "hold-123" not in contents
    assert provider.last_request.messages[-1].role == "user"
    assert provider.last_request.messages[-1].content == "tomorrow morning"


def test_llm_request_omits_context_snapshot_when_empty() -> None:
    provider = CapturingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)

    service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert provider.last_request is not None
    assert len(provider.last_request.messages) == 2
    contents = _request_message_contents(provider.last_request)
    assert "Conversation context snapshot" not in contents


def test_sanitize_conversation_context_excludes_pii_and_internal_identifiers() -> None:
    context: dict[str, object] = {
        "selected_specialty_id": "spec-uuid-1",
        "selected_specialty_name": "Cardiology",
        "selected_doctor_id": "doc-uuid-1",
        "selected_doctor_name": "Dr. Emily Carter",
        "requested_date": "2026-06-25",
        "patient_email": "real@example.com",
        "patient_phone": "+15555555555",
        "patient_date_of_birth": "1990-01-01",
        "patient_resolution_id": "resolution-uuid-1",
        "hold_id": "hold-uuid-1",
        "slot_id": "slot-uuid-1",
        "appointment_id": "appointment-uuid-1",
        "selected_availability_slot_id": "availability-slot-uuid-1",
        "offered_slots": [
            {
                "availability_slot_id": "slot-uuid-1",
                "doctor_id": "doc-uuid-1",
                "start_time": "2026-06-25T09:00:00+00:00",
                "display_time": "09:00",
            },
        ],
        "patient_identity": {
            "full_name": "Jane Doe",
            "date_of_birth": "1990-01-01",
            "phone": "+15555555555",
            "email": "real@example.com",
        },
    }

    snapshot = sanitize_conversation_context_for_llm(context)
    serialized = json.dumps(snapshot)

    assert snapshot["selected_specialty"] == "Cardiology"
    assert snapshot["selected_doctor"] == "Dr. Emily Carter"
    assert snapshot["has_patient_name"] is True
    assert snapshot["has_patient_date_of_birth"] is True
    assert snapshot["has_confirmed_email"] is True
    assert snapshot["patient_identity_status"] == "complete"

    forbidden_values = [
        "spec-uuid-1",
        "doc-uuid-1",
        "real@example.com",
        "+15555555555",
        "1990-01-01",
        "resolution-uuid-1",
        "hold-uuid-1",
        "slot-uuid-1",
        "appointment-uuid-1",
        "availability-slot-uuid-1",
        "Jane Doe",
    ]
    for value in forbidden_values:
        assert value not in serialized

    provider = CapturingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)
    service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="confirm booking",
            conversation_context=context,
        ),
    )
    assert provider.last_request is not None
    request_contents = _request_message_contents(provider.last_request)
    for value in forbidden_values:
        assert value not in request_contents


def test_sanitize_conversation_context_handles_invalid_shapes_gracefully() -> None:
    assert sanitize_conversation_context_for_llm({}) == {}
    assert sanitize_conversation_context_for_llm(None) == {}  # type: ignore[arg-type]


def test_empty_offered_slots_does_not_infer_unavailable_status() -> None:
    context: dict[str, object] = {
        "selected_doctor_name": "Dr. Emily Carter",
        "requested_date": "2026-06-25",
        "offered_slots": [],
    }

    snapshot = sanitize_conversation_context_for_llm(context)

    assert snapshot["has_offered_slots"] is False
    assert "last_availability_status" not in snapshot


def test_missing_offered_slots_does_not_infer_unavailable_status() -> None:
    context: dict[str, object] = {
        "selected_doctor_name": "Dr. Emily Carter",
        "requested_date": "2026-06-25",
    }

    snapshot = sanitize_conversation_context_for_llm(context)

    assert "has_offered_slots" not in snapshot
    assert "last_availability_status" not in snapshot


def test_explicit_availability_status_is_included_from_context() -> None:
    context: dict[str, object] = {
        "availability_status": "no_matching_slots",
        "offered_slots": [],
    }

    snapshot = sanitize_conversation_context_for_llm(context)

    assert snapshot["last_availability_status"] == "no_matching_slots"
    assert snapshot["has_offered_slots"] is False


def test_selected_time_is_derived_from_selected_start_time() -> None:
    context: dict[str, object] = {
        "selected_start_time": "2026-06-25T09:30:00+00:00",
        "hold_id": "hold-uuid-1",
    }

    snapshot = sanitize_conversation_context_for_llm(context)

    assert snapshot["selected_time"] == "09:30"
    assert "requested_time_preference" not in snapshot
    assert "hold-uuid-1" not in json.dumps(snapshot)


def test_active_hold_includes_missing_identity_flags_without_pii() -> None:
    context: dict[str, object] = {
        "hold_id": "hold-uuid-1",
        "selected_doctor_name": "Dr. Emily Carter",
    }

    snapshot = sanitize_conversation_context_for_llm(context)
    serialized = json.dumps(snapshot)

    assert snapshot["has_active_hold"] is True
    assert snapshot["patient_identity_status"] == "missing"
    assert snapshot["has_patient_name"] is False
    assert snapshot["has_patient_date_of_birth"] is False
    assert snapshot["has_confirmed_email"] is False
    assert "hold-uuid-1" not in serialized

    provider = CapturingLLMProvider(build_receptionist_analysis_payload())
    service = LLMReceptionistAnalysisService(provider=provider)
    service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="yes please",
            conversation_context=context,
        ),
    )
    assert provider.last_request is not None
    context_message = next(
        message.content
        for message in provider.last_request.messages
        if "Conversation context snapshot:" in message.content
    )
    assert '"patient_identity_status":"missing"' in context_message
    assert "hold-uuid-1" not in context_message
