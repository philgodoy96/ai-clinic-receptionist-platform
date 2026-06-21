from __future__ import annotations

import json

import pytest

from app.ai.receptionist_output import ReceptionistLLMAnalysis, ReceptionistLLMIntent
from app.ai.structured_output import (
    StructuredOutputParseError,
    StructuredOutputValidationError,
    parse_structured_output,
)


def test_structured_output_parser_accepts_json_wrapped_in_text() -> None:
    payload = {
        "intent": "greeting",
        "confidence": 0.75,
        "urgency": "normal",
    }
    raw_output = f"Here is the result: {json.dumps(payload)}"

    analysis = parse_structured_output(
        raw_output=raw_output,
        model_type=ReceptionistLLMAnalysis,
    )

    assert analysis.intent == ReceptionistLLMIntent.GREETING
    assert analysis.confidence == 0.75


def test_structured_output_parser_rejects_invalid_json() -> None:
    with pytest.raises(StructuredOutputParseError):
        parse_structured_output(
            raw_output="not json",
            model_type=ReceptionistLLMAnalysis,
        )


def test_structured_output_parser_rejects_invalid_enum() -> None:
    raw_output = json.dumps(
        {
            "intent": "made_up_intent",
            "confidence": 0.9,
            "urgency": "normal",
        },
    )

    with pytest.raises(StructuredOutputValidationError):
        parse_structured_output(
            raw_output=raw_output,
            model_type=ReceptionistLLMAnalysis,
        )
