from __future__ import annotations

from dataclasses import dataclass

CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION = "receptionist-analysis-v1"


@dataclass(frozen=True, slots=True)
class PromptMetadata:
    name: str
    version: str
    description: str
    schema_name: str
    created_for: str

    def to_metadata(self) -> dict[str, str]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "schema_name": self.schema_name,
            "created_for": self.created_for,
        }


RECEPTIONIST_ANALYSIS_PROMPT = PromptMetadata(
    name="receptionist-analysis",
    version=CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION,
    description=(
        "Extract structured receptionist analysis candidates from patient messages."
    ),
    schema_name="ReceptionistLLMAnalysis",
    created_for="chat_receptionist_assistive_analysis",
)


def get_current_receptionist_analysis_prompt_metadata() -> PromptMetadata:
    return RECEPTIONIST_ANALYSIS_PROMPT