from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetellWebCallRequest:
    demo_session_id: str | None = None
    conversation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RetellWebCallResult:
    provider: str
    call_id: str
    access_token: str
    expires_in_seconds: int
    conversation_id: UUID | None = None
