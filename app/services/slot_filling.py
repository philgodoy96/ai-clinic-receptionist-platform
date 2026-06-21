from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from app.ai.receptionist_output import ReceptionistLLMAnalysis
from app.models.scheduling import Doctor, Specialty
from app.services.scheduling import SchedulingService

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")


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

    def to_metadata(self) -> dict[str, object]:
        return {
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


ApplyResult = Literal["applied", "unchanged", "conflict"]


class LLMChatSlotFillingService:
    def __init__(self, *, scheduling: SchedulingService) -> None:
        self.scheduling = scheduling

    def apply_analysis(
        self,
        *,
        analysis: ReceptionistLLMAnalysis,
        chat_context: dict[str, Any],
    ) -> SlotFillingResult:
        updated_context = deepcopy(chat_context)
        applied_fields: list[SlotFillingAppliedField] = []
        rejected_fields: list[SlotFillingRejectedField] = []
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
            self._apply_date(
                updated_context,
                raw_date=extracted.date.strip(),
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        if extracted.time and extracted.time.strip():
            self._apply_time(
                updated_context,
                raw_time=extracted.time.strip(),
                applied_fields=applied_fields,
                rejected_fields=rejected_fields,
            )

        return SlotFillingResult(
            updated_chat_context=updated_context,
            applied_fields=applied_fields,
            rejected_fields=rejected_fields,
            used_llm_analysis=True,
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
    ) -> None:
        parsed_date = self._validate_iso_date(raw_date)
        if parsed_date is None:
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="date",
                    value=raw_date,
                    reason="invalid_date",
                ),
            )
            return

        iso_date = parsed_date.isoformat()
        result = self._apply_if_empty_or_same(context, "requested_date", iso_date)
        if result == "conflict":
            rejected_fields.append(
                SlotFillingRejectedField(
                    field="date",
                    value=raw_date,
                    reason="conflicts_with_existing_context",
                ),
            )
            return

        if result == "applied":
            applied_fields.append(
                SlotFillingAppliedField(field="date", value=raw_date),
            )

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
