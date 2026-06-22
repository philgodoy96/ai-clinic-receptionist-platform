from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.messaging.email_job_dispatch import (
    EmailJobDispatchDecodeError,
    decode_email_job_dispatch_message,
)
from app.services.email_job_worker import EmailJobWorkerResult, EmailJobWorkerService

logger = logging.getLogger("app.email_job_consumer")


@dataclass(frozen=True, slots=True)
class EmailJobConsumerHandleResult:
    ack: bool
    requeue: bool = False


class EmailJobRabbitMQAcknowledger(Protocol):
    def ack(self, *, delivery_tag: int) -> None:
        raise NotImplementedError

    def nack(self, *, delivery_tag: int, requeue: bool = False) -> None:
        raise NotImplementedError


def apply_email_job_delivery_ack(
    result: EmailJobConsumerHandleResult,
    *,
    delivery_tag: int,
    acknowledger: EmailJobRabbitMQAcknowledger,
) -> None:
    if result.ack:
        acknowledger.ack(delivery_tag=delivery_tag)
        return

    acknowledger.nack(delivery_tag=delivery_tag, requeue=result.requeue)


class EmailJobRabbitMQConsumer:
    def __init__(self, *, worker: EmailJobWorkerService) -> None:
        self.worker = worker

    def handle_delivery(
        self,
        *,
        body: bytes,
        delivery_tag: int,
    ) -> EmailJobConsumerHandleResult:
        try:
            message = decode_email_job_dispatch_message(body)
        except EmailJobDispatchDecodeError as exc:
            logger.warning(
                "email_job_dispatch_invalid_payload",
                extra={
                    "event": "email_job_dispatch_invalid_payload",
                    "error": str(exc),
                },
            )
            return EmailJobConsumerHandleResult(ack=True)

        logger.info(
            "email_job_dispatch_received",
            extra={
                "event": "email_job_dispatch_received",
                "email_job_id": str(message.email_job_id),
            },
        )

        try:
            result = self.worker.process_email_job(message.email_job_id)
        except Exception:
            logger.exception(
                "email_job_dispatch_processing_failed",
                extra={
                    "event": "email_job_dispatch_processing_failed",
                    "email_job_id": str(message.email_job_id),
                },
            )
            return EmailJobConsumerHandleResult(ack=False, requeue=True)

        self._log_processing_result(message.email_job_id, result)
        return EmailJobConsumerHandleResult(ack=True)

    def _log_processing_result(
        self,
        email_job_id: UUID,
        result: EmailJobWorkerResult,
    ) -> None:
        if result.processed:
            logger.info(
                "email_job_dispatch_processed",
                extra={
                    "event": "email_job_dispatch_processed",
                    "email_job_id": str(email_job_id),
                    "processed": True,
                    "job_id": str(result.job_id) if result.job_id is not None else None,
                    "status": result.status.value if result.status is not None else None,
                    "error": result.error,
                },
            )
            return

        logger.info(
            "email_job_dispatch_skipped",
            extra={
                "event": "email_job_dispatch_skipped",
                "email_job_id": str(email_job_id),
                "processed": False,
                "job_id": str(result.job_id) if result.job_id is not None else None,
                "status": result.status.value if result.status is not None else None,
                "skip_reason": result.skip_reason,
            },
        )
