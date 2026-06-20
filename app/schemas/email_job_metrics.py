from datetime import datetime

from pydantic import BaseModel


class EmailJobStatusCountsResponse(BaseModel):
    pending: int
    processing: int
    sent: int
    failed: int
    dead_letter: int


class EmailJobOperationalMetricsResponse(BaseModel):
    total_jobs: int
    counts_by_status: EmailJobStatusCountsResponse
    locked_count: int
    expired_lock_count: int
    overdue_pending_count: int
    oldest_pending_created_at: datetime | None
    oldest_failed_created_at: datetime | None
    newest_dead_letter_created_at: datetime | None
