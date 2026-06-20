from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class EmailJobStatusCounts:
    pending: int
    processing: int
    sent: int
    failed: int
    dead_letter: int


@dataclass(frozen=True, slots=True)
class EmailJobOperationalMetrics:
    total_jobs: int
    counts_by_status: EmailJobStatusCounts
    locked_count: int
    expired_lock_count: int
    overdue_pending_count: int
    oldest_pending_created_at: datetime | None
    oldest_failed_created_at: datetime | None
    newest_dead_letter_created_at: datetime | None
