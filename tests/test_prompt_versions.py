from __future__ import annotations

from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.receptionist_prompt import (
    build_receptionist_system_prompt,
    get_receptionist_analysis_prompt_version,
)


def test_current_prompt_version_is_receptionist_analysis_v1() -> None:
    assert get_receptionist_analysis_prompt_version() == "receptionist-analysis-v1"


def test_prompt_metadata_schema_name_is_receptionist_llm_analysis() -> None:
    metadata = get_current_receptionist_analysis_prompt_metadata()
    assert metadata.schema_name == "ReceptionistLLMAnalysis"


def test_prompt_builder_exposes_prompt_version() -> None:
    version = get_receptionist_analysis_prompt_version()
    prompt = build_receptionist_system_prompt()

    assert version == "receptionist-analysis-v1"
    assert f"Prompt version: {version}" in prompt
