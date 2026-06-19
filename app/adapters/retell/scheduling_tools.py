from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.schemas.retell_tools import (
    RetellCheckAvailabilityRequest,
    RetellListDoctorsRequest,
    RetellPatientLookupRequest,
    RetellToolResponse,
    RetellUpcomingAppointmentsRequest,
)
from app.schemas.scheduling import (
    AppointmentResponse,
    AvailabilitySlotResponse,
    DoctorResponse,
    PatientResponse,
    SpecialtyResponse,
)
from app.services.scheduling import (
    DoctorNotFoundError,
    InsufficientPatientIdentityError,
    InvalidAvailabilityWindowError,
    PatientLookupCriteria,
)


class SchedulingServiceForRetell(Protocol):
    def list_specialties(self) -> Sequence[Specialty]:
        raise NotImplementedError

    def list_doctors(self, specialty_id: UUID | None = None) -> Sequence[Doctor]:
        raise NotImplementedError

    def check_availability(
        self,
        *,
        doctor_id: UUID,
        start_from: datetime,
        start_to: datetime,
    ) -> Sequence[AvailabilitySlot]:
        raise NotImplementedError

    def lookup_patient(self, criteria: PatientLookupCriteria) -> Patient | None:
        raise NotImplementedError

    def list_upcoming_appointments(
        self,
        *,
        criteria: PatientLookupCriteria,
        start_from: datetime,
    ) -> Sequence[Appointment]:
        raise NotImplementedError


