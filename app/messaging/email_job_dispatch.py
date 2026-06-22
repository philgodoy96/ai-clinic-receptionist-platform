from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import BasicProperties


class EmailJobDispatchPublisherError(Exception):
    """Raised when an email job dispatch message cannot be published."""


class EmailJobDispatchDecodeError(ValueError):
    """Raised when an email job dispatch message cannot be decoded."""


@dataclass(frozen=True, slots=True)
class EmailJobDispatchMessage:
    email_job_id: UUID
    message_type: str = "email_job_ready"


class EmailJobDispatchPublisher(Protocol):
    def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
        raise NotImplementedError


class NoopEmailJobDispatchPublisher:
    def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
        return None


class InMemoryEmailJobDispatchPublisher:
    def __init__(self) -> None:
        self.published_messages: list[EmailJobDispatchMessage] = []

    def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
        self.published_messages.append(
            EmailJobDispatchMessage(email_job_id=email_job_id),
        )


class RabbitMQEmailJobDispatchPublisher:
    def __init__(
        self,
        *,
        rabbitmq_url: str,
        queue_name: str,
    ) -> None:
        self.rabbitmq_url = rabbitmq_url
        self.queue_name = queue_name

    def publish_email_job_ready(self, *, email_job_id: UUID) -> None:
        message = EmailJobDispatchMessage(email_job_id=email_job_id)
        body = encode_email_job_dispatch_message(message)
        connection = pika.BlockingConnection(pika.URLParameters(self.rabbitmq_url))

        try:
            channel = cast(BlockingChannel, connection.channel())
            channel.queue_declare(queue=self.queue_name, durable=True)
            channel.basic_publish(
                exchange="",
                routing_key=self.queue_name,
                body=body,
                properties=BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                ),
            )
        except Exception as exc:
            raise EmailJobDispatchPublisherError(
                "failed to publish email job dispatch message",
            ) from exc
        finally:
            connection.close()


def encode_email_job_dispatch_message(message: EmailJobDispatchMessage) -> bytes:
    payload = {
        "email_job_id": str(message.email_job_id),
    }

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def decode_email_job_dispatch_message(body: bytes) -> EmailJobDispatchMessage:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EmailJobDispatchDecodeError("email job dispatch payload is not valid json") from exc

    if not isinstance(payload, dict):
        raise EmailJobDispatchDecodeError("email job dispatch payload must be a json object")

    raw_email_job_id = payload.get("email_job_id")
    if not isinstance(raw_email_job_id, str) or not raw_email_job_id.strip():
        raise EmailJobDispatchDecodeError("email_job_id is required")

    try:
        email_job_id = UUID(raw_email_job_id)
    except ValueError as exc:
        raise EmailJobDispatchDecodeError("email_job_id must be a valid uuid") from exc

    message_type = payload.get("type", "email_job_ready")
    if not isinstance(message_type, str):
        raise EmailJobDispatchDecodeError("type must be a string when provided")

    return EmailJobDispatchMessage(
        email_job_id=email_job_id,
        message_type=message_type,
    )
