from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
