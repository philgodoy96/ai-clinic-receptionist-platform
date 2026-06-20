from typing import Protocol

from app.models.email_jobs import EmailJob


class EmailJobRepository(Protocol):
    def add(self, email_job: EmailJob) -> EmailJob:
        raise NotImplementedError