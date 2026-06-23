from __future__ import annotations

import logging
import signal
from datetime import timedelta
from uuid import uuid4

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.email.factory import create_email_provider_from_settings
from app.messaging.email_job_consumer import (
    EmailJobRabbitMQConsumer,
    apply_email_job_delivery_ack,
)
from app.services.email_job_worker import EmailJobWorkerService

logger = logging.getLogger("app.email_job_consumer")


class _PikaAcknowledger:
    def __init__(self, channel: BlockingChannel) -> None:
        self._channel = channel

    def ack(self, *, delivery_tag: int) -> None:
        self._channel.basic_ack(delivery_tag=delivery_tag)

    def nack(self, *, delivery_tag: int, requeue: bool = False) -> None:
        self._channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)


def main() -> None:
    configure_logging()
    settings = get_settings()
    worker_id = f"email-consumer-{uuid4()}"
    lock_duration = timedelta(seconds=settings.email_job_lock_ttl_seconds)
    provider = create_email_provider_from_settings(settings)

    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    channel.queue_declare(queue=settings.email_job_queue_name, durable=True)
    channel.basic_qos(prefetch_count=1)

    def handle_message(
        channel: BlockingChannel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
    ) -> None:
        worker = EmailJobWorkerService(
            session_factory=SessionLocal,
            delivery_provider=provider,
            worker_id=worker_id,
            lock_duration=lock_duration,
            backoff_base_seconds=settings.email_job_backoff_base_seconds,
            backoff_max_seconds=settings.email_job_backoff_max_seconds,
        )
        consumer = EmailJobRabbitMQConsumer(worker=worker)
        result = consumer.handle_delivery(
            body=body,
            delivery_tag=method.delivery_tag,
        )
        apply_email_job_delivery_ack(
            result,
            delivery_tag=method.delivery_tag,
            acknowledger=_PikaAcknowledger(channel),
        )

    channel.basic_consume(
        queue=settings.email_job_queue_name,
        on_message_callback=handle_message,
    )
    logger.info(
        "email_job_consumer_started",
        extra={
            "event": "email_job_consumer_started",
            "queue_name": settings.email_job_queue_name,
            "worker_id": worker_id,
        },
    )

    def request_shutdown(signum: int, _frame: object | None) -> None:
        logger.info(
            "email_job_consumer_shutdown_requested",
            extra={
                "event": "email_job_consumer_shutdown_requested",
                "signal": signum,
                "worker_id": worker_id,
            },
        )
        if channel.is_open:
            channel.stop_consuming()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    try:
        channel.start_consuming()
    finally:
        if connection.is_open:
            connection.close()


if __name__ == "__main__":
    main()
