from __future__ import annotations


def calculate_email_job_backoff_seconds(
    attempt_count: int,
    base_seconds: int,
    max_seconds: int,
) -> int:
    if attempt_count < 1:
        return base_seconds

    backoff = base_seconds * (2 ** (attempt_count - 1))
    return int(min(backoff, max_seconds))
