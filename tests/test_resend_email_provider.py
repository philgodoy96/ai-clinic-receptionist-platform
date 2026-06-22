from __future__ import annotations

from typing import Any

import pytest

from app.email.resend_client import ResendEmailClientError
from app.email.resend_provider import ResendEmailProvider
from app.email.types import EmailProviderError, OutboundEmailMessage


class FakeResendEmailClient:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = {"id": "resend-msg-123"} if response is None else response
        self.error = error
        self.sent_payloads: list[dict[str, Any]] = []

    def send_email(self, *, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent_payloads.append(payload)

        if self.error is not None:
            raise self.error

        return self.response


def test_resend_provider_sends_email_using_client() -> None:
    client = FakeResendEmailClient()
    provider = ResendEmailProvider(
        client=client,
        from_address="Clinic <sender@example.test>",
        reply_to="replies@example.test",
    )
    message = OutboundEmailMessage(
        to="patient@example.test",
        subject="Appointment confirmation",
        body="Your appointment is confirmed.",
    )

    result = provider.send(message)

    assert result.provider_message_id == "resend-msg-123"
    assert client.sent_payloads == [
        {
            "from": "Clinic <sender@example.test>",
            "to": ["patient@example.test"],
            "subject": "Appointment confirmation",
            "text": "Your appointment is confirmed.",
            "reply_to": "replies@example.test",
        },
    ]


def test_resend_provider_omits_reply_to_when_not_configured() -> None:
    client = FakeResendEmailClient()
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )

    provider.send(
        OutboundEmailMessage(
            to="patient@example.test",
            subject="Hello",
            body="Body",
        ),
    )

    assert "reply_to" not in client.sent_payloads[0]


def test_resend_provider_maps_client_errors_to_email_provider_error() -> None:
    client = FakeResendEmailClient(error=ResendEmailClientError("resend api request failed"))
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )

    with pytest.raises(EmailProviderError, match="resend email delivery failed"):
        provider.send(
            OutboundEmailMessage(
                to="patient@example.test",
                subject="Hello",
                body="Body",
            ),
        )


def test_resend_provider_raises_when_response_has_no_message_id() -> None:
    client = FakeResendEmailClient(response={})
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )

    with pytest.raises(EmailProviderError, match="returned no message id"):
        provider.send(
            OutboundEmailMessage(
                to="patient@example.test",
                subject="Hello",
                body="Body",
            ),
        )


def test_resend_provider_error_does_not_include_api_key() -> None:
    client = FakeResendEmailClient(
        error=ResendEmailClientError("resend api request failed for Bearer secret-key"),
    )
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )

    with pytest.raises(EmailProviderError) as exc_info:
        provider.send(
            OutboundEmailMessage(
                to="patient@example.test",
                subject="Hello",
                body="Body",
            ),
        )

    assert "secret-key" not in str(exc_info.value)
