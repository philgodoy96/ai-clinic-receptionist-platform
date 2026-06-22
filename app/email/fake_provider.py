from __future__ import annotations

from app.email.types import EmailProviderError, EmailSendResult, OutboundEmailMessage


class FakeEmailProvider:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.sent_messages: list[OutboundEmailMessage] = []

    def send(self, message: OutboundEmailMessage) -> EmailSendResult:
        if self.should_fail:
            raise EmailProviderError("fake email provider failure")

        self.sent_messages.append(message)
        return EmailSendResult(provider_message_id=f"fake-{len(self.sent_messages)}")
