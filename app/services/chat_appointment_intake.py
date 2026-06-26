from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, cast

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConversationState,
    ExpectedResponseType,
    KnownDoctor,
    KnownSpecialty,
    OfferedSlot,
)
from app.models.scheduling import Doctor, Specialty
from app.services.chat_turn_understanding_interpreter import ChatTurnUnderstandingInterpreter
from app.services.clinic_time import ClinicTimeService
from app.services.date_parsing import DateParseStatus, NaturalLanguageDateParser
from app.services.scheduling import SchedulingService
from app.services.time_preferences import TimePreferenceParser, TimePreferenceStatus

logger = logging.getLogger(__name__)

_CTU_LOW_CONFIDENCE_THRESHOLD = 0.5

_APPOINTMENT_INTAKE_INTENTS = frozenset(
    {
        ChatTurnIntent.APPOINTMENT_REQUEST,
        ChatTurnIntent.AVAILABILITY_REQUEST,
        ChatTurnIntent.SLOT_SELECTION,
        ChatTurnIntent.FALLBACK,
    },
)

_SOONEST_MARKERS = (
    "soonest",
    "earliest",
    "as soon as possible",
    "asap",
    "first available",
)

EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS = 14


@dataclass(frozen=True, slots=True)
class AppointmentSearchCriteria:
    selected_specialty_id: str | None = None
    selected_specialty_name: str | None = None
    selected_doctor_id: str | None = None
    selected_doctor_name: str | None = None
    requested_date: str | None = None
    requested_time_window: dict[str, str] | None = None
    soonest_requested: bool = False
    search_start_date: str | None = None
    search_end_date: str | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class ChatAppointmentIntakeResult:
    intent: str
    content: str | None = None
    chat_context_updates: dict[str, Any] = field(default_factory=dict)
    search_criteria: AppointmentSearchCriteria | None = None


@dataclass(frozen=True, slots=True)
class _SpecialtyValidation:
    specialty_id: str | None = None
    specialty_name: str | None = None
    clarification: str | None = None


@dataclass(frozen=True, slots=True)
class _DoctorValidation:
    doctor_id: str | None = None
    doctor_name: str | None = None
    clarification: str | None = None


@dataclass(frozen=True, slots=True)
class _DateValidation:
    requested_date: str | None = None
    clarification: str | None = None


@dataclass(frozen=True, slots=True)
class _TimeValidation:
    requested_time_window: dict[str, str] | None = None
    clarification: str | None = None


