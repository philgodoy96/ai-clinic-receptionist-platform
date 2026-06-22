from __future__ import annotations

import argparse
import logging
import time
from datetime import timedelta
from uuid import uuid4

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.email.factory import create_email_provider_from_settings
from app.services.email_job_worker import EmailJobWorkerResult, EmailJobWorkerService

logger = logging.getLogger("app.email_job_worker")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the email job worker.")
    parser.add_argument("--once", action="store_true", help="Process one job and exit.")
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=2.0,
        help="Sleep interval when no job is available.",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=f"email-worker-{uuid4()}",
        help="Worker identifier used for job locking.",
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    lock_duration = timedelta(seconds=settings.email_job_lock_ttl_seconds)
    provider = create_email_provider_from_settings(settings)

    while True:
        worker = EmailJobWorkerService(
            session_factory=SessionLocal,
            delivery_provider=provider,
            worker_id=args.worker_id,
            lock_duration=lock_duration,
            backoff_base_seconds=settings.email_job_backoff_base_seconds,
            backoff_max_seconds=settings.email_job_backoff_max_seconds,
        )
        results = worker.process_due_email_jobs(limit=1)

        result = results[0] if results else EmailJobWorkerResult(processed=False)

        logger.info(
            "email_worker_iteration_completed",
            extra={
                "event": "email_worker_iteration_completed",
                "processed": result.processed,
                "job_id": str(result.job_id) if result.job_id is not None else None,
                "status": result.status.value if result.status is not None else None,
                "error": result.error,
            },
        )

        if args.once:
            return

        if not result.processed:
            time.sleep(args.sleep_seconds)


if __name__ == "__main__":
    main()
