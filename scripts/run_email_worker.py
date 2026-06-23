"""Production email worker service entrypoint.

Use the same container image as the API with a different command:

    python -m scripts.run_email_worker

The worker runs the RabbitMQ dispatch consumer. For local polling fallback, use
``python -m scripts.run_email_job_worker`` instead.
"""

from __future__ import annotations


def main() -> None:
    from scripts.run_email_job_consumer import main as run_consumer_main

    run_consumer_main()


if __name__ == "__main__":
    main()
