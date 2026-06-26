from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.retell.appointment_booking_tools import RetellAppointmentBookingToolAdapter
from app.adapters.retell.appointment_hold_tools import RetellAppointmentHoldToolAdapter
from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.ai.llm_provider import LLMProvider
from app.ai.provider_factory import build_llm_provider
from app.cache.redis import get_redis_client
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.integrations.retell.signature import (
    RetellSignatureVerifier,
    create_retell_signature_verifier,
)
from app.messaging.email_job_dispatch import (
    EmailJobDispatchPublisher,
    NoopEmailJobDispatchPublisher,
    RabbitMQEmailJobDispatchPublisher,
)
from app.repositories.redis.appointment_holds import RedisAppointmentHoldRepository
from app.repositories.redis.patient_resolution import RedisPatientResolutionRepository
from app.repositories.sqlalchemy.appointments import (
    SQLAlchemyAppointmentCancellationAttemptRepository,
    SQLAlchemyAppointmentRescheduleAttemptRepository,
)
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
from app.repositories.sqlalchemy.voice_booking_attempts import (
    SQLAlchemyVoiceBookingAttemptRepository,
)
from app.repositories.sqlalchemy.voice_calls import SQLAlchemyVoiceCallRepository
from app.services.appointment_booking import AppointmentBookingService
from app.services.appointment_cancellation import AppointmentCancellationService
from app.services.appointment_holds import AppointmentHoldService
from app.services.appointment_rescheduling import AppointmentReschedulingService
from app.services.audit_logs import AuditLogService
from app.services.chat_receptionist import ChatReceptionistService
from app.services.clinic_time import ClinicTimeService
from app.services.clock import SystemClock
from app.services.conversation_health import ConversationHealthService
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.demo_guardrails import DemoGuardrailService
from app.services.email_jobs import EmailJobService
from app.services.human_escalations import HumanEscalationService
from app.services.human_handoff_notifications import HumanHandoffNotificationService
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    build_llm_receptionist_analysis_service_from_settings,
)
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.public_demo_voice_session import PublicDemoVoiceSessionService
from app.services.receptionist_response_generator import (
    ReceptionistResponseGenerator,
    build_receptionist_response_generator_from_settings,
)
from app.services.retell_call_lifecycle import RetellCallLifecycleService
from app.services.retell_tool_adapter import RetellToolCallingAdapter
from app.services.retell_web_call import (
    RetellWebCallService,
    create_retell_web_call_service_from_settings,
)
from app.services.scheduling import SchedulingAvailabilityPolicy, SchedulingService
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from app.services.voice_appointment_cancellation import VoiceAppointmentCancellationService
from app.services.voice_appointment_rescheduling import VoiceAppointmentReschedulingService
from app.services.voice_booking_confirmation import VoiceBookingConfirmationService
from app.services.voice_calls import VoiceCallInspectionService
from app.services.voice_conversation_bridge import VoiceConversationBridgeService
from app.services.voice_patient_appointment_lookup import VoicePatientAppointmentLookupService


