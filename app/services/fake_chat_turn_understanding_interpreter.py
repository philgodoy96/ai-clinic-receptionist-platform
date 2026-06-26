from __future__ import annotations

import re
from datetime import datetime

from app.domain.chat_turn_understanding import (
    ChatTurnIntent,
    ChatTurnUnderstandingRequest,
    ChatTurnUnderstandingResult,
    ConfirmationDecision,
    ExpectedResponseType,
    ExtractedTurnFields,
    FieldIssue,
    KnownDoctor,
    KnownSpecialty,
    OfferedSlot,
    PatientStatusAnswer,
)

_MONTH_TOKEN_TO_NUMBER: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_CONFIRMATION_PHRASES = frozenset(
    {
        "yes",
        "yeah",
        "yep",
        "correct",
        "that's right",
        "thats right",
        "that is right",
        "that works for me",
        "sounds good",
    },
)

_TIME_WINDOW_KEYWORDS: dict[str, str] = {
    "morning": "morning",
    "afternoon": "afternoon",
    "evening": "evening",
}


class FakeChatTurnUnderstandingInterpreter:
    def interpret(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnUnderstandingResult:
        message = request.latest_user_message.strip()
        normalized = self._normalize(message)

        handlers = (
            self._try_change_request,
            self._try_confirmation,
            self._try_patient_status_and_identity,
            self._try_slot_selection,
            self._try_appointment_request,
            self._try_specialty_request,
            self._try_doctor_request,
        )

        for handler in handlers:
            result = handler(request, message, normalized)
            if result is not None:
                return self._enforce_allowed_intents(request, result)

        return self._fallback(message)

    def _try_change_request(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        if "change the time" not in normalized:
            return None

        return self._build_result(
            ChatTurnIntent.CHANGE_REQUEST,
            confidence=0.88,
            reason="User asked to change the appointment time.",
            confirmation_decision=ConfirmationDecision.WANTS_CHANGE,
        )

    def _try_confirmation(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        if normalized not in _CONFIRMATION_PHRASES:
            return None

        expected = request.expected_response_type

        if expected is ExpectedResponseType.PATIENT_STATUS:
            return self._build_result(
                ChatTurnIntent.PATIENT_STATUS_ANSWER,
                confidence=0.92,
                reason="User confirmed they are an existing patient.",
                patient_status_answer=PatientStatusAnswer.EXISTING_PATIENT,
            )

        if expected in {
            ExpectedResponseType.BOOKING_CONFIRMATION,
            ExpectedResponseType.EMAIL_CONFIRMATION,
        }:
            return self._build_result(
                ChatTurnIntent.CONFIRMATION,
                confidence=0.93,
                reason="User confirmed the pending booking or email detail.",
                confirmation_decision=ConfirmationDecision.CONFIRMED,
            )

        if expected is ExpectedResponseType.SLOT_SELECTION or (
            expected
            in {
                ExpectedResponseType.BOOKING_CONFIRMATION,
                ExpectedResponseType.EMAIL_CONFIRMATION,
            }
            and normalized == "that works for me"
        ):
            return self._handle_slot_confirmation(request, normalized)

        if expected in {
            ExpectedResponseType.BOOKING_CONFIRMATION,
            ExpectedResponseType.EMAIL_CONFIRMATION,
        } and normalized in {"that's right", "thats right", "that is right"}:
            return self._build_result(
                ChatTurnIntent.CONFIRMATION,
                confidence=0.93,
                reason="User confirmed with an affirmative phrase.",
                confirmation_decision=ConfirmationDecision.CONFIRMED,
            )

        return None

    def _handle_slot_confirmation(
        self,
        request: ChatTurnUnderstandingRequest,
        normalized: str,
    ) -> ChatTurnUnderstandingResult:
        offered_slots = request.offered_slots

        if len(offered_slots) == 1:
            return self._build_result(
                ChatTurnIntent.CONFIRMATION,
                confidence=0.9,
                reason="User confirmed the only offered slot.",
                confirmation_decision=ConfirmationDecision.CONFIRMED,
                selected_slot_reference=offered_slots[0].reference,
            )

        if len(offered_slots) > 1:
            return self._build_result(
                ChatTurnIntent.CONFIRMATION,
                confidence=0.75,
                reason="User confirmed but multiple slots are offered.",
                confirmation_decision=ConfirmationDecision.CONFIRMED,
                clarification_question="Which of the offered times would you like?",
                ambiguous_fields=[
                    FieldIssue(
                        field="selected_slot_reference",
                        reason="multiple_offered_slots",
                        candidates=[slot.reference for slot in offered_slots],
                        clarification_question="Which of the offered times would you like?",
                    ),
                ],
            )

        return self._build_result(
            ChatTurnIntent.CONFIRMATION,
            confidence=0.9,
            reason="User confirmed without slot context.",
            confirmation_decision=ConfirmationDecision.CONFIRMED,
        )

    def _try_patient_status_and_identity(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        expected = request.expected_response_type
        identity_expected = expected in {
            ExpectedResponseType.PATIENT_IDENTITY,
            ExpectedResponseType.PATIENT_STATUS,
        }

        new_patient_signal = any(
            phrase in normalized
            for phrase in ("i'm new", "im new", "i am new", "new patient")
        )
        has_identity_signal = self._has_identity_signal(message)

        if not identity_expected and not (new_patient_signal and has_identity_signal):
            if not has_identity_signal:
                return None
            if expected is not ExpectedResponseType.PATIENT_IDENTITY:
                structured_identity = self._parse_comma_separated_identity(message)
                if structured_identity is None and "my name is" not in normalized:
                    return None

        extracted = ExtractedTurnFields()
        ambiguous_fields: list[FieldIssue] = []
        patient_status_answer = PatientStatusAnswer.NOT_APPLICABLE
        reason = "Extracted patient identity fields from the user message."
        confidence = 0.9

        if new_patient_signal:
            patient_status_answer = PatientStatusAnswer.NEW_PATIENT
            reason = (
                "User indicated they are a new patient and provided identity details."
            )
            self._apply_new_patient_bundle(message, extracted)

        structured = self._parse_structured_identity(message)
        if structured is not None:
            extracted = self._merge_extracted_fields(extracted, structured.extracted)
            ambiguous_fields.extend(structured.ambiguous_fields)
        elif not new_patient_signal or extracted.patient_name is None:
            comma_identity = self._parse_comma_separated_identity(message)
            if comma_identity is not None:
                extracted = self._merge_extracted_fields(extracted, comma_identity.extracted)
                ambiguous_fields.extend(comma_identity.ambiguous_fields)

        email = self._extract_email(message)
        if email is not None:
            extracted.email = email

        if (
            extracted.patient_name is None
            and extracted.date_of_birth is None
            and extracted.date_of_birth_raw is None
            and patient_status_answer is PatientStatusAnswer.NOT_APPLICABLE
        ):
            return None

        intent = (
            ChatTurnIntent.PATIENT_IDENTITY_PROVIDED
            if extracted.patient_name is not None or extracted.date_of_birth is not None
            else ChatTurnIntent.PATIENT_STATUS_ANSWER
        )

        return self._build_result(
            intent,
            confidence=confidence,
            reason=reason,
            extracted_fields=extracted,
            ambiguous_fields=ambiguous_fields,
            patient_status_answer=patient_status_answer,
        )

    def _try_slot_selection(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        offered_slots = request.offered_slots

        if normalized in {"the first one", "first one", "the first"} and offered_slots:
            return self._build_result(
                ChatTurnIntent.SLOT_SELECTION,
                confidence=0.91,
                reason="User selected the first offered slot.",
                selected_slot_reference=offered_slots[0].reference,
            )

        time_match = re.search(r"\b(\d{1,2})\s*pm\b", normalized)
        if time_match is None:
            return None

        hour = int(time_match.group(1))
        if hour < 1 or hour > 12:
            return None

        normalized_time = f"{hour + 12:02d}:00"
        time_raw = time_match.group(0).strip()

        matching_slots = [
            slot for slot in offered_slots if self._slot_matches_time(slot, normalized_time)
        ]

        extracted = ExtractedTurnFields(
            appointment_time_raw=time_raw,
            appointment_time=normalized_time,
        )

        if len(matching_slots) == 1:
            return self._build_result(
                ChatTurnIntent.SLOT_SELECTION,
                confidence=0.9,
                reason="User selected a unique offered slot matching the requested time.",
                extracted_fields=extracted,
                selected_slot_reference=matching_slots[0].reference,
            )

        if len(matching_slots) > 1:
            return self._build_result(
                ChatTurnIntent.SLOT_SELECTION,
                confidence=0.72,
                reason="Multiple offered slots match the requested time.",
                extracted_fields=extracted,
                clarification_question="Which of the 2pm slots did you mean?",
                ambiguous_fields=[
                    FieldIssue(
                        field="selected_slot_reference",
                        source_text=time_raw,
                        reason="multiple_slot_time_matches",
                        candidates=[slot.reference for slot in matching_slots],
                        clarification_question="Which of the 2pm slots did you mean?",
                    ),
                ],
            )

        if not offered_slots:
            return self._build_result(
                ChatTurnIntent.SLOT_SELECTION,
                confidence=0.82,
                reason="Extracted appointment time without offered slots to select.",
                extracted_fields=extracted,
            )

        return None

    def _try_appointment_request(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        if "dermatology" not in normalized and "next thursday" not in normalized:
            return None

        extracted = ExtractedTurnFields()
        if "dermatology" in normalized:
            extracted.specialty_raw = "dermatology"
            resolved = self._resolve_specialty("dermatology", request.known_specialties)
            if resolved is not None:
                extracted.specialty = resolved

        if "next thursday" in normalized:
            extracted.appointment_date_raw = "next Thursday"

        for keyword, window in _TIME_WINDOW_KEYWORDS.items():
            if keyword in normalized:
                extracted.appointment_time_window_raw = keyword
                extracted.appointment_time_window = window
                break

        intent = self._preferred_appointment_intent(request)
        return self._build_result(
            intent,
            confidence=0.86,
            reason="User requested an appointment with scheduling preferences.",
            extracted_fields=extracted,
        )

    def _try_specialty_request(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        match = re.search(r"\bi need\s+([a-z][a-z\s-]*)", normalized)
        if match is None:
            return None

        specialty_raw = match.group(1).strip()
        if not specialty_raw:
            return None

        resolved = self._resolve_specialty(specialty_raw, request.known_specialties)
        extracted = ExtractedTurnFields(
            specialty_raw=specialty_raw,
            specialty=resolved,
        )
        intent = self._preferred_appointment_intent(request)
        return self._build_result(
            intent,
            confidence=0.84,
            reason="User requested a specialty.",
            extracted_fields=extracted,
        )

    def _try_doctor_request(
        self,
        request: ChatTurnUnderstandingRequest,
        message: str,
        normalized: str,
    ) -> ChatTurnUnderstandingResult | None:
        if not request.known_doctors:
            return None

        matches = self._find_matching_doctors(message, request.known_doctors)
        if not matches:
            return None

        if len(matches) == 1:
            doctor = matches[0]
            return self._build_result(
                self._preferred_appointment_intent(request),
                confidence=0.85,
                reason="User referenced a uniquely matching doctor.",
                extracted_fields=ExtractedTurnFields(
                    doctor_name=doctor.full_name,
                    doctor_name_raw=self._doctor_match_token(message, doctor),
                ),
            )

        return self._build_result(
            self._preferred_appointment_intent(request),
            confidence=0.7,
            reason="User referenced a doctor name that matches multiple doctors.",
            extracted_fields=ExtractedTurnFields(
                doctor_name_raw=self._shared_doctor_match_token(message, matches),
            ),
            clarification_question="Which doctor did you mean?",
            ambiguous_fields=[
                FieldIssue(
                    field="doctor_name",
                    source_text=self._shared_doctor_match_token(message, matches),
                    reason="multiple_doctor_matches",
                    candidates=[
                        {"id": doctor.id, "full_name": doctor.full_name} for doctor in matches
                    ],
                    clarification_question="Which doctor did you mean?",
                ),
            ],
        )

    def _enforce_allowed_intents(
        self,
        request: ChatTurnUnderstandingRequest,
        result: ChatTurnUnderstandingResult,
    ) -> ChatTurnUnderstandingResult:
        if result.intent in request.allowed_intents:
            return result

        return ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.35,
            reason=(
                f"Detected intent {result.intent.value} is not allowed; "
                "returning fallback."
            ),
            clarification_question="Could you please clarify what you'd like to do?",
        )

    def _fallback(self, message: str) -> ChatTurnUnderstandingResult:
        return ChatTurnUnderstandingResult(
            intent=ChatTurnIntent.FALLBACK,
            confidence=0.3,
            reason=f"No deterministic scenario matched message: {message!r}",
            clarification_question=(
                "I didn't quite understand that. Could you clarify what you'd like to do?"
            ),
        )

    def _build_result(
        self,
        intent: ChatTurnIntent,
        *,
        confidence: float,
        reason: str,
        confirmation_decision: ConfirmationDecision = ConfirmationDecision.NOT_APPLICABLE,
        patient_status_answer: PatientStatusAnswer = PatientStatusAnswer.NOT_APPLICABLE,
        extracted_fields: ExtractedTurnFields | None = None,
        ambiguous_fields: list[FieldIssue] | None = None,
        selected_slot_reference: str | None = None,
        clarification_question: str | None = None,
    ) -> ChatTurnUnderstandingResult:
        return ChatTurnUnderstandingResult(
            intent=intent,
            confidence=confidence,
            reason=reason,
            confirmation_decision=confirmation_decision,
            patient_status_answer=patient_status_answer,
            extracted_fields=extracted_fields or ExtractedTurnFields(),
            ambiguous_fields=ambiguous_fields or [],
            selected_slot_reference=selected_slot_reference,
            clarification_question=clarification_question,
        )

    def _preferred_appointment_intent(
        self,
        request: ChatTurnUnderstandingRequest,
    ) -> ChatTurnIntent:
        if ChatTurnIntent.APPOINTMENT_REQUEST in request.allowed_intents:
            return ChatTurnIntent.APPOINTMENT_REQUEST
        if ChatTurnIntent.AVAILABILITY_REQUEST in request.allowed_intents:
            return ChatTurnIntent.AVAILABILITY_REQUEST
        return ChatTurnIntent.APPOINTMENT_REQUEST

    def _normalize(self, message: str) -> str:
        return re.sub(r"\s+", " ", message.lower().strip())

    def _has_identity_signal(self, message: str) -> bool:
        if self._extract_email(message) is not None:
            return True
        if re.search(r"\bmy name is\b", message, re.IGNORECASE):
            return True
        if re.search(r"\b\d{4}-\d{2}-\d{2}\b", message):
            return True
        if re.search(
            r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b",
            message,
            re.IGNORECASE,
        ):
            return True
        if re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", message):
            return True
        if re.search(r"\bborn\b", message, re.IGNORECASE):
            return True
        return self._parse_comma_separated_identity(message) is not None

    def _apply_new_patient_bundle(
        self,
        message: str,
        extracted: ExtractedTurnFields,
    ) -> None:
        name_match = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", message)
        if name_match is not None:
            extracted.patient_name = name_match.group(1)

        born_match = re.search(
            r"\bborn\s+(.+?)(?:,|\s+email\b|$)",
            message,
            re.IGNORECASE,
        )
        dob_source = born_match.group(1).strip() if born_match is not None else message
        natural = self._parse_natural_date(dob_source)
        if natural is not None:
            extracted.date_of_birth_raw = natural.raw
            extracted.date_of_birth = natural.normalized

    def _merge_extracted_fields(
        self,
        base: ExtractedTurnFields,
        incoming: ExtractedTurnFields,
    ) -> ExtractedTurnFields:
        merged = base.model_dump()
        for field_name, value in incoming.model_dump().items():
            if value is not None:
                merged[field_name] = value
        return ExtractedTurnFields(**merged)

    def _parse_structured_identity(self, message: str) -> _IdentityParseResult | None:
        match = re.search(
            r"\bmy name is\s+(.+?)\s+and my dob is\s+(\d{4}-\d{2}-\d{2})\b",
            message,
            re.IGNORECASE,
        )
        if match is None:
            return None

        return _IdentityParseResult(
            extracted=ExtractedTurnFields(
                patient_name=match.group(1).strip(),
                date_of_birth=match.group(2),
                date_of_birth_raw=match.group(2),
            ),
        )

    def _parse_comma_separated_identity(self, message: str) -> _IdentityParseResult | None:
        if "," not in message:
            return None

        name_part, remainder = (part.strip() for part in message.split(",", maxsplit=1))
        if len(name_part.split()) < 2:
            return None

        dob_raw = remainder.strip()
        if dob_raw.lower().startswith("born "):
            dob_raw = dob_raw[5:].strip()

        extracted = ExtractedTurnFields(patient_name=name_part)
        ambiguous_fields: list[FieldIssue] = []

        iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", dob_raw)
        if iso_match is not None:
            extracted.date_of_birth = iso_match.group(1)
            extracted.date_of_birth_raw = iso_match.group(1)
            return _IdentityParseResult(extracted=extracted)

        numeric = self._parse_numeric_date(dob_raw)
        if numeric is not None:
            extracted.date_of_birth_raw = numeric.raw
            if numeric.is_ambiguous:
                ambiguous_fields.append(
                    FieldIssue(
                        field="date_of_birth",
                        source_text=numeric.raw,
                        reason="ambiguous_numeric_date_format",
                        candidates=numeric.candidates,
                        clarification_question=(
                            "Did you mean September 10, 1996 or October 9, 1996?"
                        ),
                    ),
                )
            else:
                extracted.date_of_birth = numeric.normalized
            return _IdentityParseResult(
                extracted=extracted,
                ambiguous_fields=ambiguous_fields,
            )

        natural = self._parse_natural_date(dob_raw)
        if natural is not None:
            extracted.date_of_birth_raw = natural.raw
            extracted.date_of_birth = natural.normalized
            return _IdentityParseResult(extracted=extracted)

        if dob_raw:
            extracted.date_of_birth_raw = dob_raw
            natural_from_remainder = self._parse_natural_date(dob_raw)
            if natural_from_remainder is not None:
                extracted.date_of_birth = natural_from_remainder.normalized

        return _IdentityParseResult(extracted=extracted, ambiguous_fields=ambiguous_fields)

    def _parse_numeric_date(self, raw: str) -> _NumericDateParse | None:
        match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", raw)
        if match is None:
            return None

        first = int(match.group(1))
        second = int(match.group(2))
        year = int(match.group(3))
        token = match.group(0)

        if first > 12 and second <= 12:
            return _NumericDateParse(
                raw=token,
                normalized=f"{year}-{second:02d}-{first:02d}",
            )

        if second > 12 and first <= 12:
            return _NumericDateParse(
                raw=token,
                normalized=f"{year}-{first:02d}-{second:02d}",
            )

        if first <= 12 and second <= 12:
            return _NumericDateParse(
                raw=token,
                is_ambiguous=True,
                candidates=[
                    f"{year}-{first:02d}-{second:02d}",
                    f"{year}-{second:02d}-{first:02d}",
                ],
            )

        return None

    def _parse_natural_date(self, raw: str) -> _NaturalDateParse | None:
        match = re.search(
            r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
            r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
            r"nov(?:ember)?|dec(?:ember)?)\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})\b",
            raw,
            re.IGNORECASE,
        )
        if match is None:
            return None

        month_token = match.group(1).lower()[:3]
        month = _MONTH_TOKEN_TO_NUMBER[month_token]
        day = int(match.group(2))
        year = int(match.group(3))
        token = match.group(0)
        return _NaturalDateParse(
            raw=token,
            normalized=f"{year}-{month:02d}-{day:02d}",
        )

    def _extract_email(self, message: str) -> str | None:
        match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", message)
        return match.group(0) if match else None

    def _resolve_specialty(
        self,
        raw: str,
        known_specialties: list[KnownSpecialty],
    ) -> str | None:
        raw_lower = raw.lower().strip()
        for specialty in known_specialties:
            if specialty.name.lower() == raw_lower:
                return specialty.name
            if any(alias.lower() == raw_lower for alias in specialty.aliases):
                return specialty.name
        return None

    def _find_matching_doctors(
        self,
        message: str,
        known_doctors: list[KnownDoctor],
    ) -> list[KnownDoctor]:
        normalized_message = self._normalize(message)
        matches: list[KnownDoctor] = []

        for doctor in known_doctors:
            tokens = self._doctor_tokens(doctor)
            if any(token in normalized_message for token in tokens if len(token) >= 3):
                matches.append(doctor)

        return matches

    def _doctor_tokens(self, doctor: KnownDoctor) -> list[str]:
        tokens = [doctor.full_name.lower()]
        tokens.extend(alias.lower() for alias in doctor.aliases)
        name_parts = re.sub(r"^dr\.?\s+", "", doctor.full_name.lower()).split()
        tokens.extend(name_parts)
        return tokens

    def _doctor_match_token(self, message: str, doctor: KnownDoctor) -> str | None:
        normalized_message = self._normalize(message)
        for token in self._doctor_tokens(doctor):
            if len(token) >= 3 and token in normalized_message:
                return token
        return None

    def _shared_doctor_match_token(
        self,
        message: str,
        doctors: list[KnownDoctor],
    ) -> str | None:
        for doctor in doctors:
            token = self._doctor_match_token(message, doctor)
            if token is not None:
                return token
        return None

    def _slot_matches_time(self, slot: OfferedSlot, normalized_time: str) -> bool:
        try:
            parsed = datetime.fromisoformat(slot.start_time)
        except ValueError:
            return False
        return parsed.strftime("%H:%M") == normalized_time


class _IdentityParseResult:
    def __init__(
        self,
        *,
        extracted: ExtractedTurnFields,
        ambiguous_fields: list[FieldIssue] | None = None,
    ) -> None:
        self.extracted = extracted
        self.ambiguous_fields = ambiguous_fields or []


class _NumericDateParse:
    def __init__(
        self,
        *,
        raw: str,
        normalized: str | None = None,
        is_ambiguous: bool = False,
        candidates: list[str] | None = None,
    ) -> None:
        self.raw = raw
        self.normalized = normalized
        self.is_ambiguous = is_ambiguous
        self.candidates = candidates or []


class _NaturalDateParse:
    def __init__(self, *, raw: str, normalized: str) -> None:
        self.raw = raw
        self.normalized = normalized
