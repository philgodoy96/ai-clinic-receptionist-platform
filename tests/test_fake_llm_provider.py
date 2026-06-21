from __future__ import annotations

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMMessage, LLMRequest
from app.ai.receptionist_output import (
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
)
from app.ai.structured_output import parse_structured_output

JOHN_MILLER_IDENTITY_MESSAGE = (
    "My name is John Miller, DOB 1985-04-12, phone +1-555-0201, "
    "email john.miller@example.test. Confirm."
)


def test_fake_llm_provider_classifies_emergency() -> None:
    provider = FakeLLMProvider()
    response = provider.complete(
        LLMRequest(
            messages=[
                LLMMessage(role="user", content="This is an emergency"),
            ],
        ),
    )

    analysis = parse_structured_output(
        raw_output=response.content,
        model_type=ReceptionistLLMAnalysis,
    )

    assert analysis.intent == ReceptionistLLMIntent.EMERGENCY
    assert analysis.urgency == ReceptionistUrgency.EMERGENCY
    assert "medical_emergency" in analysis.safety_flags


def test_fake_llm_provider_extracts_structured_identity() -> None:
    provider = FakeLLMProvider()
    response = provider.complete(
        LLMRequest(
            messages=[
                LLMMessage(role="user", content=JOHN_MILLER_IDENTITY_MESSAGE),
            ],
        ),
    )

    analysis = parse_structured_output(
        raw_output=response.content,
        model_type=ReceptionistLLMAnalysis,
    )
    identity = analysis.extracted.patient_identity

    assert identity.full_name == "John Miller"
    assert identity.date_of_birth == "1985-04-12"
    assert identity.phone == "+1-555-0201"
    assert identity.email == "john.miller@example.test"
