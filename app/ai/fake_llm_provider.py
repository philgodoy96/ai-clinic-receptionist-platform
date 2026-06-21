from __future__ import annotations

import json
import re

from app.ai.llm_provider import LLMProvider, LLMRequest, LLMResponse


class FakeLLMProvider:
    model_name = "fake-receptionist-llm"

    def complete(self, request: LLMRequest) -> LLMResponse:
        user_message = self._last_user_message(request)
        analysis = self._analyze(user_message)
        content = json.dumps(analysis, separators=(",", ":"))

        return LLMResponse(
            content=content,
            model=self.model_name,
            input_tokens=self._estimate_input_tokens(request),
            output_tokens=max(1, len(content.split())),
            estimated_cost_micros=0,
        )

    def _last_user_message(self, request: LLMRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user":
                return message.content

        return ""

    def _analyze(self, message: str) -> dict[str, object]:
        normalized_message = message.lower()
        extracted = self._extract_fields(message)

        if self._contains_any(
            normalized_message,
            ["emergency", "urgent", "chest pain", "can't breathe", "cannot breathe"],
        ):
            return self._analysis(
                intent="emergency",
                confidence=0.99,
                urgency="emergency",
                extracted=extracted,
                safety_flags=["medical_emergency"],
            )

        if self._contains_any(
            normalized_message,
            ["human", "person", "receptionist", "representative", "someone"],
        ) and self._contains_any(
            normalized_message,
            ["talk", "speak", "transfer", "connect", "helping"],
        ):
            return self._analysis(
                intent="human_escalation_request",
                confidence=0.9,
                urgency="normal",
                extracted=extracted,
                requires_human=True,
            )

        if self._contains_any(normalized_message, ["cancel", "cancellation"]):
            return self._analysis(
                intent="cancel_request",
                confidence=0.85,
                urgency="normal",
                extracted=extracted,
            )

        if self._contains_any(normalized_message, ["reschedule", "move appointment"]):
            return self._analysis(
                intent="reschedule_request",
                confidence=0.85,
                urgency="normal",
                extracted=extracted,
            )

        if self._contains_any(
            normalized_message,
            ["confirm", "book it", "schedule it", "go ahead"],
        ):
            return self._analysis(
                intent="booking_confirmation",
                confidence=0.9,
                urgency="normal",
                extracted=extracted,
            )

        if self._contains_any(
            normalized_message,
            ["hold", "reserve", "i'll take", "i will take", "that works"],
        ):
            return self._analysis(
                intent="hold_request",
                confidence=0.84,
                urgency="normal",
                extracted=extracted,
            )

        if self._contains_any(
            normalized_message,
            ["available", "availability", "openings", "times", "slots"],
        ):
            return self._analysis(
                intent="availability_request",
                confidence=0.86,
                urgency="normal",
                extracted=extracted,
            )

        if self._contains_any(
            normalized_message,
            ["appointment", "schedule", "book", "doctor", "dermatology", "cardiology"],
        ):
            return self._analysis(
                intent="appointment_request",
                confidence=0.82,
                urgency="normal",
                extracted=extracted,
            )

        if self._contains_any(normalized_message, ["hello", "hi", "hey"]):
            return self._analysis(
                intent="greeting",
                confidence=0.75,
                urgency="normal",
                extracted=extracted,
            )

        return self._analysis(
            intent="fallback",
            confidence=0.4,
            urgency="normal",
            extracted=extracted,
        )

    def _analysis(
        self,
        *,
        intent: str,
        confidence: float,
        urgency: str,
        extracted: dict[str, object],
        requires_human: bool = False,
        safety_flags: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "intent": intent,
            "confidence": confidence,
            "urgency": urgency,
            "extracted": extracted,
            "requires_human": requires_human,
            "safety_flags": safety_flags or [],
        }

    def _extract_fields(self, message: str) -> dict[str, object]:
        return {
            "specialty": self._extract_specialty(message),
            "doctor_name": self._extract_doctor_name(message),
            "date": self._extract_date(message),
            "time": self._extract_time(message),
            "patient_identity": {
                "full_name": self._extract_name(message),
                "date_of_birth": self._extract_date(message),
                "phone": self._extract_phone(message),
                "email": self._extract_email(message),
            },
        }

    def _extract_specialty(self, message: str) -> str | None:
        normalized_message = message.lower()

        if "dermatology" in normalized_message or "dermatologist" in normalized_message:
            return "Dermatology"

        if "cardiology" in normalized_message or "cardiologist" in normalized_message:
            return "Cardiology"

        if "primary care" in normalized_message:
            return "Primary Care"

        return None

    def _extract_doctor_name(self, message: str) -> str | None:
        match = re.search(r"Dr\.\s+[A-Z][A-Za-z]+\s+[A-Z][A-Za-z]+", message)

        return match.group(0) if match else None

    def _extract_date(self, message: str) -> str | None:
        match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", message)

        return match.group(0) if match else None

    def _extract_time(self, message: str) -> str | None:
        match = re.search(r"\b\d{1,2}:\d{2}\b", message)

        return match.group(0) if match else None

    def _extract_email(self, message: str) -> str | None:
        match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", message)

        return match.group(0) if match else None

    def _extract_phone(self, message: str) -> str | None:
        candidates: list[str] = re.findall(r"\+?[\d][\d\s().-]{5,}\d", message)

        for candidate in candidates:
            normalized_candidate = candidate.strip()

            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized_candidate):
                continue

            digits = re.sub(r"\D", "", normalized_candidate)

            if 7 <= len(digits) <= 15:
                return normalized_candidate

        return None

    def _extract_name(self, message: str) -> str | None:
        match = re.search(
            r"my name is\s+(.+?)(?:,|\s+DOB|\s+phone|\s+email|\.|$)",
            message,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

        first_segment = message.split(",", maxsplit=1)[0].strip()
        if len(first_segment.split()) >= 2 and not first_segment.lower().startswith("dr."):
            return first_segment

        return None

    def _contains_any(self, value: str, options: list[str]) -> bool:
        return any(option in value for option in options)

    def _estimate_input_tokens(self, request: LLMRequest) -> int:
        return max(1, sum(len(message.content.split()) for message in request.messages))


def build_fake_llm_provider() -> LLMProvider:
    return FakeLLMProvider()