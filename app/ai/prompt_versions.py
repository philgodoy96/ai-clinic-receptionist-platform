from __future__ import annotations

from dataclasses import dataclass

CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION = "receptionist-analysis-v2"
CURRENT_RECEPTIONIST_RESPONSE_PROMPT_VERSION = "receptionist-response-v1"


class PromptVersionNotFoundError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromptMetadata:
    name: str
    version: str
    description: str
    schema_name: str
    created_for: str
    prompt_module: str
    prompt_hash: str | None = None

    def to_metadata(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "schema_name": self.schema_name,
            "created_for": self.created_for,
        }


PROMPT_REGISTRY: dict[str, PromptMetadata] = {
    "receptionist-analysis-v1": PromptMetadata(
        name="receptionist-analysis",
        version="receptionist-analysis-v1",
        description=("Extract structured receptionist analysis candidates from patient messages."),
        schema_name="ReceptionistLLMAnalysis",
        created_for="chat_receptionist_assistive_analysis",
        prompt_module="app.ai.prompts.receptionist_analysis_v1",
    ),
    "receptionist-analysis-v2": PromptMetadata(
        name="receptionist-analysis",
        version="receptionist-analysis-v2",
        description=(
            "Calibrated receptionist analysis with explicit requires_human and intent taxonomy."
        ),
        schema_name="ReceptionistLLMAnalysis",
        created_for="chat_receptionist_assistive_analysis",
        prompt_module="app.ai.prompts.receptionist_analysis_v2",
    ),
    "receptionist-response-v1": PromptMetadata(
        name="receptionist-response",
        version="receptionist-response-v1",
        description="Phrase patient-facing receptionist replies from a response plan.",
        schema_name="ReceptionistLLMPhrasedResponse",
        created_for="receptionist_response_generation",
        prompt_module="app.ai.prompts.receptionist_response_v1",
    ),
}


def get_prompt_metadata(version: str) -> PromptMetadata:
    try:
        return PROMPT_REGISTRY[version]
    except KeyError as exc:
        raise PromptVersionNotFoundError(
            f"Unknown prompt version: {version!r}",
        ) from exc


def get_current_receptionist_analysis_prompt_metadata() -> PromptMetadata:
    return get_prompt_metadata(CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION)


def get_current_receptionist_response_prompt_metadata() -> PromptMetadata:
    return get_prompt_metadata(CURRENT_RECEPTIONIST_RESPONSE_PROMPT_VERSION)


def list_prompt_versions() -> list[str]:
    return sorted(PROMPT_REGISTRY.keys())
