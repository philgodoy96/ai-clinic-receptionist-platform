from __future__ import annotations

import logging
from uuid import uuid4

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.spec import Basic, BasicProperties

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.messaging.email_job_dispatch import (
    decode_email_job_dispatch_message,
)
from app.providers.email import FakeEmailDeliveryProvider
from app.repositories.sqlalchemy.email_jobs import SQLAlchemyEmailJobRepository
from app.services.email_job_worker import EmailJobWorkerService

logger = logging.getLogger("app.email_job_consumer")


def main() -> None:
    configure_logging()
    settings = get_settings()
    worker_id = f"email-consumer-{uuid4()}"

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
        try:
            message = decode_email_job_dispatch_message(body)
            logger.info(
                "email_job_dispatch_received",
                extra={
                    "event": "email_job_dispatch_received",
                    "email_job_id": str(message.email_job_id),
                },
            )

            with SessionLocal() as session:
                repository = SQLAlchemyEmailJobRepository(session)
                provider = FakeEmailDeliveryProvider()
                worker = EmailJobWorkerService(
                    repository=repository,
                    delivery_provider=provider,
                    worker_id=worker_id,
                )
                result = worker.process_one()
                session.commit()

            logger.info(
                "email_job_dispatch_processed",
                extra={
                    "event": "email_job_dispatch_processed",
                    "processed": result.processed,
                    "job_id": str(result.job_id) if result.job_id is not None else None,
                    "status": result.status.value if result.status is not None else None,
                    "error": result.error,
                },
            )
            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception:
            logger.exception(
                "email_job_dispatch_consumer_failed",
                extra={
                    "event": "email_job_dispatch_consumer_failed",
                },
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

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