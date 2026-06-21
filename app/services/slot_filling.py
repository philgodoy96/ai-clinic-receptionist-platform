from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from app.ai.receptionist_output import ExtractedPatientIdentity, ReceptionistLLMAnalysis
from app.domain.scheduling.phone import normalize_phone_digits
from app.models.scheduling import Doctor, Specialty
from app.services.date_parsing import (
    DateParseResult,
    DateParseStatus,
    NaturalLanguageDateParser,
)
from app.services.scheduling import SchedulingService

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True, slots=True)
class SlotFillingAppliedField:
    field: str
    value: object


@dataclass(frozen=True, slots=True)
class SlotFillingRejectedField:
    field: str
    value: object
    reason: str


@dataclass(frozen=True, slots=True)
class SlotFillingResult:
    updated_chat_context: dict[str, Any]
    applied_fields: list[SlotFillingAppliedField] = field(default_factory=list)
    rejected_fields: list[SlotFillingRejectedField] = field(default_factory=list)
    used_llm_analysis: bool = False
    date_parsing: dict[str, object] | None = None

    def to_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {
            "used_llm_analysis": self.used_llm_analysis,
            "applied_fields": [
                {
                    "field": field.field,
                    "value": field.value,
                }
                for field in self.applied_fields
            ],
            "rejected_fields": [
                {
                    "field": field.field,
                    "value": field.value,
                    "reason": field.reason,
                }
                for field in self.rejected_fields
            ],
        }
        if self.date_parsing is not None:
            metadata["date_parsing"] = self.date_parsing
        return metadata


ApplyResult = Literal["applied", "unchanged", "conflict"]


