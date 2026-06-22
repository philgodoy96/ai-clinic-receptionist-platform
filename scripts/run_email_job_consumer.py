from __future__ import annotations

import logging
from datetime import timedelta
from uuid import uuid4

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.email.factory import create_email_provider_from_settings
from app.messaging.email_job_consumer import EmailJobRabbitMQConsumer
from app.services.email_job_worker import EmailJobWorkerService

logger = logging.getLogger("app.email_job_consumer")


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

        if result.ack:
            channel.basic_ack(delivery_tag=method.delivery_tag)

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

    try:
        channel.start_consuming()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
