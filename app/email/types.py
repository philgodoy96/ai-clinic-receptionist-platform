from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EmailProviderError(Exception):
    """Raised when an email provider cannot deliver a message."""


@dataclass(frozen=True, slots=True)
class OutboundEmailMessage:
    to: str
    subject: str
    body: str
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class EmailSendResult:
    provider_message_id: str | None = None


class EmailProvider(Protocol):
    def send(self, message: OutboundEmailMessage) -> EmailSendResult:
        raise NotImplementedError
