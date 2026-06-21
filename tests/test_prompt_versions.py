from __future__ import annotations

import pytest

from app.ai.prompt_versions import (
    PROMPT_REGISTRY,
    PromptVersionNotFoundError,
    get_current_receptionist_analysis_prompt_metadata,
    get_prompt_metadata,
    list_prompt_versions,
)
from app.ai.prompts.receptionist_analysis_v1 import build_receptionist_analysis_system_prompt
from app.ai.receptionist_prompt import (
    build_receptionist_system_prompt,
    get_receptionist_analysis_prompt_version,
)


def test_current_prompt_version_is_receptionist_analysis_v1() -> None:
    assert get_receptionist_analysis_prompt_version() == "receptionist-analysis-v1"


def test_prompt_registry_contains_receptionist_analysis_v1() -> None:
    assert "receptionist-analysis-v1" in PROMPT_REGISTRY


def test_get_prompt_metadata_returns_receptionist_analysis_v1_metadata() -> None:
    metadata = get_prompt_metadata("receptionist-analysis-v1")

    assert metadata.version == "receptionist-analysis-v1"
    assert metadata.schema_name == "ReceptionistLLMAnalysis"


def test_prompt_metadata_prompt_module_points_to_versioned_module() -> None:
    metadata = get_current_receptionist_analysis_prompt_metadata()

    assert metadata.prompt_module == "app.ai.prompts.receptionist_analysis_v1"


def test_list_prompt_versions_includes_receptionist_analysis_v1() -> None:
    assert "receptionist-analysis-v1" in list_prompt_versions()


def test_get_prompt_metadata_raises_for_unknown_version() -> None:
    with pytest.raises(PromptVersionNotFoundError, match="Unknown prompt version"):
        get_prompt_metadata("receptionist-analysis-v999")


def test_prompt_builder_exposes_prompt_version() -> None:
    version = get_receptionist_analysis_prompt_version()
    prompt = build_receptionist_system_prompt()

    assert version == "receptionist-analysis-v1"
    assert f"Prompt version: {version}" in prompt


def test_versioned_prompt_module_exposes_prompt_text() -> None:
    prompt = build_receptionist_analysis_system_prompt()

    assert "You are a clinic receptionist analysis service." in prompt
    assert "Prompt version: receptionist-analysis-v1" in prompt
    assert prompt == build_receptionist_system_prompt()