def get_demo_guardrail_service(
    redis_client: Annotated[Any, Depends(get_redis_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DemoGuardrailService:
    return DemoGuardrailService(
        redis_client=redis_client,
        settings=settings,
        clock=SystemClock(),
    )


def get_appointment_hold_service(
    redis_client: Annotated[Any, Depends(get_redis_client)],
) -> AppointmentHoldService:
    settings = get_settings()

    return AppointmentHoldService(
        repository=RedisAppointmentHoldRepository(redis_client),
        ttl_seconds=settings.appointment_hold_ttl_seconds,
    )


def get_clinic_time_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClinicTimeService:
    return ClinicTimeService.from_settings(settings, clock=SystemClock())


def get_scheduling_service(
    db: Annotated[Session, Depends(get_db)],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> SchedulingService:
    return SchedulingService(
        specialties=SQLAlchemySpecialtyRepository(db),
        doctors=SQLAlchemyDoctorRepository(db),
        patients=SQLAlchemyPatientRepository(db),
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
        appointments=SQLAlchemyAppointmentRepository(db),
        clinic_time_service=clinic_time_service,
        hold_service=hold_service,
        availability_policy=SchedulingAvailabilityPolicy(
            min_booking_lead_minutes=settings.scheduling_min_booking_lead_minutes,
            booking_horizon_days=settings.scheduling_booking_horizon_days,
        ),
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
        clock=SystemClock(),
    )


def get_conversation_health_service() -> ConversationHealthService:
    return ConversationHealthService()


def get_llm_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LLMProvider:
    return build_llm_provider(settings)


def get_llm_receptionist_analysis_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LLMReceptionistAnalysisService | None:
    return build_llm_receptionist_analysis_service_from_settings(settings)


def get_natural_language_date_parser() -> NaturalLanguageDateParser:
    return NaturalLanguageDateParser()


def get_time_preference_parser() -> TimePreferenceParser:
    return TimePreferenceParser()


def get_llm_chat_slot_filling_service(
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    date_parser: Annotated[
        NaturalLanguageDateParser,
        Depends(get_natural_language_date_parser),
    ],
    time_preference_parser: Annotated[
        TimePreferenceParser,
        Depends(get_time_preference_parser),
    ],
) -> LLMChatSlotFillingService:
    return LLMChatSlotFillingService(
        scheduling=scheduling_service,
        date_parser=date_parser,
        time_preference_parser=time_preference_parser,
    )


def get_receptionist_response_generator(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReceptionistResponseGenerator:
    return build_receptionist_response_generator_from_settings(settings)


def get_email_job_dispatch_publisher() -> EmailJobDispatchPublisher:
    settings = get_settings()

    if not settings.email_job_dispatch_enabled:
        return NoopEmailJobDispatchPublisher()

    return RabbitMQEmailJobDispatchPublisher(
        rabbitmq_url=settings.rabbitmq_url,
        queue_name=settings.email_job_queue_name,
    )


def get_retell_signature_verifier(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RetellSignatureVerifier:
    return create_retell_signature_verifier(settings)


def get_retell_call_lifecycle_service(
    db: Annotated[Session, Depends(get_db)],
) -> RetellCallLifecycleService:
    return RetellCallLifecycleService(
        repository=SQLAlchemyVoiceCallRepository(db),
    )


def get_voice_call_inspection_service(
    db: Annotated[Session, Depends(get_db)],
) -> VoiceCallInspectionService:
    return VoiceCallInspectionService(
        repository=SQLAlchemyVoiceCallRepository(db),
    )


def get_retell_scheduling_tool_adapter(
    service: Annotated[SchedulingService, Depends(get_scheduling_service)],
) -> RetellSchedulingToolAdapter:
    return RetellSchedulingToolAdapter(service)


def get_voice_conversation_bridge_service(
    db: Annotated[Session, Depends(get_db)],
) -> VoiceConversationBridgeService:
    conversation_repository = SQLAlchemyConversationRepository(db)
    return VoiceConversationBridgeService(
        voice_calls=SQLAlchemyVoiceCallRepository(db),
        conversations=conversation_repository,
        conversation_service=ConversationService(repository=conversation_repository),
    )


def get_patient_intake_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PatientIntakeService:
    return PatientIntakeService(
        patients=SQLAlchemyPatientRepository(db),
        mode=settings.voice_patient_intake_mode,
        db=db,
    )


def get_patient_identity_resolution_service(
    db: Annotated[Session, Depends(get_db)],
    redis_client: Annotated[Any, Depends(get_redis_client)],
    settings: Annotated[Settings, Depends(get_settings)],
    patient_intake: Annotated[PatientIntakeService, Depends(get_patient_intake_service)],
) -> PatientIdentityResolutionService:
    return PatientIdentityResolutionService(
        patients=SQLAlchemyPatientRepository(db),
        resolutions=RedisPatientResolutionRepository(redis_client),
        patient_intake=patient_intake,
        resolution_ttl_seconds=settings.patient_resolution_ttl_seconds,
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
        LLMReceptionistAnalysisService | None,
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
    date_parser: Annotated[
        NaturalLanguageDateParser,
        Depends(get_natural_language_date_parser),
    ],
    time_preference_parser: Annotated[
        TimePreferenceParser,
        Depends(get_time_preference_parser),
    ],
    response_generator: Annotated[
        ReceptionistResponseGenerator,
        Depends(get_receptionist_response_generator),
    ],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    patient_identity_resolution: Annotated[
        PatientIdentityResolutionService,
        Depends(get_patient_identity_resolution_service),
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
        date_parser=date_parser,
        time_preference_parser=time_preference_parser,
        clinic_time_service=clinic_time_service,
        response_generator=response_generator,
        response_generation_mode=settings.receptionist_response_mode,
        patient_identity_resolution=patient_identity_resolution,
    )


def get_voice_booking_confirmation_service(
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    booking_service: Annotated[
        AppointmentBookingService,
        Depends(get_appointment_booking_service),
    ],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
    email_jobs: Annotated[EmailJobService, Depends(get_email_job_service)],
    email_job_dispatch: Annotated[
        EmailJobDispatchPublisher,
        Depends(get_email_job_dispatch_publisher),
    ],
    demo_guardrails: Annotated[DemoGuardrailService, Depends(get_demo_guardrail_service)],
    patient_intake: Annotated[PatientIntakeService, Depends(get_patient_intake_service)],
    patient_identity_resolution: Annotated[
        PatientIdentityResolutionService,
        Depends(get_patient_identity_resolution_service),
    ],
) -> VoiceBookingConfirmationService:
    conversation_repository = SQLAlchemyConversationRepository(db)
    return VoiceBookingConfirmationService(
        db=db,
        booking_service=booking_service,
        hold_service=hold_service,
        scheduling_service=scheduling_service,
        conversations=ConversationService(repository=conversation_repository),
        audit_logs=audit_logs,
        email_jobs=email_jobs,
        voice_booking_attempts=SQLAlchemyVoiceBookingAttemptRepository(db),
        appointments=SQLAlchemyAppointmentRepository(db),
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
        email_job_dispatch=email_job_dispatch,
        demo_guardrails=demo_guardrails,
        patient_intake=patient_intake,
        patient_identity_resolution=patient_identity_resolution,
    )


def get_appointment_cancellation_service(
    db: Annotated[Session, Depends(get_db)],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
) -> AppointmentCancellationService:
    return AppointmentCancellationService(
        appointments=SQLAlchemyAppointmentRepository(db),
        cancellation_attempts=SQLAlchemyAppointmentCancellationAttemptRepository(db),
        audit_logs=audit_logs,
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
    )


def get_appointment_rescheduling_service(
    db: Annotated[Session, Depends(get_db)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    audit_logs: Annotated[AuditLogService, Depends(get_audit_log_service)],
    email_jobs: Annotated[EmailJobService, Depends(get_email_job_service)],
) -> AppointmentReschedulingService:
    conversation_repository = SQLAlchemyConversationRepository(db)
    return AppointmentReschedulingService(
        appointments=SQLAlchemyAppointmentRepository(db),
        availability_slots=SQLAlchemyAvailabilitySlotRepository(db),
        doctors=SQLAlchemyDoctorRepository(db),
        hold_service=hold_service,
        reschedule_attempts=SQLAlchemyAppointmentRescheduleAttemptRepository(db),
        audit_logs=audit_logs,
        conversations=ConversationService(repository=conversation_repository),
        email_jobs=email_jobs,
    )


def get_voice_patient_appointment_lookup_service(
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
    patient_identity_resolution: Annotated[
        PatientIdentityResolutionService,
        Depends(get_patient_identity_resolution_service),
    ],
) -> VoicePatientAppointmentLookupService:
    return VoicePatientAppointmentLookupService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=SQLAlchemyAppointmentRepository(db),
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )


def get_voice_appointment_cancellation_service(
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
    patient_identity_resolution: Annotated[
        PatientIdentityResolutionService,
        Depends(get_patient_identity_resolution_service),
    ],
    appointment_cancellation: Annotated[
        AppointmentCancellationService,
        Depends(get_appointment_cancellation_service),
    ],
) -> VoiceAppointmentCancellationService:
    return VoiceAppointmentCancellationService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=SQLAlchemyAppointmentRepository(db),
        appointment_cancellation=appointment_cancellation,
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )


def get_voice_appointment_rescheduling_service(
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
    patient_identity_resolution: Annotated[
        PatientIdentityResolutionService,
        Depends(get_patient_identity_resolution_service),
    ],
    appointment_rescheduling: Annotated[
        AppointmentReschedulingService,
        Depends(get_appointment_rescheduling_service),
    ],
) -> VoiceAppointmentReschedulingService:
    return VoiceAppointmentReschedulingService(
        patient_identity_resolution=patient_identity_resolution,
        appointments=SQLAlchemyAppointmentRepository(db),
        appointment_rescheduling=appointment_rescheduling,
        scheduling_metadata=scheduling_service,
        clinic_time_service=clinic_time_service,
    )


def get_retell_tool_calling_adapter(
    db: Annotated[Session, Depends(get_db)],
    scheduling_service: Annotated[SchedulingService, Depends(get_scheduling_service)],
    hold_service: Annotated[AppointmentHoldService, Depends(get_appointment_hold_service)],
    voice_conversation_bridge: Annotated[
        VoiceConversationBridgeService,
        Depends(get_voice_conversation_bridge_service),
    ],
    voice_booking_confirmation: Annotated[
        VoiceBookingConfirmationService,
        Depends(get_voice_booking_confirmation_service),
    ],
    appointment_cancellation: Annotated[
        AppointmentCancellationService,
        Depends(get_appointment_cancellation_service),
    ],
    appointment_rescheduling: Annotated[
        AppointmentReschedulingService,
        Depends(get_appointment_rescheduling_service),
    ],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
    patient_identity_resolution: Annotated[
        PatientIdentityResolutionService,
        Depends(get_patient_identity_resolution_service),
    ],
    voice_patient_appointment_lookup: Annotated[
        VoicePatientAppointmentLookupService,
        Depends(get_voice_patient_appointment_lookup_service),
    ],
    voice_appointment_cancellation: Annotated[
        VoiceAppointmentCancellationService,
        Depends(get_voice_appointment_cancellation_service),
    ],
    voice_appointment_rescheduling: Annotated[
        VoiceAppointmentReschedulingService,
        Depends(get_voice_appointment_rescheduling_service),
    ],
) -> RetellToolCallingAdapter:
    conversation_repository = SQLAlchemyConversationRepository(db)
    return RetellToolCallingAdapter(
        scheduling_service=scheduling_service,
        hold_service=hold_service,
        voice_calls=SQLAlchemyVoiceCallRepository(db),
        voice_conversation_bridge=voice_conversation_bridge,
        conversations=ConversationService(repository=conversation_repository),
        voice_booking_confirmation=voice_booking_confirmation,
        appointment_cancellation=appointment_cancellation,
        appointment_rescheduling=appointment_rescheduling,
        appointments=SQLAlchemyAppointmentRepository(db),
        clinic_time_service=clinic_time_service,
        patient_identity_resolution=patient_identity_resolution,
        voice_patient_appointment_lookup=voice_patient_appointment_lookup,
        voice_appointment_cancellation=voice_appointment_cancellation,
        voice_appointment_rescheduling=voice_appointment_rescheduling,
        db=db,
    )


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


def get_retell_web_call_service(
    settings: Annotated[Settings, Depends(get_settings)],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
) -> RetellWebCallService:
    return create_retell_web_call_service_from_settings(
        settings,
        clinic_time_service=clinic_time_service,
    )


def get_public_demo_voice_session_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    clinic_time_service: Annotated[ClinicTimeService, Depends(get_clinic_time_service)],
) -> PublicDemoVoiceSessionService:
    conversation_repository = SQLAlchemyConversationRepository(db)
    return PublicDemoVoiceSessionService(
        retell_web_call_service=create_retell_web_call_service_from_settings(
            settings,
            clinic_time_service=clinic_time_service,
        ),
        voice_calls=SQLAlchemyVoiceCallRepository(db),
        voice_conversation_bridge=VoiceConversationBridgeService(
            voice_calls=SQLAlchemyVoiceCallRepository(db),
            conversations=conversation_repository,
            conversation_service=ConversationService(repository=conversation_repository),
        ),
        conversations=ConversationService(repository=conversation_repository),
    )
