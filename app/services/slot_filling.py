from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.ai.receptionist_output import ReceptionistLLMAnalysis


@dataclass(frozen=True, slots=True)
class SlotFillingAppliedField:
    field: str
    value: object


@dataclass(frozen=True, slots=True)
class SlotFillingRejectedField:
    field: str
    value: object
    reason: str


@dataclass(frozen=True, slots=True)
class SlotFillingResult:
    updated_chat_context: dict[str, Any]
    applied_fields: list[SlotFillingAppliedField] = field(default_factory=list)
    rejected_fields: list[SlotFillingRejectedField] = field(default_factory=list)
    used_llm_analysis: bool = False

    def to_metadata(self) -> dict[str, object]:
        return {
            "used_llm_analysis": self.used_llm_analysis,
            "applied_fields": [
                {
                    "field": field.field,
                    "value": field.value,
                }
                for field in self.applied_fields
            ],
            "rejected_fields": [
                {
                    "field": field.field,
                    "value": field.value,
                    "reason": field.reason,
                }
                for field in self.rejected_fields
            ],
        }


class LLMChatSlotFillingService:
    def apply_analysis(
        self,
        *,
        analysis: ReceptionistLLMAnalysis,
        chat_context: dict[str, Any],
    ) -> SlotFillingResult:
        return SlotFillingResult(
            updated_chat_context=deepcopy(chat_context),
            used_llm_analysis=True,
        )