class RetellSchedulingToolAdapter:
    def __init__(self, service: SchedulingServiceForRetell) -> None:
        self.service = service

    def list_specialties(self) -> RetellToolResponse:
        specialties = [
            self._specialty_to_result(specialty)
            for specialty in self.service.list_specialties()
        ]

        return self._success({"specialties": specialties})

    def list_doctors(self, payload: RetellListDoctorsRequest) -> RetellToolResponse:
        specialty_id, error = self._resolve_specialty_id(payload.specialty_name)

        if error is not None:
            return error

        doctors = list(self.service.list_doctors(specialty_id=specialty_id))

        if payload.doctor_name is not None:
            doctors = [
                doctor
                for doctor in doctors
                if self._matches_human_name(doctor.full_name, payload.doctor_name)
            ]

        return self._success(
            {
                "doctors": [
                    self._doctor_to_result(doctor)
                    for doctor in doctors
                ],
            },
        )

    def check_availability(self, payload: RetellCheckAvailabilityRequest) -> RetellToolResponse:
        doctor_id = payload.doctor_id

        if doctor_id is None:
            doctor_resolution = self._resolve_single_doctor(
                specialty_name=payload.specialty_name,
                doctor_name=payload.doctor_name,
            )

            if isinstance(doctor_resolution, RetellToolResponse):
                return doctor_resolution

            doctor_id = doctor_resolution.id

        try:
            slots = self.service.check_availability(
                doctor_id=doctor_id,
                start_from=payload.start_from,
                start_to=payload.start_to,
            )
        except InvalidAvailabilityWindowError:
            return self._error(
                "invalid_availability_window",
                "The requested availability window is invalid.",
            )
        except DoctorNotFoundError:
            return self._error(
                "doctor_not_found",
                "No matching active doctor was found.",
            )

        return self._success(
            {
                "doctor_id": str(doctor_id),
                "available_slots": [
                    self._availability_slot_to_result(slot)
                    for slot in slots
                ],
            },
        )

    def lookup_patient(self, payload: RetellPatientLookupRequest) -> RetellToolResponse:
        criteria = PatientLookupCriteria(
            full_name=payload.full_name,
            date_of_birth=payload.date_of_birth,
            phone_number=payload.phone_number,
            email=payload.email,
        )

        try:
            patient = self.service.lookup_patient(criteria)
        except InsufficientPatientIdentityError:
            return self._error(
                "insufficient_patient_identity",
                "Patient lookup requires phone number or email.",
            )

        if patient is None:
            return self._success(
                {
                    "found": False,
                    "patient": None,
                },
            )

        return self._success(
            {
                "found": True,
                "patient": self._patient_to_result(patient),
            },
        )

    def list_upcoming_appointments(
        self,
        payload: RetellUpcomingAppointmentsRequest,
    ) -> RetellToolResponse:
        criteria = PatientLookupCriteria(
            full_name=payload.full_name,
            date_of_birth=payload.date_of_birth,
            phone_number=payload.phone_number,
            email=payload.email,
        )

        try:
            patient = self.service.lookup_patient(criteria)
        except InsufficientPatientIdentityError:
            return self._error(
                "insufficient_patient_identity",
                "Patient lookup requires phone number or email.",
            )

        if patient is None:
            return self._success(
                {
                    "patient_found": False,
                    "appointments": [],
                },
            )

        appointments = self.service.list_upcoming_appointments(
            criteria=criteria,
            start_from=payload.start_from,
        )

        return self._success(
            {
                "patient_found": True,
                "patient": self._patient_to_result(patient),
                "appointments": [
                    self._appointment_to_result(appointment)
                    for appointment in appointments
                ],
            },
        )

    def _resolve_specialty_id(
        self,
        specialty_name: str | None,
    ) -> tuple[UUID | None, RetellToolResponse | None]:
        if specialty_name is None:
            return None, None

        matches = [
            specialty
            for specialty in self.service.list_specialties()
            if self._matches_human_name(specialty.name, specialty_name)
        ]

        if not matches:
            return None, self._error(
                "specialty_not_found",
                "No matching specialty was found.",
            )

        if len(matches) > 1:
            return None, self._error(
                "ambiguous_specialty",
                "More than one matching specialty was found.",
            )

        return matches[0].id, None

    def _resolve_single_doctor(
        self,
        *,
        specialty_name: str | None,
        doctor_name: str | None,
    ) -> Doctor | RetellToolResponse:
        if doctor_name is None and specialty_name is None:
            return self._error(
                "missing_doctor",
                "A doctor name, doctor ID, or specialty name is required.",
            )

        list_payload = RetellListDoctorsRequest(
            specialty_name=specialty_name,
            doctor_name=doctor_name,
        )
        list_response = self.list_doctors(list_payload)

        if not list_response.ok:
            return list_response

        doctors = cast(dict[str, Any], list_response.result).get("doctors", [])

        if not doctors:
            return self._error(
                "doctor_not_found",
                "No matching doctor was found.",
            )

        if len(doctors) > 1:
            return self._error(
                "ambiguous_doctor",
                "More than one matching doctor was found.",
            )

        doctor_id = UUID(doctors[0]["id"])

        for doctor in self.service.list_doctors():
            if doctor.id == doctor_id:
                return doctor

        return self._error(
            "doctor_not_found",
            "No matching doctor was found.",
        )

    def _matches_human_name(self, candidate: str, query: str) -> bool:
        candidate_terms = self._normalize_name(candidate).split()
        query_terms = self._normalize_name(query).split()

        return all(term in candidate_terms for term in query_terms)

    def _normalize_name(self, value: str) -> str:
        return value.replace(".", "").strip().casefold()

    def _specialty_to_result(self, specialty: Specialty) -> dict[str, Any]:
        return SpecialtyResponse.model_validate(specialty).model_dump(mode="json")

    def _doctor_to_result(self, doctor: Doctor) -> dict[str, Any]:
        return DoctorResponse.model_validate(doctor).model_dump(mode="json")

    def _availability_slot_to_result(self, slot: AvailabilitySlot) -> dict[str, Any]:
        return AvailabilitySlotResponse.model_validate(slot).model_dump(mode="json")

    def _patient_to_result(self, patient: Patient) -> dict[str, Any]:
        return PatientResponse.model_validate(patient).model_dump(mode="json")

    def _appointment_to_result(self, appointment: Appointment) -> dict[str, Any]:
        return AppointmentResponse.model_validate(appointment).model_dump(mode="json")

    def _success(
        self,
        result: dict[str, Any] | list[dict[str, Any]],
    ) -> RetellToolResponse:
        return RetellToolResponse(
            ok=True,
            result=result,
        )

    def _error(self, error_code: str, message: str) -> RetellToolResponse:
        return RetellToolResponse(
            ok=False,
            error_code=error_code,
            message=message,
        )