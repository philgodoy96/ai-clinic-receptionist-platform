from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EmailDeliveryError(Exception):
    """Raised when an email provider cannot deliver a message."""


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailDeliveryProvider(Protocol):
    def send(self, message: EmailMessage) -> None:
        raise NotImplementedError


class FakeEmailDeliveryProvider:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.sent_messages: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        if self.should_fail:
            raise EmailDeliveryError("fake email provider failure")

        self.sent_messages.append(message)