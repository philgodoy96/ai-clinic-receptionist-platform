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
        "type": message.message_type,
        "email_job_id": str(message.email_job_id),
    }

    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def decode_email_job_dispatch_message(body: bytes) -> EmailJobDispatchMessage:
    payload = json.loads(body.decode("utf-8"))

    return EmailJobDispatchMessage(
        email_job_id=UUID(payload["email_job_id"]),
        message_type=payload["type"],
    )