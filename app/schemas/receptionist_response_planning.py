from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import (
    ReceptionistResponseMode,
    ReceptionistResponseSafetyLevel,
    ReceptionistResponseType,
)
from app.domain.receptionist.response_planning import (
    GeneratedResponse,
    ResponsePlan,
    ResponsePlanCriticalSafetyRequirementError,
    ResponsePlanMissingFallbackTextError,
    ResponsePlanMissingResponseTypeError,
    build_generated_response,
    build_response_plan,
)


class ResponsePlanSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response_type: ReceptionistResponseType
    channel: ConversationChannel
    fallback_text: str
    safety_level: ReceptionistResponseSafetyLevel | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    deterministic_behavior: bool = False

    @field_validator("fallback_text")
    @classmethod
    def validate_fallback_text(cls, value: str) -> str:
        if not value.strip():
            msg = "fallback_text is required"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def validate_critical_requirements(self) -> Self:
        if self.response_type == ReceptionistResponseType.CRITICAL and not (
            self.deterministic_behavior or self.safety_level is not None
        ):
            msg = "critical response plans require deterministic_behavior or safety_level"
            raise ValueError(msg)
        return self

    def to_domain(self) -> ResponsePlan:
        try:
            return build_response_plan(
                response_type=self.response_type,
                channel=self.channel,
                fallback_text=self.fallback_text,
                safety_level=self.safety_level,
                facts=self.facts,
                metadata=self.metadata,
                deterministic_behavior=self.deterministic_behavior,
            )
        except ResponsePlanMissingFallbackTextError as exc:
            raise ValueError(str(exc)) from exc
        except ResponsePlanMissingResponseTypeError as exc:
            raise ValueError(str(exc)) from exc
        except ResponsePlanCriticalSafetyRequirementError as exc:
            raise ValueError(str(exc)) from exc


class GeneratedResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    response_type: ReceptionistResponseType
    channel: ConversationChannel
    used_fallback: bool
    mode: ReceptionistResponseMode
    safety_level: ReceptionistResponseSafetyLevel | None = None
    facts: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip():
            msg = "text is required"
            raise ValueError(msg)
        return value

    def to_domain(self) -> GeneratedResponse:
        return build_generated_response(
            text=self.text,
            response_type=self.response_type,
            channel=self.channel,
            used_fallback=self.used_fallback,
            mode=self.mode,
            safety_level=self.safety_level,
            facts=self.facts,
            metadata=self.metadata,
        )
