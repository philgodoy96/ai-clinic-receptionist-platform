from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from app.email.factory import create_email_provider_from_settings
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
        self.sent_idempotency_keys: list[str | None] = []

    def send_email(
        self,
        *,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.sent_payloads.append(payload)
        self.sent_idempotency_keys.append(idempotency_key)

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
    assert client.sent_idempotency_keys == [None]


def test_resend_provider_sends_idempotency_key_from_message() -> None:
    client = FakeResendEmailClient()
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )
    message = OutboundEmailMessage(
        to="patient@example.test",
        subject="Appointment confirmation",
        body="Your appointment is confirmed.",
        idempotency_key="appointment_confirmation:11111111-1111-1111-1111-111111111111",
    )

    result = provider.send(message)

    assert result.provider_message_id == "resend-msg-123"
    assert client.sent_idempotency_keys == [
        "appointment_confirmation:11111111-1111-1111-1111-111111111111",
    ]
    assert "idempotency" not in client.sent_payloads[0]


def test_resend_provider_passes_appointment_confirmation_key_unchanged() -> None:
    from uuid import UUID

    appointment_id = UUID("22222222-2222-2222-2222-222222222222")
    expected_key = f"appointment_confirmation:{appointment_id}"
    client = FakeResendEmailClient()
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )

    provider.send(
        OutboundEmailMessage(
            to="patient@example.test",
            subject="Appointment confirmation",
            body="Body",
            idempotency_key=expected_key,
        ),
    )

    assert client.sent_idempotency_keys == [expected_key]


def test_resend_provider_uses_email_job_fallback_idempotency_key() -> None:
    email_job_id = "33333333-3333-3333-3333-333333333333"
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
            idempotency_key=f"email_job:{email_job_id}",
        ),
    )

    assert client.sent_idempotency_keys == [f"email_job:{email_job_id}"]


def test_resend_provider_hashes_overlong_idempotency_keys() -> None:
    client = FakeResendEmailClient()
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )
    overlong_key = "x" * 300

    provider.send(
        OutboundEmailMessage(
            to="patient@example.test",
            subject="Hello",
            body="Body",
            idempotency_key=overlong_key,
        ),
    )

    sent_key = client.sent_idempotency_keys[0]
    assert sent_key is not None
    assert sent_key.startswith("hash:")
    assert len(sent_key) <= 256


def test_resend_provider_idempotency_key_is_not_logged_on_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_key = "appointment_confirmation:secret-business-key"
    client = FakeResendEmailClient(
        error=ResendEmailClientError("resend api request failed"),
    )
    provider = ResendEmailProvider(
        client=client,
        from_address="sender@example.test",
    )

    with caplog.at_level(logging.ERROR):
        with pytest.raises(EmailProviderError):
            provider.send(
                OutboundEmailMessage(
                    to="patient@example.test",
                    subject="Hello",
                    body="Body",
                    idempotency_key=secret_key,
                ),
            )

    assert secret_key not in caplog.text


def test_build_email_delivery_message_sets_appointment_confirmation_idempotency_key() -> None:
    from uuid import uuid4

    from app.domain.jobs.enums import EmailJobStatus, EmailJobType
    from app.models.email_jobs import EmailJob
    from app.services.email_job_delivery_content import build_email_delivery_message
    from app.services.email_jobs import build_appointment_confirmation_idempotency_key

    appointment_id = uuid4()
    email_job = EmailJob(
        id=uuid4(),
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=EmailJobStatus.PENDING,
        appointment_id=appointment_id,
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="Confirmed.",
        attempt_count=0,
        max_attempts=3,
        idempotency_key=build_appointment_confirmation_idempotency_key(appointment_id),
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    message = build_email_delivery_message(email_job)

    assert message.idempotency_key == f"appointment_confirmation:{appointment_id}"


def test_build_email_delivery_message_uses_email_job_fallback_without_idempotency_key() -> None:
    from uuid import uuid4

    from app.domain.jobs.enums import EmailJobStatus, EmailJobType
    from app.models.email_jobs import EmailJob
    from app.services.email_job_delivery_content import build_email_delivery_message

    job_id = uuid4()
    email_job = EmailJob(
        id=job_id,
        job_type=EmailJobType.APPOINTMENT_CONFIRMATION,
        status=EmailJobStatus.PENDING,
        appointment_id=uuid4(),
        patient_id=uuid4(),
        recipient_email="patient@example.test",
        subject="Appointment confirmation",
        body="Confirmed.",
        attempt_count=0,
        max_attempts=3,
        idempotency_key=None,
        payload={},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    message = build_email_delivery_message(email_job)

    assert message.idempotency_key == f"email_job:{job_id}"


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


def test_resend_client_error_message_is_generic() -> None:
    error = ResendEmailClientError("resend api request failed")

    assert "Bearer" not in str(error)
    assert "re_" not in str(error)


def test_create_resend_provider_logs_do_not_expose_api_key(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "re_super_secret_key")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "sender@example.test")

    settings = Settings(_env_file=None)

    with caplog.at_level(logging.INFO, logger="app.email_provider"):
        provider = create_email_provider_from_settings(settings)

    assert provider is not None
    assert "re_super_secret_key" not in caplog.text
    assert "Bearer" not in caplog.text
