from __future__ import annotations

from app.ai.receptionist_output import (
    ExtractedPatientIdentity,
    ReceptionistExtractedFields,
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
)
from app.services.slot_filling import LLMChatSlotFillingService
from tests.test_scheduling_services import create_service


def create_slot_filling_service() -> LLMChatSlotFillingService:
    return LLMChatSlotFillingService(scheduling=create_service())


def create_analysis(
    *,
    full_name: str | None = None,
    date_of_birth: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> ReceptionistLLMAnalysis:
    return ReceptionistLLMAnalysis(
        intent=ReceptionistLLMIntent.APPOINTMENT_REQUEST,
        confidence=0.9,
        urgency=ReceptionistUrgency.NORMAL,
        extracted=ReceptionistExtractedFields(
            patient_identity=ExtractedPatientIdentity(
                full_name=full_name,
                date_of_birth=date_of_birth,
                phone=phone,
                email=email,
            ),
        ),
    )


def test_valid_identity_fields_are_merged() -> None:
    service = create_slot_filling_service()
    analysis = create_analysis(
        full_name="Jane Doe",
        date_of_birth="1990-05-15",
        phone="+1 555-123-4567",
        email="jane.doe@example.com",
    )

    result = service.apply_analysis(analysis=analysis, chat_context={})

    identity = result.updated_chat_context["patient_identity"]
    assert identity == {
        "full_name": "Jane Doe",
        "date_of_birth": "1990-05-15",
        "phone": "+1 555-123-4567",
        "email": "jane.doe@example.com",
    }
    assert {field.field for field in result.applied_fields} == {
        "patient_identity.full_name",
        "patient_identity.date_of_birth",
        "patient_identity.phone",
        "patient_identity.email",
    }
    assert result.rejected_fields == []


def test_existing_identity_fields_are_preserved() -> None:
    service = create_slot_filling_service()
    analysis = create_analysis(
        full_name="John Smith",
        date_of_birth="1985-01-01",
        phone="+1 555-999-8888",
        email="john.smith@example.com",
    )
    chat_context = {
        "patient_identity": {
            "full_name": "Jane Doe",
            "date_of_birth": "1990-05-15",
            "phone": "+1 555-000-1111",
        },
    }

    result = service.apply_analysis(analysis=analysis, chat_context=chat_context)

    assert result.updated_chat_context["patient_identity"] == {
        "full_name": "Jane Doe",
        "date_of_birth": "1990-05-15",
        "phone": "+1 555-000-1111",
        "email": "john.smith@example.com",
    }
    assert len(result.applied_fields) == 1
    assert result.applied_fields[0].field == "patient_identity.email"
    assert result.applied_fields[0].value == "john.smith@example.com"
    assert {rejected.field for rejected in result.rejected_fields} == {
        "patient_identity.full_name",
        "patient_identity.date_of_birth",
        "patient_identity.phone",
    }
    assert all(
        rejected.reason == "conflicts_with_existing_context"
        for rejected in result.rejected_fields
    )


def test_conflicting_phone_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_analysis(phone="+1 555-999-8888")
    chat_context = {
        "patient_identity": {
            "phone": "+1 555-000-1111",
        },
    }

    result = service.apply_analysis(analysis=analysis, chat_context=chat_context)

    assert result.updated_chat_context["patient_identity"]["phone"] == "+1 555-000-1111"
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "patient_identity.phone"
    assert result.rejected_fields[0].value == "+1 555-999-8888"
    assert result.rejected_fields[0].reason == "conflicts_with_existing_context"


def test_invalid_phone_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_analysis(phone="123")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "patient_identity" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "patient_identity.phone"
    assert result.rejected_fields[0].reason == "invalid_phone"


def test_invalid_email_is_rejected() -> None:
    service = create_slot_filling_service()
    analysis = create_analysis(email="not-an-email")

    result = service.apply_analysis(analysis=analysis, chat_context={})

    assert "patient_identity" not in result.updated_chat_context
    assert result.applied_fields == []
    assert len(result.rejected_fields) == 1
    assert result.rejected_fields[0].field == "patient_identity.email"
    assert result.rejected_fields[0].reason == "invalid_email"


def test_demo_phone_is_accepted() -> None:
    service = create_slot_filling_service()
    analysis = create_analysis(
        full_name="John Miller",
        date_of_birth="1985-04-12",
        phone="+1-555-0201",
        email="john.miller@example.test",
    )

    result = service.apply_analysis(analysis=analysis, chat_context={})

    identity = result.updated_chat_context["patient_identity"]
    assert identity["phone"] == "+1-555-0201"
    assert identity["full_name"] == "John Miller"
    assert identity["date_of_birth"] == "1985-04-12"
    assert identity["email"] == "john.miller@example.test"
    assert result.rejected_fields == []
