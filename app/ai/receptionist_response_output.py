from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReceptionistLLMPhrasedResponse(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def build_receptionist_phrased_response_openai_json_schema() -> dict[str, Any]:
    return {
        "name": "ReceptionistLLMPhrasedResponse",
        "strict": True,
        "schema": ReceptionistLLMPhrasedResponse.model_json_schema(),
    }
