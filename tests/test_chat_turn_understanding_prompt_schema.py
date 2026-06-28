from __future__ import annotations

import json
from typing import Any

from app.ai.chat_turn_understanding_prompt import (
    build_chat_turn_understanding_system_prompt,
    get_chat_turn_understanding_prompt_version,
)
from app.ai.chat_turn_understanding_schema import (
    build_chat_turn_understanding_openai_json_schema,
)
from app.ai.prompt_versions import (
    CURRENT_CHAT_TURN_UNDERSTANDING_PROMPT_VERSION,
    CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION,
    get_current_chat_turn_understanding_prompt_metadata,
    get_current_receptionist_analysis_prompt_metadata,
    get_prompt_metadata,
)
from app.ai.prompts.chat_turn_understanding_v1 import (
    PROMPT_VERSION as CHAT_TURN_UNDERSTANDING_V1_PROMPT_VERSION,
)
from app.ai.receptionist_output import build_receptionist_analysis_openai_json_schema

SIDE_EFFECT_FIELD_NAMES = frozenset(
    {
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "create_patient",
        "send_email",
        "execute_action",
        "tool_call",
        "tool_calls",
        "mutate_state",
    },
)

REQUEST_ONLY_FIELD_NAMES = frozenset(
    {
        "latest_user_message",
        "current_context",
        "conversation_state",
        "expected_response_type",
        "allowed_intents",
        "offered_slots",
        "known_specialties",
        "known_doctors",
        "locale",
        "last_assistant_question",
    },
)

REQUIRED_TOP_LEVEL_FIELDS = frozenset({"intent", "confidence", "reason"})

EXTRACTED_FIELD_RAW_AND_NORMALIZED_PAIRS = (
    ("date_of_birth", "date_of_birth_raw"),
    ("specialty", "specialty_raw"),
    ("doctor_name", "doctor_name_raw"),
    ("appointment_date", "appointment_date_raw"),
    ("appointment_time", "appointment_time_raw"),
    ("appointment_time_window", "appointment_time_window_raw"),
)


def _collect_schema_property_names(schema: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    properties = schema.get("properties")
    if isinstance(properties, dict):
        names.update(properties.keys())

    for defs_key in ("$defs", "definitions"):
        defs = schema.get(defs_key)
        if isinstance(defs, dict):
            for nested_schema in defs.values():
                if isinstance(nested_schema, dict):
                    names.update(_collect_schema_property_names(nested_schema))

    return names


def test_schema_builder_returns_stable_schema_name() -> None:
    schema = build_chat_turn_understanding_openai_json_schema()

    assert schema["name"] == "ChatTurnUnderstandingResult"
    assert schema["strict"] is True
    assert schema == build_chat_turn_understanding_openai_json_schema()


def test_schema_includes_required_top_level_fields() -> None:
    schema = build_chat_turn_understanding_openai_json_schema()
    inner_schema = schema["schema"]
    top_level_properties = set(inner_schema["properties"].keys())

    assert REQUIRED_TOP_LEVEL_FIELDS.issubset(top_level_properties)
    assert set(inner_schema["required"]) == REQUIRED_TOP_LEVEL_FIELDS


def test_schema_includes_missing_and_ambiguous_fields() -> None:
    schema = build_chat_turn_understanding_openai_json_schema()
    properties = set(schema["schema"]["properties"].keys())

    assert "missing_fields" in properties
    assert "ambiguous_fields" in properties


def test_schema_includes_extracted_fields_raw_and_normalized_fields() -> None:
    schema = build_chat_turn_understanding_openai_json_schema()
    property_names = _collect_schema_property_names(schema["schema"])

    assert "extracted_fields" in property_names
    for normalized_field, raw_field in EXTRACTED_FIELD_RAW_AND_NORMALIZED_PAIRS:
        assert normalized_field in property_names
        assert raw_field in property_names


def test_schema_does_not_include_request_only_fields() -> None:
    schema = build_chat_turn_understanding_openai_json_schema()
    property_names = _collect_schema_property_names(schema["schema"])
    serialized_schema = json.dumps(schema)

    assert REQUEST_ONLY_FIELD_NAMES.isdisjoint(property_names)
    assert all(field_name not in serialized_schema for field_name in REQUEST_ONLY_FIELD_NAMES)


def test_schema_does_not_include_side_effect_execution_fields() -> None:
    schema = build_chat_turn_understanding_openai_json_schema()
    property_names = _collect_schema_property_names(schema["schema"])
    serialized_schema = json.dumps(schema)

    assert SIDE_EFFECT_FIELD_NAMES.isdisjoint(property_names)
    assert all(field_name not in serialized_schema for field_name in SIDE_EFFECT_FIELD_NAMES)


def test_prompt_metadata_resolves_with_expected_name_version_and_schema() -> None:
    metadata = get_current_chat_turn_understanding_prompt_metadata()

    assert metadata.name == "chat-turn-understanding"
    assert metadata.version == CURRENT_CHAT_TURN_UNDERSTANDING_PROMPT_VERSION
    assert metadata.version == "chat-turn-understanding-v1"
    assert metadata.schema_name == "ChatTurnUnderstandingResult"
    assert metadata.created_for == "chat_turn_understanding"
    assert metadata == get_prompt_metadata(CURRENT_CHAT_TURN_UNDERSTANDING_PROMPT_VERSION)


def test_prompt_builder_exposes_current_prompt_version_and_boundaries() -> None:
    prompt = build_chat_turn_understanding_system_prompt()

    assert get_chat_turn_understanding_prompt_version() == CHAT_TURN_UNDERSTANDING_V1_PROMPT_VERSION
    assert f"Prompt version: {CHAT_TURN_UNDERSTANDING_V1_PROMPT_VERSION}" in prompt
    assert "ChatTurnUnderstandingResult" in prompt
    assert "must not book appointments" in prompt
    assert "backend validates all extracted fields" in prompt
    assert "ambiguous_fields" in prompt
    assert "missing_fields" in prompt
    assert "reason field is diagnostic only" in prompt


def test_prompt_describes_time_normalization_and_offered_slot_preference() -> None:
    prompt = build_chat_turn_understanding_system_prompt()

    assert "appointment_time" in prompt
    assert "24-hour" in prompt
    assert "3PM" in prompt and "15:00" in prompt
    assert "selected_slot_reference" in prompt
    assert "not offered" in prompt
    assert "ambiguous_fields" in prompt
    assert "It could be at 10" in prompt
    assert "Could it be on Monday 2pm" in prompt


def test_existing_receptionist_schema_and_prompt_metadata_remain_unchanged() -> None:
    receptionist_schema = build_receptionist_analysis_openai_json_schema()
    receptionist_metadata = get_current_receptionist_analysis_prompt_metadata()

    assert receptionist_schema["name"] == "ReceptionistLLMAnalysis"
    assert receptionist_metadata.version == CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION
    assert receptionist_metadata.schema_name == "ReceptionistLLMAnalysis"
