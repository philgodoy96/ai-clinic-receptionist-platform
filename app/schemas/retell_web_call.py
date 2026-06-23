from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.retell_web_call import RetellWebCallRequest, RetellWebCallResult


class RetellWebCallRequestSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    demo_session_id: str | None = Field(default=None, max_length=120)
    conversation_id: UUID | None = None

    def to_domain(self) -> RetellWebCallRequest:
        return RetellWebCallRequest(
            demo_session_id=self.demo_session_id,
            conversation_id=self.conversation_id,
        )


class RetellWebCallResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1, max_length=40)
    call_id: str = Field(min_length=1, max_length=120)
    access_token: str = Field(min_length=1)
    expires_in_seconds: int = Field(ge=1)
    conversation_id: UUID | None = None


def retell_web_call_result_to_response(result: RetellWebCallResult) -> RetellWebCallResponseSchema:
    return RetellWebCallResponseSchema(
        provider=result.provider,
        call_id=result.call_id,
        access_token=result.access_token,
        expires_in_seconds=result.expires_in_seconds,
        conversation_id=result.conversation_id,
    )
