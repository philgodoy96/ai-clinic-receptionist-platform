from sqlalchemy.orm import Session

from app.models.email_jobs import EmailJob


class SQLAlchemyEmailJobRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, email_job: EmailJob) -> EmailJob:
        self.session.add(email_job)
        self.session.flush()

        return email_job