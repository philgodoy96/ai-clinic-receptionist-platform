from __future__ import annotations

from uuid import uuid4

from app.messaging.email_job_dispatch import (
    EmailJobDispatchMessage,
    InMemoryEmailJobDispatchPublisher,
    NoopEmailJobDispatchPublisher,
    decode_email_job_dispatch_message,
    encode_email_job_dispatch_message,
)


def test_email_job_dispatch_message_round_trip() -> None:
    message = EmailJobDispatchMessage(email_job_id=uuid4())

    encoded = encode_email_job_dispatch_message(message)
    decoded = decode_email_job_dispatch_message(encoded)

    assert decoded.email_job_id == message.email_job_id
    assert decoded.message_type == "email_job_ready"


def test_noop_email_job_dispatch_publisher_does_nothing() -> None:
    publisher = NoopEmailJobDispatchPublisher()

    publisher.publish_email_job_ready(email_job_id=uuid4())


def test_in_memory_email_job_dispatch_publisher_records_message() -> None:
    publisher = InMemoryEmailJobDispatchPublisher()
    email_job_id = uuid4()

    publisher.publish_email_job_ready(email_job_id=email_job_id)

    assert len(publisher.published_messages) == 1
    assert publisher.published_messages[0].email_job_id == email_job_id
    assert publisher.published_messages[0].message_type == "email_job_ready"