class LLMChatSlotFillingService:
    def __init__(
        self,
        *,
        scheduling: SchedulingService,
        date_parser: NaturalLanguageDateParser,
    ) -> None:
        self.scheduling = scheduling
        self.date_parser = date_parser

    def apply_analysis(
        self,
        *,
        analysis: ReceptionistLLMAnalysis,
        chat_context: dict[str, Any],
    ) -> SlotFillingResult:
        updated_context = deepcopy(chat_context)
        applied_fields: list[SlotFillingAppliedField] = []
        rejected_fields: list[SlotFillingRejectedField] = []
        date_parsing: dict[str, object] | None = None
        extracted = analysis.extracted

        if extracted.specialty and extracted.specialty.strip():
            self._apply_specialty(
                updated_context,
                specialty_name=extracted.specialty.strip(),
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        if extracted.doctor_name and extracted.doctor_name.strip():
            self._apply_doctor(
                updated_context,
                doctor_name=extracted.doctor_name.strip(),
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        if extracted.date and extracted.date.strip():
            parse_result = self._apply_date(
                updated_context,
                raw_date=extracted.date.strip(),
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )
            date_parsing = parse_result.to_metadata()

        if extracted.time and extracted.time.strip():
            self._apply_time(
                updated_context,
                raw_time=extracted.time.strip(),
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        self._apply_patient_identity(
            updated_context,
            patient_identity=extracted.patient_identity,
            applied_fields=applied_fields,
            rejected_fields=rejected_fields,
        )

        return SlotFillingResult(
            updated_chat_context=updated_context,
            applied_fields=applied_fields,
            rejected_fields=rejected_fields,
            used_llm_analysis=True,
            date_parsing=date_parsing,
        )

    def _apply_specialty(
        self,
        context: dict[str, Any],
        *,
        specialty_name: str,
        applied_fields: list[SlotFillingAppliedField],
        rejected_fields: list[SlotFillingRejectedField],
    ) -> None:
        matched_specialty = self._match_specialty(specialty_name)
        if matched_specialty is None:
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="specialty",
                    value=specialty_name,
                    reason="unknown_specialty",
                ),
            )
            return

        specialty_id = str(matched_specialty.id)
        id_result = self._apply_if_empty_or_same(
            context,
            "selected_specialty_id",
            specialty_id,
        )
        name_result = self._apply_if_empty_or_same(
            context,
            "selected_specialty_name",
            matched_specialty.name,
        )

        if "conflict" in (id_result, name_result):
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="specialty",
                    value=specialty_name,
                    reason="conflicts_with_existing_context",
                ),
            )
            return

        if id_result == "applied" or name_result == "applied":
            applied_fields.append(
                SlotFillingAppliedField(field="specialty", value=specialty_name),
            )

    def _apply_doctor(
        self,
        context: dict[str, Any],
        *,
        doctor_name: str,
        applied_fields: list[SlotFillingAppliedField],
        rejected_fields: list[SlotFillingRejectedField],
    ) -> None:
        matched_doctor = self._match_doctor(doctor_name)
        if matched_doctor is None:
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="doctor_name",
                    value=doctor_name,
                    reason="unknown_doctor",
                ),
            )
            return

        doctor_id = str(matched_doctor.id)
        id_result = self._apply_if_empty_or_same(
            context,
            "selected_doctor_id",
            doctor_id,
        )
        name_result = self._apply_if_empty_or_same(
            context,
            "selected_doctor_name",
            matched_doctor.full_name,
        )

        if "conflict" in (id_result, name_result):
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="doctor_name",
                    value=doctor_name,
                    reason="conflicts_with_existing_context",
                ),
            )
            return

        if id_result == "applied" or name_result == "applied":
            applied_fields.append(
                SlotFillingAppliedField(field="doctor_name", value=doctor_name),
            )
            self._apply_doctor_specialty_if_empty(context, matched_doctor)

    def _apply_doctor_specialty_if_empty(
        self,
        context: dict[str, Any],
        doctor: Doctor,
    ) -> None:
        if context.get("selected_specialty_id"):
            return

        specialty = self._find_specialty_by_id(doctor.specialty_id)
        if specialty is None:
            return

        context["selected_specialty_id"] = str(specialty.id)
        context["selected_specialty_name"] = specialty.name

    def _apply_date(
        self,
        context: dict[str, Any],
        *,
        raw_date: str,
        applied_fields: list[SlotFillingAppliedField],
        rejected_fields: list[SlotFillingRejectedField],
    ) -> DateParseResult:
        parse_result = self.date_parser.parse(raw_date)

        if parse_result.status == DateParseStatus.PARSED:
            normalized_date = parse_result.normalized_date
            if normalized_date is None:
                rejected_fields.append(
                    SlotFillingRejectedField(
                        field="date",
                        value=raw_date,
                        reason="invalid_date",
                    ),
                )
                return parse_result

            result = self._apply_if_empty_or_same(
                context,
                "requested_date",
                normalized_date,
            )
            if result == "conflict":
                rejected_fields.append(
                    SlotFillingRejectedField(
                        field="date",
                        value=raw_date,
                        reason="conflicts_with_existing_context",
                    ),
                )
                return parse_result

            if result == "applied":
                applied_fields.append(
                    SlotFillingAppliedField(field="date", value=normalized_date),
                )
            return parse_result

        rejection_reason_by_status = {
            DateParseStatus.INVALID: "invalid_date",
            DateParseStatus.AMBIGUOUS: "ambiguous_date",
            DateParseStatus.UNSUPPORTED: "unsupported_date_expression",
            DateParseStatus.NOT_FOUND: "date_not_found",
        }
        rejected_fields.append(
            SlotFillingRejectedField(
                field="date",
                value=raw_date,
                reason=rejection_reason_by_status[parse_result.status],
            ),
        )
        return parse_result

    def _apply_time(
        self,
        context: dict[str, Any],
        *,
        raw_time: str,
        applied_fields: list[SlotFillingAppliedField],
        rejected_fields: list[SlotFillingRejectedField],
    ) -> None:
        normalized_time = self._validate_time(raw_time)
        if normalized_time is None:
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="time",
                    value=raw_time,
                    reason="invalid_time",
                ),
            )
            return

        result = self._apply_if_empty_or_same(
            context,
            "requested_time",
            normalized_time,
        )
        if result == "conflict":
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="time",
                    value=raw_time,
                    reason="conflicts_with_existing_context",
                ),
            )
            return

        if result == "applied":
            applied_fields.append(
                SlotFillingAppliedField(field="time", value=raw_time),
            )

    def _apply_patient_identity(
        self,
        context: dict[str, Any],
        *,
        patient_identity: ExtractedPatientIdentity,
        applied_fields: list[SlotFillingAppliedField],
        rejected_fields: list[SlotFillingRejectedField],
    ) -> None:
        if patient_identity.full_name and patient_identity.full_name.strip():
            self._apply_patient_identity_field(
                context,
                field_name="full_name",
                raw_value=patient_identity.full_name.strip(),
                validated_value=self._validate_full_name(patient_identity.full_name),
                invalid_reason="invalid_full_name",
                values_equal=self._full_names_equal,
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        if patient_identity.date_of_birth and patient_identity.date_of_birth.strip():
            raw_dob = patient_identity.date_of_birth.strip()
            parsed_dob = self._validate_iso_date(raw_dob)
            self._apply_patient_identity_field(
                context,
                field_name="date_of_birth",
                raw_value=raw_dob,
                validated_value=parsed_dob.isoformat() if parsed_dob is not None else None,
                invalid_reason="invalid_date_of_birth",
                values_equal=lambda existing, value: existing == value,
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        if patient_identity.phone and patient_identity.phone.strip():
            raw_phone = patient_identity.phone.strip()
            self._apply_patient_identity_field(
                context,
                field_name="phone",
                raw_value=raw_phone,
                validated_value=self._validate_phone(raw_phone),
                invalid_reason="invalid_phone",
                values_equal=self._phones_equal,
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        if patient_identity.email and patient_identity.email.strip():
            raw_email = patient_identity.email.strip()
            self._apply_patient_identity_field(
                context,
                field_name="email",
                raw_value=raw_email,
                validated_value=self._validate_email(raw_email),
                invalid_reason="invalid_email",
                values_equal=self._emails_equal,
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

    def _apply_patient_identity_field(
        self,
        context: dict[str, Any],
        *,
        field_name: str,
        raw_value: str,
        validated_value: str | None,
        invalid_reason: str,
        values_equal: Callable[[str, str], bool],
        applied_fields: list[SlotFillingAppliedField],
        rejected_fields: list[SlotFillingRejectedField],
    ) -> None:
        applied_field_name = f"patient_identity.{field_name}"

        if validated_value is None:
            rejected_fields.append(
                SlotFillingRejectedField(
                    field=applied_field_name,
                    value=raw_value,
                    reason=invalid_reason,
                ),
            )
            return

        identity = context.get("patient_identity")
        if not isinstance(identity, dict):
            identity = {}
            context["patient_identity"] = identity

        existing = identity.get(field_name)
        if existing is None or existing == "":
            identity[field_name] = validated_value
            applied_fields.append(
                SlotFillingAppliedField(field=applied_field_name, value=raw_value),
            )
            return

        if values_equal(str(existing), validated_value):
            return

        rejected_fields.append(
            SlotFillingRejectedField(
                field=applied_field_name,
                value=raw_value,
                reason="conflicts_with_existing_context",
            ),
        )

    def _validate_full_name(self, value: str) -> str | None:
        stripped = value.strip()
        if not stripped:
            return None

        if len(stripped.split()) < 2:
            return None

        return stripped

    def _validate_phone(self, value: str) -> str | None:
        stripped = value.strip()
        if not stripped:
            return None

        digits = normalize_phone_digits(stripped)
        if not 7 <= len(digits) <= 15:
            return None

        return stripped

    def _validate_email(self, value: str) -> str | None:
        stripped = value.strip()
        if not stripped:
            return None

        if not _EMAIL_PATTERN.fullmatch(stripped):
            return None

        return stripped

    def _full_names_equal(self, existing: str, value: str) -> bool:
        return self._normalize_text(existing) == self._normalize_text(value)

    def _phones_equal(self, existing: str, value: str) -> bool:
        return normalize_phone_digits(existing) == normalize_phone_digits(value)

    def _emails_equal(self, existing: str, value: str) -> bool:
        return existing.lower() == value.lower()

    def _apply_if_empty_or_same(
        self,
        context: dict[str, Any],
        key: str,
        value: str,
    ) -> ApplyResult:
        existing = context.get(key)
        if existing is None or existing == "":
            context[key] = value
            return "applied"

        existing_text = str(existing)
        if existing_text == value:
            return "unchanged"

        if self._normalize_text(existing_text) == self._normalize_text(value):
            return "unchanged"

        return "conflict"

    def _normalize_text(self, text: str) -> str:
        return text.replace(".", "").strip().lower()

    def _validate_iso_date(self, value: str) -> date | None:
        if not _ISO_DATE_PATTERN.fullmatch(value):
            return None

        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def _validate_time(self, value: str) -> str | None:
        match = _TIME_PATTERN.fullmatch(value)
        if match is None:
            return None

        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            return None

        return f"{hour:02d}:{minute:02d}"

    def _match_specialty(self, specialty_name: str) -> Specialty | None:
        normalized_input = self._normalize_text(specialty_name)

        for specialty in self.scheduling.list_specialties():
            if self._normalize_text(specialty.name) == normalized_input:
                return specialty

            for term in self._specialty_match_terms(specialty):
                if self._normalize_text(term) == normalized_input:
                    return specialty

        return None

    def _specialty_match_terms(self, specialty: Specialty) -> list[str]:
        name = specialty.name.lower()
        terms = [name]

        if name.endswith("ology"):
            terms.append(f"{name.removesuffix('ology')}ologist")

        return terms

    def _match_doctor(self, doctor_name: str) -> Doctor | None:
        normalized_input = self._normalize_text(doctor_name)
        doctors = sorted(
            self.scheduling.list_doctors(),
            key=lambda doctor: len(doctor.full_name),
            reverse=True,
        )

        for doctor in doctors:
            if self._doctor_name_matches(normalized_input, doctor.full_name):
                return doctor

        return None

    def _doctor_name_matches(self, normalized_input: str, full_name: str) -> bool:
        normalized_name = self._normalize_text(full_name)

        if normalized_name == normalized_input:
            return True

        if normalized_name in normalized_input or normalized_input in normalized_name:
            return True

        name_terms = normalized_name.split()
        return all(term in normalized_input for term in name_terms)

    def _find_specialty_by_id(self, specialty_id: UUID) -> Specialty | None:
        for specialty in self.scheduling.list_specialties():
            if specialty.id == specialty_id:
                return specialty

        return None
