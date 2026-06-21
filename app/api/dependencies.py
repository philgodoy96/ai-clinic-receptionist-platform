from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.retell.appointment_booking_tools import RetellAppointmentBookingToolAdapter
from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.ai.fake_llm_provider import FakeLLMProvider
from app.cache.redis import get_redis_client
from app.core.config import get_settings
from app.db.session import get_db
from app.messaging.email_job_dispatch import (
    EmailJobDispatchPublisher,
    NoopEmailJobDispatchPublisher,
    RabbitMQEmailJobDispatchPublisher,
)
from app.repositories.redis.appointment_holds import RedisAppointmentHoldRepository
from app.repositories.sqlalchemy.audit_logs import SQLAlchemyAuditLogRepository
from app.repositories.sqlalchemy.conversations import SQLAlchemyConversationRepository
from app.repositories.sqlalchemy.email_jobs import SQLAlchemyEmailJobRepository
from app.repositories.sqlalchemy.human_escalations import SQLAlchemyHumanEscalationRepository
from app.repositories.sqlalchemy.scheduling import (
    SQLAlchemyAppointmentRepository,
    SQLAlchemyAvailabilitySlotRepository,
    SQLAlchemyDoctorRepository,
    SQLAlchemyPatientRepository,
    SQLAlchemySpecialtyRepository,
)
from app.services.appointment_booking import AppointmentBookingService
from app.services.appointment_holds import AppointmentHoldService
from app.services.audit_logs import AuditLogService
from app.services.chat_receptionist import ChatReceptionistService
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.email_jobs import EmailJobService
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.scheduling import SchedulingService
from app.services.slot_filling import LLMChatSlotFillingService


def get_scheduling_service(
    db: Annotated[Session, Depends(get_db)],
) -> SchedulingService:
    return SchedulingService(
        specialties=SQLAlchemySpecialtyRepository(db),
        doctors=SQLAlchemyDoctorRepository(db),
        patients=SQLAlchemyPatientRepository(db),
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
        appointments=SQLAlchemyAppointmentRepository(db),
    )


def get_appointment_hold_service(
    redis_client: Annotated[Any, Depends(get_redis_client)],
) -> AppointmentHoldService:
    settings = get_settings()

    return AppointmentHoldService(
        repository=RedisAppointmentHoldRepository(redis_client),
        ttl_seconds=settings.appointment_hold_ttl_seconds,
    )


def get_appointment_booking_service(
    db: Annotated[Session, Depends(get_db)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
) -> AppointmentBookingService:
    return AppointmentBookingService(
        patients=SQLAlchemyPatientRepository(db),
        doctors=SQLAlchemyDoctorRepository(db),
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
        appointments=SQLAlchemyAppointmentRepository(db),
        hold_service=hold_service,
    )


def get_audit_log_service(
    db: Annotated[Session, Depends(get_db)],
) -> AuditLogService:
    return AuditLogService(
        repository=SQLAlchemyAuditLogRepository(db),
    )


def get_email_job_service(
    db: Annotated[Session, Depends(get_db)],
) -> EmailJobService:
    return EmailJobService(
        repository=SQLAlchemyEmailJobRepository(db),
    )


def get_human_handoff_notification_service(
    email_jobs: Annotated[EmailJobService, Depends(get_email_job_service)],
) -> HumanHandoffNotificationService:
    return HumanHandoffNotificationService(email_jobs=email_jobs)


def get_conversation_service(
    db: Annotated[Session, Depends(get_db)],
) -> ConversationService:
    return ConversationService(
        repository=SQLAlchemyConversationRepository(db),
    )


def get_human_escalation_service(
    db: Annotated[Session, Depends(get_db)],
) -> HumanEscalationService:
    return HumanEscalationService(
        repository=SQLAlchemyHumanEscalationRepository(db),
    )


def get_conversation_health_service() -> ConversationHealthService:
    return ConversationHealthService()


def get_llm_receptionist_analysis_service() -> LLMReceptionistAnalysisService:
    return LLMReceptionistAnalysisService(provider=FakeLLMProvider())


def get_natural_language_date_parser() -> NaturalLanguageDateParser:
    return NaturalLanguageDateParser()


def get_llm_chat_slot_filling_service(
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    date_parser: Annotated[
        NaturalLanguageDateParser,
        Depends(get_natural_language_date_parser),
    ],
) -> LLMChatSlotFillingService:
    return LLMChatSlotFillingService(
        scheduling=scheduling_service,
        date_parser=date_parser,
    )


def get_chat_receptionist_service(
    conversation_service: Annotated[
        ConversationService,
        Depends(get_conversation_service),
    ],
    scheduling_service: Annotated[
        SchedulingService,
        Depends(get_scheduling_service),
    ],
    hold_service: Annotated[
        AppointmentHoldService,
        Depends(get_appointment_hold_service),
    ],
    booking_service: Annotated[
        AppointmentBookingService,
        Depends(get_appointment_booking_service),
    ],
    llm_analysis: Annotated[
        LLMReceptionistAnalysisService,
        Depends(get_llm_receptionist_analysis_service),
    ],
    slot_filling: Annotated[
        LLMChatSlotFillingService,
        Depends(get_llm_chat_slot_filling_service),
    ],
    conversation_health: Annotated[
        ConversationHealthService,
        Depends(get_conversation_health_service),
    ],
    human_escalations: Annotated[
        HumanEscalationService,
        Depends(get_human_escalation_service),
    ],
    human_handoff_notifications: Annotated[
        HumanHandoffNotificationService,
        Depends(get_human_handoff_notification_service),
    ],
) -> ChatReceptionistService:
    return ChatReceptionistService(
        conversations=conversation_service,
        scheduling=scheduling_service,
        appointment_holds=hold_service,
        appointment_booking=booking_service,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        conversation_health=conversation_health,
        human_escalations=human_escalations,
        human_handoff_notifications=human_handoff_notifications,
    )


def get_email_job_dispatch_publisher() -> EmailJobDispatchPublisher:
    settings = get_settings()

    if not settings.email_job_dispatch_enabled:
        return NoopEmailJobDispatchPublisher()

    return RabbitMQEmailJobDispatchPublisher(
        rabbitmq_url=settings.rabbitmq_url,
        queue_name=settings.email_job_queue_name,
    )


def get_retell_scheduling_tool_adapter(
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> RetellSchedulingToolAdapter:
    return RetellSchedulingToolAdapter(service)


def get_retell_appointment_hold_tool_adapter(
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
) -> RetellAppointmentHoldToolAdapter:
    return RetellAppointmentHoldToolAdapter(
        db=db,
        scheduling_service=scheduling_service,
        hold_service=hold_service,
        audit_logs=audit_logs,
    )


def get_retell_appointment_booking_tool_adapter(
    db: Annotated[Session, Depends(get_db)],
    booking_service: Annotated[
        AppointmentBookingService,
        Depends(get_appointment_booking_service),
    ],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
    email_jobs: Annotated[EmailJobService, Depends(get_email_job_service)],
    email_job_dispatch: Annotated[
        EmailJobDispatchPublisher,
        Depends(get_email_job_dispatch_publisher),
    ],
) -> RetellAppointmentBookingToolAdapter:
    return RetellAppointmentBookingToolAdapter(
        db=db,
        booking_service=booking_service,
        hold_service=hold_service,
        audit_logs=audit_logs,
        email_jobs=email_jobs,
        email_job_dispatch=email_job_dispatch,
    )