class ChatAppointmentIntakeOrchestrator:
    def __init__(
        self,
        *,
        scheduling: SchedulingService,
        date_parser: NaturalLanguageDateParser | None = None,
        time_preference_parser: TimePreferenceParser | None = None,
        clinic_time_service: ClinicTimeService | None = None,
        chat_turn_understanding_interpreter: ChatTurnUnderstandingInterpreter | None = None,
    ) -> None:
        self.scheduling = scheduling
        self.date_parser = date_parser or NaturalLanguageDateParser()
        self.time_preference_parser = time_preference_parser or TimePreferenceParser()
        self.clinic_time_service = clinic_time_service
        self.chat_turn_understanding_interpreter = chat_turn_understanding_interpreter

    def handle(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> ChatAppointmentIntakeResult:
        if self.chat_turn_understanding_interpreter is None:
            return self._noop_result()

        understanding = self._interpret_turn(message=message, chat_context=chat_context)
        if understanding is None:
            return self._noop_result()

        if not self._is_actionable_understanding(understanding):
            return self._noop_or_clarification_result(understanding)

        return self._apply_understanding(
            message=message,
            chat_context=chat_context,
            understanding=understanding,
        )

    def _noop_result(self) -> ChatAppointmentIntakeResult:
        return ChatAppointmentIntakeResult(intent="noop")

    def _noop_or_clarification_result(
        self,
        understanding: ChatTurnUnderstandingResult,
    ) -> ChatAppointmentIntakeResult:
        if understanding.intent is ChatTurnIntent.FALLBACK:
            return self._noop_result()

        clarification = understanding.clarification_question
        if clarification is None:
            for issue in understanding.ambiguous_fields:
                if issue.clarification_question:
                    clarification = issue.clarification_question
                    break
        if clarification:
            return ChatAppointmentIntakeResult(
                intent="clarification",
                content=clarification,
            )
        return self._noop_result()

    def _interpret_turn(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> ChatTurnUnderstandingResult | None:
        request = self._build_turn_understanding_request(
            message=message,
            chat_context=chat_context,
        )
        try:
            return self.chat_turn_understanding_interpreter.interpret(request)  # type: ignore[union-attr]
        except Exception:
            logger.exception("chat turn understanding interpreter failed during appointment intake")
            return None

    def _build_turn_understanding_request(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
    ) -> ChatTurnUnderstandingRequest:
        offered_slots = self._build_offered_slots(chat_context)
        known_specialties = self._build_known_specialties()
        known_doctors = self._build_known_doctors(chat_context)

        conversation_state, expected_response_type = self._resolve_conversation_state(
            chat_context=chat_context,
            offered_slots=offered_slots,
        )

        allowed_intents = [
            ChatTurnIntent.APPOINTMENT_REQUEST,
            ChatTurnIntent.AVAILABILITY_REQUEST,
            ChatTurnIntent.FALLBACK,
        ]
        if offered_slots:
            allowed_intents.insert(2, ChatTurnIntent.SLOT_SELECTION)

        current_context = {
            key: chat_context[key]
            for key in (
                "selected_specialty_id",
                "selected_specialty_name",
                "selected_doctor_id",
                "selected_doctor_name",
                "requested_date",
                "requested_time_window",
                "offered_slots",
                "offered_doctors",
            )
            if key in chat_context
        }

        return ChatTurnUnderstandingRequest(
            conversation_state=conversation_state,
            expected_response_type=expected_response_type,
            latest_user_message=message,
            allowed_intents=allowed_intents,
            current_context=current_context,
            offered_slots=offered_slots,
            known_specialties=known_specialties,
            known_doctors=known_doctors,
        )

    def _resolve_conversation_state(
        self,
        *,
        chat_context: dict[str, Any],
        offered_slots: list[OfferedSlot],
    ) -> tuple[ConversationState, ExpectedResponseType]:
        if offered_slots:
            return ConversationState.OFFERING_SLOTS, ExpectedResponseType.SLOT_SELECTION

        offered_doctors = chat_context.get("offered_doctors")
        has_offered_doctors = isinstance(offered_doctors, list) and bool(offered_doctors)
        has_doctor = bool(chat_context.get("selected_doctor_id"))
        has_provider = bool(
            chat_context.get("selected_specialty_id") or chat_context.get("selected_doctor_id"),
        )
        has_date = bool(chat_context.get("requested_date"))

        if has_offered_doctors and not has_doctor:
            return (
                ConversationState.COLLECTING_APPOINTMENT_REQUEST,
                ExpectedResponseType.SPECIALTY_OR_DOCTOR,
            )
        if not has_provider:
            return (
                ConversationState.COLLECTING_APPOINTMENT_REQUEST,
                ExpectedResponseType.SPECIALTY_OR_DOCTOR,
            )
        if not has_date:
            return (
                ConversationState.COLLECTING_APPOINTMENT_REQUEST,
                ExpectedResponseType.DATE_OR_TIME,
            )
        return ConversationState.COLLECTING_APPOINTMENT_REQUEST, ExpectedResponseType.OPEN_TEXT

    def _build_known_specialties(self) -> list[KnownSpecialty]:
        return [
            KnownSpecialty(id=str(specialty.id), name=specialty.name)
            for specialty in self.scheduling.list_specialties()
        ]

    def _build_known_doctors(self, chat_context: dict[str, Any]) -> list[KnownDoctor]:
        doctors_by_id: dict[str, KnownDoctor] = {}

        for doctor in self.scheduling.list_doctors():
            doctors_by_id[str(doctor.id)] = KnownDoctor(
                id=str(doctor.id),
                full_name=doctor.full_name,
                specialty_id=str(doctor.specialty_id) if doctor.specialty_id else None,
            )

        offered_doctors = chat_context.get("offered_doctors")
        if isinstance(offered_doctors, list):
            for item in offered_doctors:
                if not isinstance(item, dict):
                    continue
                doctor_id = item.get("id") or item.get("doctor_id")
                full_name = item.get("full_name") or item.get("doctor_name")
                if not isinstance(doctor_id, str) or not isinstance(full_name, str):
                    continue
                doctors_by_id[doctor_id] = KnownDoctor(
                    id=doctor_id,
                    full_name=full_name,
                    specialty_id=(
                        str(item["specialty_id"])
                        if isinstance(item.get("specialty_id"), str)
                        else None
                    ),
                )

        return list(doctors_by_id.values())

    def _build_offered_slots(self, chat_context: dict[str, Any]) -> list[OfferedSlot]:
        raw_slots = chat_context.get("offered_slots")
        if not isinstance(raw_slots, list):
            return []

        offered_slots: list[OfferedSlot] = []
        for item in raw_slots:
            if not isinstance(item, dict):
                continue
            reference = item.get("availability_slot_id")
            start_time = item.get("start_time")
            if not isinstance(reference, str) or not isinstance(start_time, str):
                continue
            offered_slots.append(
                OfferedSlot(
                    reference=reference,
                    start_time=start_time,
                    doctor_id=(
                        str(item["doctor_id"]) if isinstance(item.get("doctor_id"), str) else None
                    ),
                    doctor_name=(
                        str(item["doctor_name"])
                        if isinstance(item.get("doctor_name"), str)
                        else None
                    ),
                    specialty_name=(
                        str(item["specialty_name"])
                        if isinstance(item.get("specialty_name"), str)
                        else None
                    ),
                    display_label=(
                        str(item.get("display_time") or item.get("display_label"))
                        if item.get("display_time") or item.get("display_label")
                        else None
                    ),
                ),
            )
        return offered_slots

    def _is_actionable_understanding(
        self,
        understanding: ChatTurnUnderstandingResult,
    ) -> bool:
        if understanding.intent not in _APPOINTMENT_INTAKE_INTENTS:
            return False

        if understanding.intent is ChatTurnIntent.FALLBACK:
            return False

        if understanding.confidence < _CTU_LOW_CONFIDENCE_THRESHOLD:
            return False

        if understanding.ambiguous_fields:
            return False

        return True

    def _apply_understanding(
        self,
        *,
        message: str,
        chat_context: dict[str, Any],
        understanding: ChatTurnUnderstandingResult,
    ) -> ChatAppointmentIntakeResult:
        extracted = understanding.extracted_fields
        normalized_message = message.lower()

        specialty_result = self._validate_specialty(
            extracted_specialty=extracted.specialty,
            specialty_raw=extracted.specialty_raw,
            normalized_message=normalized_message,
        )
        if specialty_result is not None and specialty_result.clarification is not None:
            return ChatAppointmentIntakeResult(
                intent="clarification",
                content=specialty_result.clarification,
            )

        doctor_result = self._validate_doctor(
            extracted_doctor_name=extracted.doctor_name,
            doctor_name_raw=extracted.doctor_name_raw,
            normalized_message=normalized_message,
            specialty_id=specialty_result.specialty_id if specialty_result else None,
            chat_context=chat_context,
        )
        if doctor_result.clarification is not None:
            return ChatAppointmentIntakeResult(
                intent="clarification",
                content=doctor_result.clarification,
            )

        if specialty_result and doctor_result.doctor_id:
            mismatch = self._doctor_specialty_mismatch(
                specialty_id=specialty_result.specialty_id,
                specialty_name=specialty_result.specialty_name,
                doctor_id=doctor_result.doctor_id,
            )
            if mismatch is not None:
                return ChatAppointmentIntakeResult(
                    intent="clarification",
                    content=mismatch,
                )

        date_result = self._validate_date(
            message=message,
            extracted_date=extracted.appointment_date,
            date_raw=extracted.appointment_date_raw,
        )
        if date_result.clarification is not None:
            return ChatAppointmentIntakeResult(
                intent="clarification",
                content=date_result.clarification,
            )

        time_result = self._validate_time_window(
            message=message,
            extracted_window=extracted.appointment_time_window,
            window_raw=extracted.appointment_time_window_raw,
        )
        if time_result.clarification is not None:
            return ChatAppointmentIntakeResult(
                intent="clarification",
                content=time_result.clarification,
            )

        soonest_requested = self._detect_soonest_request(
            message=message,
            understanding=understanding,
        )

        context_updates: dict[str, Any] = {}

        if specialty_result and specialty_result.specialty_id:
            conflict = self._context_conflict(
                chat_context=chat_context,
                field="selected_specialty_id",
                new_value=specialty_result.specialty_id,
                display_name=specialty_result.specialty_name,
                label="specialty",
            )
            if conflict is not None:
                return ChatAppointmentIntakeResult(intent="clarification", content=conflict)
            context_updates["selected_specialty_id"] = specialty_result.specialty_id
            context_updates["selected_specialty_name"] = specialty_result.specialty_name

        if doctor_result.doctor_id:
            conflict = self._context_conflict(
                chat_context=chat_context,
                field="selected_doctor_id",
                new_value=doctor_result.doctor_id,
                display_name=doctor_result.doctor_name,
                label="doctor",
            )
            if conflict is not None:
                return ChatAppointmentIntakeResult(intent="clarification", content=conflict)
            context_updates["selected_doctor_id"] = doctor_result.doctor_id
            context_updates["selected_doctor_name"] = doctor_result.doctor_name

        if date_result.requested_date:
            conflict = self._context_conflict(
                chat_context=chat_context,
                field="requested_date",
                new_value=date_result.requested_date,
                display_name=date_result.requested_date,
                label="date",
            )
            if conflict is not None:
                return ChatAppointmentIntakeResult(intent="clarification", content=conflict)
            context_updates["requested_date"] = date_result.requested_date

        if time_result.requested_time_window:
            existing_window = chat_context.get("requested_time_window")
            if (
                isinstance(existing_window, dict)
                and existing_window != time_result.requested_time_window
            ):
                existing_label = str(existing_window.get("label", "requested"))
                new_label = time_result.requested_time_window.get("label", "requested")
                if existing_label != new_label:
                    return ChatAppointmentIntakeResult(
                        intent="clarification",
                        content=(
                            f"You previously asked for {existing_label}. "
                            f"Did you want to change that to {new_label}?"
                        ),
                    )
            context_updates["requested_time_window"] = time_result.requested_time_window

        if understanding.selected_slot_reference and chat_context.get("offered_slots"):
            context_updates["selected_availability_slot_id"] = understanding.selected_slot_reference

        if not context_updates and not soonest_requested:
            return self._noop_or_clarification_result(understanding)

        search_criteria = self._build_search_criteria(
            chat_context=chat_context,
            context_updates=context_updates,
            soonest_requested=soonest_requested,
        )

        return ChatAppointmentIntakeResult(
            intent="appointment_intake",
            chat_context_updates=context_updates,
            search_criteria=search_criteria,
        )

    def _validate_specialty(
        self,
        *,
        extracted_specialty: str | None,
        specialty_raw: str | None,
        normalized_message: str,
    ) -> _SpecialtyValidation | None:
        candidates: list[str] = []
        if extracted_specialty:
            candidates.append(extracted_specialty)
        if specialty_raw:
            candidates.append(specialty_raw)

        matched: Specialty | None = None
        for candidate in candidates:
            matched = self._match_specialty_name(candidate)
            if matched is not None:
                break

        if matched is None:
            matched = self._match_specialty_in_message(normalized_message)

        if matched is None:
            if candidates:
                return _SpecialtyValidation(
                    clarification=(
                        "I couldn't find that specialty at our clinic. "
                        "Could you tell me which specialty you need?"
                    ),
                )
            return None

        return _SpecialtyValidation(
            specialty_id=str(matched.id),
            specialty_name=matched.name,
        )

    def _validate_doctor(
        self,
        *,
        extracted_doctor_name: str | None,
        doctor_name_raw: str | None,
        normalized_message: str,
        specialty_id: str | None,
        chat_context: dict[str, Any],
    ) -> _DoctorValidation:
        del specialty_id

        search_terms: list[str] = []
        if extracted_doctor_name:
            search_terms.append(extracted_doctor_name)
        if doctor_name_raw:
            search_terms.append(doctor_name_raw)

        offered_doctors_raw = chat_context.get("offered_doctors")
        has_offered_doctors = isinstance(offered_doctors_raw, list) and bool(offered_doctors_raw)
        if has_offered_doctors:
            offered_doctors = cast(list[dict[str, Any]], offered_doctors_raw)
            search_texts = search_terms or [normalized_message]
            offered_matches = self._find_matching_offered_doctors(
                search_texts,
                offered_doctors,
            )
            if offered_matches:
                if len(offered_matches) > 1:
                    names = self._join_names(
                        [
                            str(item["doctor_name"])
                            for item in offered_matches
                            if isinstance(item.get("doctor_name"), str)
                        ],
                    )
                    return _DoctorValidation(
                        clarification=f"Which doctor did you mean: {names}?",
                    )
                item = offered_matches[0]
                doctor_id = item.get("doctor_id")
                doctor_name = item.get("doctor_name")
                if isinstance(doctor_id, str) and isinstance(doctor_name, str):
                    return _DoctorValidation(
                        doctor_id=doctor_id,
                        doctor_name=doctor_name,
                    )
            if search_terms or self._contains_doctor_reference(normalized_message):
                return _DoctorValidation(
                    clarification=(
                        "I couldn't find that doctor among the options I shared. "
                        "Could you pick one of the doctors I listed?"
                    ),
                )
            return _DoctorValidation()

        doctors = list(self.scheduling.list_doctors())

        matches: list[Doctor] = []
        for term in search_terms:
            matches = self._find_matching_doctors(term, doctors)
            if matches:
                break

        if not matches:
            matches = self._find_matching_doctors(normalized_message, doctors)

        if not matches:
            if search_terms or self._contains_doctor_reference(normalized_message):
                return _DoctorValidation(
                    clarification=(
                        "I couldn't find a doctor matching that name. "
                        "Could you provide the doctor's full name?"
                    ),
                )
            return _DoctorValidation()

        if len(matches) > 1:
            names = self._join_names([doctor.full_name for doctor in matches])
            return _DoctorValidation(
                clarification=f"Which doctor did you mean: {names}?",
            )

        doctor = matches[0]
        return _DoctorValidation(
            doctor_id=str(doctor.id),
            doctor_name=doctor.full_name,
        )

    def _doctor_specialty_mismatch(
        self,
        *,
        specialty_id: str | None,
        specialty_name: str | None,
        doctor_id: str,
    ) -> str | None:
        if specialty_id is None or specialty_name is None:
            return None

        doctor = self._find_doctor_by_id(doctor_id)
        if doctor is None:
            return None

        if str(doctor.specialty_id) != specialty_id:
            return (
                f"{doctor.full_name} is not in {specialty_name}. "
                "Would you like to see a different doctor or choose another specialty?"
            )
        return None

    def _validate_date(
        self,
        *,
        message: str,
        extracted_date: str | None,
        date_raw: str | None,
    ) -> _DateValidation:
        if extracted_date and self._is_iso_date(extracted_date):
            return _DateValidation(requested_date=extracted_date)

        parse_text = date_raw or message
        parse_result = self.date_parser.parse(parse_text)

        if parse_result.status == DateParseStatus.PARSED and parse_result.normalized_date:
            return _DateValidation(requested_date=parse_result.normalized_date)

        if parse_result.status == DateParseStatus.NOT_FOUND:
            stripped = self._strip_time_preference_markers(parse_text)
            if stripped != parse_text:
                retry = self.date_parser.parse(stripped)
                if retry.status == DateParseStatus.PARSED and retry.normalized_date:
                    return _DateValidation(requested_date=retry.normalized_date)
            return _DateValidation()

        if date_raw or extracted_date:
            return _DateValidation(
                clarification="Could you tell me which date works for you?",
            )
        return _DateValidation()

    def _validate_time_window(
        self,
        *,
        message: str,
        extracted_window: str | None,
        window_raw: str | None,
    ) -> _TimeValidation:
        if extracted_window:
            window = self._window_dict_for_label(extracted_window)
            if window is not None:
                return _TimeValidation(requested_time_window=window)

        parse_text = window_raw or message
        parse_result = self.time_preference_parser.parse(parse_text)

        if parse_result.status == TimePreferenceStatus.PARSED and parse_result.window:
            return _TimeValidation(
                requested_time_window={
                    "label": parse_result.window.label,
                    "start_time": parse_result.window.start_time,
                    "end_time": parse_result.window.end_time,
                },
            )

        if parse_result.status == TimePreferenceStatus.NOT_FOUND:
            return _TimeValidation()

        if window_raw or extracted_window:
            return _TimeValidation(
                clarification="Could you tell me if you prefer morning, afternoon, or evening?",
            )
        return _TimeValidation()

    def _detect_soonest_request(
        self,
        *,
        message: str,
        understanding: ChatTurnUnderstandingResult,
    ) -> bool:
        normalized = message.lower()
        if any(marker in normalized for marker in _SOONEST_MARKERS):
            return True

        reason = understanding.reason.lower()
        return any(marker in reason for marker in _SOONEST_MARKERS)

    def _build_search_criteria(
        self,
        *,
        chat_context: dict[str, Any],
        context_updates: dict[str, Any],
        soonest_requested: bool,
    ) -> AppointmentSearchCriteria:
        merged = {**chat_context, **context_updates}
        search_start_date: str | None = None
        search_end_date: str | None = None

        has_provider = bool(
            _as_str(merged.get("selected_specialty_id"))
            or _as_str(merged.get("selected_doctor_id")),
        )
        has_explicit_date = bool(_as_str(merged.get("requested_date")))
        should_search_earliest = soonest_requested or (has_provider and not has_explicit_date)

        if should_search_earliest:
            clinic_today: date | None = None
            if self.clinic_time_service is not None:
                clinic_today = self.clinic_time_service.clinic_today()
            else:
                from app.services.date_parsing import SystemClock

                clinic_today = SystemClock().today()

            search_start_date = clinic_today.isoformat()
            search_end_date = (
                clinic_today + timedelta(days=EARLIEST_AVAILABILITY_SEARCH_HORIZON_DAYS)
            ).isoformat()

        requested_window = merged.get("requested_time_window")
        window: dict[str, str] | None = None
        if isinstance(requested_window, dict):
            window = {
                "label": str(requested_window.get("label", "")),
                "start_time": str(requested_window.get("start_time", "")),
                "end_time": str(requested_window.get("end_time", "")),
            }

        return AppointmentSearchCriteria(
            selected_specialty_id=_as_str(merged.get("selected_specialty_id")),
            selected_specialty_name=_as_str(merged.get("selected_specialty_name")),
            selected_doctor_id=_as_str(merged.get("selected_doctor_id")),
            selected_doctor_name=_as_str(merged.get("selected_doctor_name")),
            requested_date=_as_str(merged.get("requested_date")),
            requested_time_window=window,
            soonest_requested=soonest_requested,
            search_start_date=search_start_date,
            search_end_date=search_end_date,
        )

    def _context_conflict(
        self,
        *,
        chat_context: dict[str, Any],
        field: str,
        new_value: str,
        display_name: str | None,
        label: str,
    ) -> str | None:
        existing = chat_context.get(field)
        if not isinstance(existing, str) or existing == new_value:
            return None

        existing_display = existing
        if field == "selected_doctor_id":
            existing_display = chat_context.get("selected_doctor_name", existing)
        elif field == "selected_specialty_id":
            existing_display = chat_context.get("selected_specialty_name", existing)

        new_display = display_name or new_value
        return (
            f"You previously selected {existing_display} as your {label}. "
            f"Did you want to change that to {new_display}?"
        )

    def _match_specialty_name(self, candidate: str) -> Specialty | None:
        normalized = candidate.strip().lower()
        if not normalized:
            return None
        return self._match_specialty_in_message(normalized)

    def _match_specialty_in_message(self, normalized_message: str) -> Specialty | None:
        specialties = sorted(
            self.scheduling.list_specialties(),
            key=lambda specialty: len(specialty.name),
            reverse=True,
        )
        for specialty in specialties:
            if any(term in normalized_message for term in self._specialty_match_terms(specialty)):
                return specialty
        return None

    def _specialty_match_terms(self, specialty: Specialty) -> list[str]:
        name = specialty.name.lower()
        terms = [name]
        if name.endswith("ology"):
            terms.append(f"{name.removesuffix('ology')}ologist")
        return terms

    def _find_matching_doctors(
        self,
        search_text: str,
        doctors: Sequence[Doctor],
    ) -> list[Doctor]:
        normalized = search_text.replace(".", "").lower().strip()
        if not normalized:
            return []

        matches: list[Doctor] = []
        sorted_doctors = sorted(doctors, key=lambda doctor: len(doctor.full_name), reverse=True)
        for doctor in sorted_doctors:
            if self._doctor_name_in_message(normalized, doctor.full_name):
                matches.append(doctor)
                continue
            name_parts = re.sub(r"^dr\.?\s+", "", doctor.full_name.lower()).split()
            if any(len(part) >= 3 and part in normalized for part in name_parts):
                matches.append(doctor)

        return matches

    def _find_matching_offered_doctors(
        self,
        search_texts: Sequence[str],
        offered_doctors: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for search_text in search_texts:
            normalized = search_text.replace(".", "").lower().strip()
            if not normalized:
                continue
            for item in offered_doctors:
                if not isinstance(item, dict):
                    continue
                doctor_id = item.get("doctor_id")
                doctor_name = item.get("doctor_name")
                if not isinstance(doctor_id, str) or not isinstance(doctor_name, str):
                    continue
                if doctor_id in seen_ids:
                    continue
                if self._doctor_name_in_message(normalized, doctor_name):
                    matches.append(item)
                    seen_ids.add(doctor_id)
                    continue
                name_parts = re.sub(r"^dr\.?\s+", "", doctor_name.lower()).split()
                if any(len(part) >= 3 and part in normalized for part in name_parts):
                    matches.append(item)
                    seen_ids.add(doctor_id)

        return matches

    def _doctor_name_in_message(self, normalized_message: str, full_name: str) -> bool:
        normalized_name = full_name.replace(".", "").lower()
        if normalized_name in normalized_message:
            return True
        name_terms = normalized_name.split()
        return all(term in normalized_message for term in name_terms)

    def _find_doctor_by_id(self, doctor_id: str) -> Doctor | None:
        for doctor in self.scheduling.list_doctors():
            if str(doctor.id) == doctor_id:
                return doctor
        return None

    def _contains_doctor_reference(self, normalized_message: str) -> bool:
        return bool(re.search(r"\bdr\.?\s", normalized_message))

    def _strip_time_preference_markers(self, message: str) -> str:
        stripped = message
        for label in ("morning", "afternoon", "evening"):
            stripped = re.sub(rf"\b{label}\b", " ", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\bor\b", " ", stripped, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", stripped).strip()

    def _window_dict_for_label(self, label: str) -> dict[str, str] | None:
        result = self.time_preference_parser.parse(label)
        if result.status == TimePreferenceStatus.PARSED and result.window:
            return {
                "label": result.window.label,
                "start_time": result.window.start_time,
                "end_time": result.window.end_time,
            }
        return None

    def _is_iso_date(self, value: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))

    def _join_names(self, names: Sequence[str]) -> str:
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} or {names[1]}"
        return ", ".join(names[:-1]) + f", or {names[-1]}"


def _as_str(value: object | None) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None
