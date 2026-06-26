"""In-memory fixtures for offline chat scheduling evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProvider, LLMProviderError, LLMRequest, LLMResponse
from app.domain.conversations.enums import ConversationChannel
from app.domain.receptionist.enums import ReceptionistResponseMode
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.domain.scheduling.enums import AppointmentStatus, AvailabilitySlotStatus
from app.domain.scheduling.phone import normalize_phone_digits
from app.domain.voice_patient_intake import VoicePatientIntakeMode
from app.evals.chat_scheduling import ChatSchedulingEvaluationError
from app.models.conversations import Conversation, ConversationMessage
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.repositories.memory.patient_resolution import InMemoryPatientResolutionRepository
from app.services.appointment_booking import AppointmentBookingService
from app.services.appointment_holds import AppointmentHoldService
from app.services.chat_receptionist import ChatReceptionistService
from app.services.clinic_time import ClinicTimeService
from app.services.clock import FixedClock
from app.services.conversations import ConversationService
from app.services.date_parsing import FixedClock as DateParsingFixedClock
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from app.services.patient_identity_resolution import PatientIdentityResolutionService
from app.services.patient_intake import PatientIntakeService
from app.services.scheduling import SchedulingAvailabilityPolicy, SchedulingService
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser

_SUPPORTED_FIXTURES = frozenset(
    {
        "availability_guidance",
        "structured_slot_filling",
        "llm_failure_fallback",
    },
)

_EMILY_JULY_SLOT_1_ID = UUID("11111111-1111-4111-8111-111111111101")
_EMILY_JULY_SLOT_2_ID = UUID("11111111-1111-4111-8111-111111111102")
_EVAL_JANE_DOE_PATIENT_ID = UUID("22222222-2222-4222-8222-222222222201")
_EVAL_CLINIC_NOW_UTC = datetime(2026, 7, 1, 14, 0, tzinfo=UTC)


class _EvalRaisingLLMProvider:
    def complete(self, request: LLMRequest) -> LLMResponse:
        raise LLMProviderError("simulated provider failure")


class _EvalConversationRepository:
    def __init__(self) -> None:
        self.conversations: list[Conversation] = []
        self.messages: list[ConversationMessage] = []

    def add(self, conversation: Conversation) -> Conversation:
        if conversation.id is None:
            conversation.id = uuid4()
        self.conversations.append(conversation)
        return conversation

    def get_by_id(self, conversation_id: UUID) -> Conversation | None:
        for conversation in self.conversations:
            if conversation.id == conversation_id:
                return conversation
        return None

    def get_by_external_id(
        self,
        *,
        channel: ConversationChannel,
        external_conversation_id: str,
    ) -> Conversation | None:
        for conversation in self.conversations:
            if (
                conversation.channel == channel
                and conversation.external_conversation_id == external_conversation_id
            ):
                return conversation
        return None

    def get_by_call_id(self, call_id: str) -> Conversation | None:
        for conversation in self.conversations:
            if conversation.call_id == call_id:
                return conversation
        return None

    def add_message(self, message: ConversationMessage) -> ConversationMessage:
        if message.id is None:
            message.id = uuid4()
        self.messages.append(message)
        return message

    def list_messages(
        self,
        *,
        conversation_id: UUID,
        limit: int,
    ) -> Sequence[ConversationMessage]:
        messages = [
            message for message in self.messages if message.conversation_id == conversation_id
        ]
        return messages[:limit]

    def update(self, conversation: Conversation) -> Conversation:
        return conversation


class _EvalAppointmentHoldRepository:
    def __init__(self) -> None:
        self.holds: dict[tuple[UUID, datetime], AppointmentHold] = {}

    def create(self, hold: AppointmentHold, ttl_seconds: int) -> bool:
        key = (hold.doctor_id, hold.start_time)
        if key in self.holds:
            return False
        self.holds[key] = hold
        return True

    def get(self, *, doctor_id: UUID, start_time: datetime) -> AppointmentHold | None:
        return self.holds.get((doctor_id, start_time))

    def get_by_hold_id(self, hold_id: UUID) -> AppointmentHold | None:
        for hold in self.holds.values():
            if hold.hold_id == hold_id:
                return hold
        return None

    def delete(self, *, doctor_id: UUID, start_time: datetime) -> None:
        self.holds.pop((doctor_id, start_time), None)

    def find_held_availability_slot_ids(
        self,
        *,
        doctor_id: UUID,
        slots: Sequence[tuple[UUID, datetime]],
    ) -> set[UUID]:
        return {
            slot_id
            for slot_id, start_time in slots
            if (doctor_id, start_time) in self.holds
        }


class _EvalAppointmentHoldService(AppointmentHoldService):
    def __init__(self) -> None:
        super().__init__(repository=_EvalAppointmentHoldRepository(), ttl_seconds=300)


class _EvalSpecialtyRepository:
    def __init__(self, specialties: Sequence[Specialty]) -> None:
        self.specialties = list(specialties)

    def list_active(self) -> Sequence[Specialty]:
        return [specialty for specialty in self.specialties if specialty.is_active]

    def get_by_name(self, name: str) -> Specialty | None:
        for specialty in self.specialties:
            if specialty.name == name:
                return specialty
        return None

    def get_by_id(self, specialty_id: UUID) -> Specialty | None:
        for specialty in self.specialties:
            if specialty.id == specialty_id:
                return specialty
        return None


class _EvalDoctorRepository:
    def __init__(self, doctors: Sequence[Doctor]) -> None:
        self.doctors = list(doctors)

    def list_active(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        doctors = [doctor for doctor in self.doctors if doctor.is_active]
        if specialty_id is not None:
            doctors = [doctor for doctor in doctors if doctor.specialty_id == specialty_id]
        return doctors

    def get_by_id(self, doctor_id: UUID) -> Doctor | None:
        for doctor in self.doctors:
            if doctor.id == doctor_id:
                return doctor
        return None


class _EvalPatientRepository:
    def __init__(self, patients: Sequence[Patient]) -> None:
        self.patients = list(patients)

    def get_by_id(self, patient_id: UUID) -> Patient | None:
        for patient in self.patients:
            if patient.id == patient_id:
                return patient
        return None

    def get_by_email(self, email: str) -> Patient | None:
        for patient in self.patients:
            if patient.email == email:
                return patient
        return None

    def get_by_phone_number(self, phone_number: str) -> Patient | None:
        for patient in self.patients:
            if patient.phone_number == phone_number:
                return patient
        return None

    def get_by_identity(
        self,
        *,
        full_name: str,
        date_of_birth: date,
        phone_number: str | None = None,
        email: str | None = None,
    ) -> Patient | None:
        if phone_number is None and email is None:
            return None

        candidates: list[Patient] = []
        for patient in self.patients:
            if patient.full_name != full_name:
                continue
            if patient.date_of_birth != date_of_birth:
                continue
            if email is not None and patient.email != email:
                continue
            candidates.append(patient)

        if not candidates:
            return None

        if phone_number is None:
            return candidates[0]

        normalized_phone = normalize_phone_digits(phone_number)
        for patient in candidates:
            if patient.phone_number is None:
                continue
            if normalize_phone_digits(patient.phone_number) == normalized_phone:
                return patient
        return None

    def list_by_date_of_birth(self, date_of_birth: date) -> list[Patient]:
        return [patient for patient in self.patients if patient.date_of_birth == date_of_birth]

    def add(self, patient: Patient) -> Patient:
        self.patients.append(patient)
        return patient


class _EvalAvailabilitySlotRepository:
    def __init__(self, slots: Sequence[AvailabilitySlot]) -> None:
        self.slots = list(slots)

    def get_by_id(self, slot_id: UUID) -> AvailabilitySlot | None:
        for slot in self.slots:
            if slot.id == slot_id:
                return slot
        return None

    def list_available(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        return [
            slot
            for slot in self.slots
            if slot.doctor_id == doctor_id
            and slot.status == AvailabilitySlotStatus.AVAILABLE
            and slot.start_time >= start_from
            and slot.start_time < start_to
        ]


class _EvalAppointmentRepository:
    def __init__(self, appointments: Sequence[Appointment]) -> None:
        self.appointments = list(appointments)

    def get_by_id(self, appointment_id: UUID) -> Appointment | None:
        for appointment in self.appointments:
            if appointment.id == appointment_id:
                return appointment
        return None

    def list_upcoming_for_patient(
        self,
        *,
        patient_id: UUID,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        return [
            appointment
            for appointment in self.appointments
            if appointment.patient_id == patient_id
            and appointment.status == AppointmentStatus.SCHEDULED
            and appointment.start_time >= start_from
        ]

    def list_cancelable_for_patient(
        self,
        *,
        patient_id: UUID,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        return sorted(
            (
                appointment
                for appointment in self.appointments
                if appointment.patient_id == patient_id
                and appointment.status
                in (AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED)
                and appointment.start_time >= start_from
            ),
            key=lambda appointment: appointment.start_time,
        )

    def find_scheduled_conflict(
        self,
        *,
        doctor_id: UUID,
        start_time: datetime,
    ) -> Appointment | None:
        for appointment in self.appointments:
            if appointment.doctor_id != doctor_id:
                continue
            if appointment.start_time != start_time:
                continue
            if appointment.status != AppointmentStatus.SCHEDULED:
                continue
            return appointment
        return None

    def add(self, appointment: Appointment) -> Appointment:
        if appointment.id is None:
            appointment.id = uuid4()
        self.appointments.append(appointment)
        return appointment

    def find_by_rescheduled_from(
        self,
        *,
        appointment_id: UUID,
    ) -> Appointment | None:
        for appointment in self.appointments:
            if appointment.rescheduled_from_appointment_id == appointment_id:
                return appointment
        return None


def build_chat_scheduling_eval_service(fixture: str) -> ChatReceptionistService:
    if fixture not in _SUPPORTED_FIXTURES:
        allowed = ", ".join(sorted(_SUPPORTED_FIXTURES))
        raise ChatSchedulingEvaluationError(
            f"Unsupported chat scheduling eval fixture '{fixture}'. "
            f"Allowed values: {allowed}",
        )

    scheduling = _create_eval_scheduling_service()

    if fixture == "availability_guidance":
        return _create_availability_guidance_service(scheduling)

    if fixture == "structured_slot_filling":
        hold_service = _EvalAppointmentHoldService()
        appointment_booking = AppointmentBookingService(
            patients=scheduling.patients,
            doctors=scheduling.doctors,
            availability_slots=scheduling.availability_slots,
            appointments=scheduling.appointments,
            hold_service=hold_service,
        )
        return _create_structured_slot_filling_chat_service(
            llm_provider=FakeLLMProvider(),
            scheduling=scheduling,
            appointment_booking=appointment_booking,
        )

    if fixture == "llm_failure_fallback":
        return _create_structured_slot_filling_chat_service(
            llm_provider=_EvalRaisingLLMProvider(),
            scheduling=scheduling,
        )

    raise ChatSchedulingEvaluationError(
        f"Unsupported chat scheduling eval fixture '{fixture}'",
    )


def _create_eval_clinic_time_service() -> ClinicTimeService:
    return ClinicTimeService(
        clinic_name="Demo Clinic",
        timezone="America/New_York",
        business_days="monday,tuesday,wednesday,thursday,friday",
        business_hours_start="09:00",
        business_hours_end="17:00",
        clock=FixedClock(current_time=_EVAL_CLINIC_NOW_UTC),
    )


def _create_eval_scheduling_service() -> SchedulingService:
    dermatology = Specialty(
        id=uuid4(),
        name="Dermatology",
        description="General care",
        is_active=True,
    )
    cardiology = Specialty(
        id=uuid4(),
        name="Cardiology",
        description="General care",
        is_active=True,
    )
    primary_care = Specialty(
        id=uuid4(),
        name="Primary Care",
        description="General care",
        is_active=True,
    )
    emily_carter = Doctor(
        id=uuid4(),
        specialty_id=dermatology.id,
        full_name="Dr. Emily Carter",
        email="emily.carter@example-clinic.test",
        phone_number="+1-555-0101",
        is_active=True,
    )
    doctors = [
        emily_carter,
        Doctor(
            id=uuid4(),
            specialty_id=cardiology.id,
            full_name="Dr. Michael Reed",
            email="michael.reed@example-clinic.test",
            phone_number="+1-555-0102",
            is_active=True,
        ),
        Doctor(
            id=uuid4(),
            specialty_id=primary_care.id,
            full_name="Dr. Sarah Mitchell",
            email="sarah.mitchell@example-clinic.test",
            phone_number="+1-555-0103",
            is_active=True,
        ),
    ]
    eval_patients = [
        Patient(
            id=_EVAL_JANE_DOE_PATIENT_ID,
            full_name="Jane Doe",
            date_of_birth=date(1990, 5, 15),
            phone_number="+1 555-123-4567",
            email="jane.doe@example.com",
        ),
    ]
    availability_slots = [
        AvailabilitySlot(
            id=_EMILY_JULY_SLOT_1_ID,
            doctor_id=emily_carter.id,
            start_time=datetime(2026, 7, 2, 9, 0, tzinfo=UTC),
            end_time=datetime(2026, 7, 2, 9, 30, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
        AvailabilitySlot(
            id=_EMILY_JULY_SLOT_2_ID,
            doctor_id=emily_carter.id,
            start_time=datetime(2026, 7, 2, 10, 30, tzinfo=UTC),
            end_time=datetime(2026, 7, 2, 11, 0, tzinfo=UTC),
            status=AvailabilitySlotStatus.AVAILABLE,
        ),
    ]

    return SchedulingService(
        specialties=_EvalSpecialtyRepository([dermatology, cardiology, primary_care]),
        doctors=_EvalDoctorRepository(doctors),
        patients=_EvalPatientRepository(eval_patients),
        availability_slots=_EvalAvailabilitySlotRepository(availability_slots),
        appointments=_EvalAppointmentRepository(()),
        clinic_time_service=_create_eval_clinic_time_service(),
        hold_service=None,
        availability_policy=SchedulingAvailabilityPolicy(
            min_booking_lead_minutes=60,
            booking_horizon_days=14,
        ),
    )


def _create_chat_receptionist_service(
    *,
    conversations: ConversationService,
    scheduling: SchedulingService,
    hold_service: _EvalAppointmentHoldService | None = None,
    appointment_booking: AppointmentBookingService | None = None,
    llm_analysis: LLMReceptionistAnalysisService | None = None,
    slot_filling: LLMChatSlotFillingService | None = None,
    date_parser: NaturalLanguageDateParser | None = None,
    time_preference_parser: TimePreferenceParser | None = None,
) -> ChatReceptionistService:
    holds = hold_service or _EvalAppointmentHoldService()
    booking = appointment_booking or AppointmentBookingService(
        patients=scheduling.patients,
        doctors=scheduling.doctors,
        availability_slots=scheduling.availability_slots,
        appointments=scheduling.appointments,
        hold_service=holds,
    )
    return ChatReceptionistService(
        conversations=conversations,
        scheduling=scheduling,
        appointment_holds=holds,
        appointment_booking=booking,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        date_parser=date_parser,
        time_preference_parser=time_preference_parser,
        response_generation_mode=ReceptionistResponseMode.DETERMINISTIC,
        patient_identity_resolution=PatientIdentityResolutionService(
            patients=scheduling.patients,
            resolutions=InMemoryPatientResolutionRepository(),
            patient_intake=PatientIntakeService(
                patients=scheduling.patients,
                mode=VoicePatientIntakeMode.DEMO_AUTO_CREATE,
            ),
        ),
    )


def _create_availability_guidance_service(
    scheduling: SchedulingService,
) -> ChatReceptionistService:
    repository = _EvalConversationRepository()
    conversations = ConversationService(repository=repository)
    hold_service = _EvalAppointmentHoldService()
    date_parser = NaturalLanguageDateParser(
        clock=DateParsingFixedClock(current_date=date(2026, 7, 1)),
    )
    return _create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        hold_service=hold_service,
        date_parser=date_parser,
        time_preference_parser=TimePreferenceParser(),
    )


def _create_structured_slot_filling_chat_service(
    *,
    llm_provider: LLMProvider | _EvalRaisingLLMProvider,
    scheduling: SchedulingService,
    appointment_booking: AppointmentBookingService | None = None,
) -> ChatReceptionistService:
    repository = _EvalConversationRepository()
    conversations = ConversationService(repository=repository)
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(),
        time_preference_parser=TimePreferenceParser(),
    )
    llm_analysis = LLMReceptionistAnalysisService(provider=llm_provider)
    return _create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
        appointment_booking=appointment_booking,
    )
