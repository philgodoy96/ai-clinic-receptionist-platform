from __future__ import annotations

import pytest

from app.ai.prompt_versions import (
    CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION,
    PROMPT_REGISTRY,
    PromptVersionNotFoundError,
    get_current_receptionist_analysis_prompt_metadata,
    get_prompt_metadata,
    list_prompt_versions,
)
from app.ai.prompts.receptionist_analysis_v2 import build_receptionist_analysis_system_prompt
from app.ai.receptionist_prompt import (
    build_receptionist_system_prompt,
    get_receptionist_analysis_prompt_version,
)


def test_current_prompt_version_is_receptionist_analysis_v2() -> None:
    assert (
        get_receptionist_analysis_prompt_version() == CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION
    )
    assert CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION == "receptionist-analysis-v2"


def test_current_receptionist_prompt_metadata_describes_receptionist_analysis() -> None:
    metadata = get_current_receptionist_analysis_prompt_metadata()

    assert metadata.name == "receptionist-analysis"
    assert metadata.schema_name == "ReceptionistLLMAnalysis"
    assert metadata.created_for == "chat_receptionist_assistive_analysis"


def test_prompt_registry_contains_receptionist_analysis_v2() -> None:
    assert CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION in PROMPT_REGISTRY


def test_prompt_registry_retains_receptionist_analysis_v1() -> None:
    assert "receptionist-analysis-v1" in PROMPT_REGISTRY


def test_get_prompt_metadata_returns_receptionist_analysis_v2_metadata() -> None:
    metadata = get_prompt_metadata(CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION)

    assert metadata.version == CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION
    assert metadata.schema_name == "ReceptionistLLMAnalysis"


def test_prompt_metadata_prompt_module_points_to_versioned_module() -> None:
    metadata = get_current_receptionist_analysis_prompt_metadata()

    assert metadata.prompt_module == "app.ai.prompts.receptionist_analysis_v2"


def test_list_prompt_versions_includes_receptionist_analysis_v2() -> None:
    assert CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION in list_prompt_versions()


def test_get_prompt_metadata_raises_for_unknown_version() -> None:
    with pytest.raises(PromptVersionNotFoundError, match="Unknown prompt version"):
        get_prompt_metadata("receptionist-analysis-v999")


def test_prompt_builder_exposes_prompt_version() -> None:
    version = get_receptionist_analysis_prompt_version()
    prompt = build_receptionist_system_prompt()

    assert version == CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION
    assert f"Prompt version: {version}" in prompt


def test_versioned_prompt_module_exposes_prompt_text() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "non-user-facing clinic receptionist analysis service" in prompt
    assert f"Prompt version: {CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION}" in prompt
    assert prompt == build_receptionist_system_prompt()


def test_v2_prompt_includes_classification_only_boundary() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "non-user-facing" in prompt
    assert "Do not speak to the user" in prompt
    assert "orchestrate tools" in prompt
    assert "return valid JSON only" in prompt


def test_v2_prompt_includes_requires_human_true_rules() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "requires_human=true only when" in prompt
    assert "explicitly asks for a human" in prompt
    assert "medical emergency" in prompt


def test_v2_prompt_includes_requires_human_false_rules() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "requires_human=false for" in prompt
    assert "booking_confirmation" in prompt
    assert "cancel_request" in prompt
    assert "generic fallback" in prompt


def test_v2_prompt_distinguishes_appointment_and_availability() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "appointment_request:" in prompt
    assert "availability_request:" in prompt
    assert "without explicit booking confirmation" in prompt


def test_v2_prompt_includes_normal_booking_cancel_fallback_examples() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "Yes, book it." in prompt
    assert "intent=booking_confirmation, requires_human=false" in prompt
    assert "intent=cancel_request, requires_human=false" in prompt
    assert "intent=fallback, requires_human=false" in prompt
