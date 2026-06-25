from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from time import perf_counter

from app.ai.llm_provider import (
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMProviderName,
    LLMRequest,
    LLMResponse,
    provider_failure_reason,
)
from app.ai.llm_reliability import (
    MIN_ACCEPTED_CONFIDENCE,
    LLMFailureCategory,
    LLMFailureReason,
    LLMOutputSafetyViolation,
    failure_category_for_reason,
    is_fallback_provider_eligible,
    is_primary_provider_retryable,
    is_structural_output_failure,
    parse_failure_reason_from_parse_error,
)
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.ai.receptionist_output import (
    ReceptionistLLMAnalysis,
    ReceptionistLLMIntent,
    ReceptionistUrgency,
    fallback_receptionist_analysis,
)
from app.ai.receptionist_prompt import build_receptionist_system_prompt
from app.ai.structured_output import (
    StructuredOutputParseError,
    StructuredOutputValidationError,
    parse_structured_output_with_repair_flag,
)
from app.core.config import Settings

logger = logging.getLogger("app.llm_receptionist")

_CONTEXT_SNAPSHOT_PREFIX = (
    "Backend-provided conversation context snapshot. "
    "Use this only to interpret the user's latest message. "
    "Do not invent missing patient details. "
    "Do not assume a durable action has happened unless explicitly stated."
)
_STRUCTURAL_OUTPUT_REPAIR_INSTRUCTION = (
    "Previous output could not be parsed or failed schema validation. "
    "Return only a valid JSON object matching the required schema. "
    "Do not include markdown, code fences, comments, explanations, or extra fields. "
    "If the user's message is ambiguous, represent that through the allowed schema "
    "fields rather than inventing information."
)
_ISO_TIME_PATTERN = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_ISO_DATETIME_TIME_PATTERN = re.compile(r"[T ](\d{1,2}):(\d{2})")


def _coerce_non_empty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    return stripped


def _coerce_display_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    stripped = value.strip()
    if not stripped:
        return None

    match = _ISO_DATETIME_TIME_PATTERN.search(stripped)
    if match is None:
        match = _ISO_TIME_PATTERN.search(stripped)
    if match is None:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None

    return f"{hour:02d}:{minute:02d}"


def _extract_patient_identity_flags(
    conversation_context: dict[str, object],
) -> dict[str, object]:
    identity_raw = conversation_context.get("patient_identity")
    identity = identity_raw if isinstance(identity_raw, dict) else {}

    has_patient_name = bool(
        _coerce_non_empty_str(identity.get("full_name"))
        or _coerce_non_empty_str(conversation_context.get("patient_name")),
    )
    has_patient_date_of_birth = bool(
        _coerce_non_empty_str(identity.get("date_of_birth"))
        or _coerce_non_empty_str(conversation_context.get("patient_date_of_birth")),
    )
    has_confirmed_email = bool(
        _coerce_non_empty_str(identity.get("email"))
        or _coerce_non_empty_str(conversation_context.get("patient_email")),
    )

    if has_patient_name and has_patient_date_of_birth and has_confirmed_email:
        patient_identity_status = "complete"
    elif has_patient_name or has_patient_date_of_birth or has_confirmed_email:
        patient_identity_status = "partial"
    else:
        patient_identity_status = "missing"

    return {
        "patient_identity_status": patient_identity_status,
        "has_patient_name": has_patient_name,
        "has_patient_date_of_birth": has_patient_date_of_birth,
        "has_confirmed_email": has_confirmed_email,
    }


def _has_identity_source(conversation_context: dict[str, object]) -> bool:
    if "patient_identity" in conversation_context:
        return True

    return any(
        key in conversation_context
        for key in (
            "patient_name",
            "patient_email",
            "patient_date_of_birth",
            "patient_phone",
            "full_name",
            "date_of_birth",
            "phone",
            "email",
        )
    )


def _has_active_hold(conversation_context: dict[str, object]) -> bool:
    hold_id = conversation_context.get("hold_id")
    appointment_id = conversation_context.get("appointment_id")
    return bool(hold_id) and not appointment_id


def _should_include_identity_flags(conversation_context: dict[str, object]) -> bool:
    if _has_identity_source(conversation_context):
        return True

    return _has_active_hold(conversation_context)


def _extract_explicit_availability_status(
    conversation_context: dict[str, object],
) -> str | None:
    for key in ("last_availability_status", "availability_status"):
        status = _coerce_non_empty_str(conversation_context.get(key))
        if status is not None:
            return status

    return None


def sanitize_conversation_context_for_llm(
    conversation_context: dict[str, object],
) -> dict[str, object]:
    if not isinstance(conversation_context, dict):
        return {}

    snapshot: dict[str, object] = {}

    selected_specialty = _coerce_non_empty_str(
        conversation_context.get("selected_specialty_name"),
    )
    if selected_specialty is not None:
        snapshot["selected_specialty"] = selected_specialty

    selected_doctor = _coerce_non_empty_str(
        conversation_context.get("selected_doctor_name"),
    )
    if selected_doctor is not None:
        snapshot["selected_doctor"] = selected_doctor

    requested_date = _coerce_non_empty_str(conversation_context.get("requested_date"))
    if requested_date is not None:
        snapshot["requested_date"] = requested_date

    requested_time_window = conversation_context.get("requested_time_window")
    if isinstance(requested_time_window, dict):
        time_window_label = _coerce_non_empty_str(requested_time_window.get("label"))
        if time_window_label is not None:
            snapshot["requested_time_window"] = time_window_label

    selected_time = _coerce_display_time(conversation_context.get("selected_start_time"))
    if selected_time is not None:
        snapshot["selected_time"] = selected_time

    explicit_availability_status = _extract_explicit_availability_status(
        conversation_context,
    )
    offered_slots = conversation_context.get("offered_slots")
    if isinstance(offered_slots, list):
        has_offered_slots = len(offered_slots) > 0
        snapshot["has_offered_slots"] = has_offered_slots
        if explicit_availability_status is not None:
            snapshot["last_availability_status"] = explicit_availability_status
        elif has_offered_slots:
            snapshot["last_availability_status"] = "available"
    elif explicit_availability_status is not None:
        snapshot["last_availability_status"] = explicit_availability_status

    has_active_hold = _has_active_hold(conversation_context)
    if has_active_hold:
        snapshot["has_active_hold"] = True

    appointment_id = conversation_context.get("appointment_id")
    booking_confirmed = bool(appointment_id) or bool(
        conversation_context.get("booking_confirmed_at"),
    )
    if booking_confirmed:
        snapshot["booking_confirmed"] = True

    if _should_include_identity_flags(conversation_context):
        identity_flags = _extract_patient_identity_flags(conversation_context)
        snapshot.update(identity_flags)
    else:
        identity_flags = {"patient_identity_status": "missing"}

    if has_active_hold and not booking_confirmed:
        snapshot["awaiting_confirmation"] = (
            identity_flags["patient_identity_status"] == "complete"
        )

    return snapshot


@dataclass(frozen=True, slots=True)
class ReceptionistAnalysisRequest:
    user_message: str
    conversation_context: dict[str, object]


@dataclass(frozen=True, slots=True)
class ReceptionistAnalysisResult:
    analysis: ReceptionistLLMAnalysis
    model: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_micros: int
    latency_ms: int
    attempt_count: int
    primary_attempt_count: int
    fallback_attempt_count: int
    used_repair: bool
    used_fallback: bool
    used_fallback_provider: bool
    primary_provider: str
    fallback_provider: str | None
    provider: str
    failure_reason: LLMFailureReason
    failure_category: LLMFailureCategory
    prompt_version: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _ProviderAttemptSuccess:
    response: LLMResponse
    analysis: ReceptionistLLMAnalysis
    used_repair: bool
    failure_reason: LLMFailureReason


@dataclass(frozen=True, slots=True)
class _ProviderAttemptFailure:
    failure_reason: LLMFailureReason
    error: str
    used_repair: bool
    retryable: bool


class LLMReceptionistAnalysisService:
    def __init__(
        self,
        *,
        primary_provider: LLMProvider | None = None,
        provider: LLMProvider | None = None,
        fallback_provider: LLMProvider | None = None,
        primary_provider_name: LLMProviderName = LLMProviderName.FAKE,
        fallback_provider_name: LLMProviderName | None = None,
        max_primary_attempts: int = 2,
        max_fallback_attempts: int = 0,
    ) -> None:
        resolved_primary = primary_provider if primary_provider is not None else provider
        if resolved_primary is None:
            raise ValueError("primary_provider is required")

        if max_primary_attempts < 1:
            raise ValueError("max_primary_attempts must be >= 1")
        if max_fallback_attempts < 0:
            raise ValueError("max_fallback_attempts must be >= 0")

        self.primary_provider = resolved_primary
        self.fallback_provider = fallback_provider
        self.primary_provider_name = primary_provider_name
        self.fallback_provider_name = fallback_provider_name
        self.max_primary_attempts = max_primary_attempts
        self.max_fallback_attempts = max_fallback_attempts

    @property
    def provider(self) -> LLMProvider:
        return self.primary_provider

    def analyze_message(
        self,
        request: ReceptionistAnalysisRequest,
    ) -> ReceptionistAnalysisResult:
        started_at = perf_counter()
        prompt_version = get_current_receptionist_analysis_prompt_metadata().version

        primary_attempt_count = 0
        fallback_attempt_count = 0
        used_repair = False
        include_repair_prompt = False
        last_failure_reason = LLMFailureReason.NONE
        last_error: str | None = None

        while primary_attempt_count < self.max_primary_attempts:
            primary_attempt_count += 1
            llm_request = self._build_llm_request(
                request=request,
                prompt_version=prompt_version,
                include_repair_prompt=include_repair_prompt,
            )
            attempt = self._attempt_provider(
                provider=self.primary_provider,
                llm_request=llm_request,
                used_repair=used_repair,
            )
            used_repair = attempt.used_repair
            if isinstance(attempt, _ProviderAttemptSuccess):
                return self._success_result(
                    started_at=started_at,
                    response=attempt.response,
                    analysis=attempt.analysis,
                    primary_attempt_count=primary_attempt_count,
                    fallback_attempt_count=fallback_attempt_count,
                    used_repair=used_repair,
                    failure_reason=attempt.failure_reason,
                    prompt_version=prompt_version,
                    provider_name=self.primary_provider_name.value,
                    used_fallback_provider=False,
                )

            last_failure_reason = attempt.failure_reason
            last_error = attempt.error
            if not attempt.retryable:
                break

            if is_structural_output_failure(last_failure_reason):
                include_repair_prompt = True

        if (
            self.fallback_provider is not None
            and self.max_fallback_attempts > 0
            and is_fallback_provider_eligible(last_failure_reason)
        ):
            while fallback_attempt_count < self.max_fallback_attempts:
                fallback_attempt_count += 1
                llm_request = self._build_llm_request(
                    request=request,
                    prompt_version=prompt_version,
                    include_repair_prompt=include_repair_prompt,
                )
                attempt = self._attempt_provider(
                    provider=self.fallback_provider,
                    llm_request=llm_request,
                    used_repair=used_repair,
                )
                used_repair = attempt.used_repair
                if isinstance(attempt, _ProviderAttemptSuccess):
                    return self._success_result(
                        started_at=started_at,
                        response=attempt.response,
                        analysis=attempt.analysis,
                        primary_attempt_count=primary_attempt_count,
                        fallback_attempt_count=fallback_attempt_count,
                        used_repair=used_repair,
                        failure_reason=attempt.failure_reason,
                        prompt_version=prompt_version,
                        provider_name=self.fallback_provider_name.value
                        if self.fallback_provider_name is not None
                        else "unknown",
                        used_fallback_provider=True,
                    )

                last_failure_reason = attempt.failure_reason
                last_error = attempt.error
                if not is_fallback_provider_eligible(last_failure_reason):
                    break

                if is_structural_output_failure(last_failure_reason):
                    include_repair_prompt = True

        return self._deterministic_fallback_result(
            started_at=started_at,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=fallback_attempt_count,
            used_repair=used_repair,
            failure_reason=last_failure_reason,
            prompt_version=prompt_version,
            error=last_error or "LLM analysis failed",
        )

    def _attempt_provider(
        self,
        *,
        provider: LLMProvider,
        llm_request: LLMRequest,
        used_repair: bool,
    ) -> _ProviderAttemptSuccess | _ProviderAttemptFailure:
        try:
            response = provider.complete(llm_request)
            parse_outcome = parse_structured_output_with_repair_flag(
                raw_output=response.content,
                model_type=ReceptionistLLMAnalysis,
            )
            updated_used_repair = used_repair or parse_outcome.used_repair
            analysis = parse_outcome.value
            self._validate_safety(analysis)
            failure_reason = self._confidence_failure_reason(analysis)
            return _ProviderAttemptSuccess(
                response=response,
                analysis=analysis,
                used_repair=updated_used_repair,
                failure_reason=failure_reason,
            )
        except LLMProviderError as exc:
            failure_reason = provider_failure_reason(exc)
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=is_primary_provider_retryable(failure_reason),
            )
        except StructuredOutputParseError as exc:
            failure_reason = parse_failure_reason_from_parse_error(
                repair_attempted=exc.repair_attempted,
            )
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=is_primary_provider_retryable(failure_reason),
            )
        except StructuredOutputValidationError as exc:
            failure_reason = LLMFailureReason.SCHEMA_VALIDATION_FAILED
            return _ProviderAttemptFailure(
                failure_reason=failure_reason,
                error=str(exc),
                used_repair=used_repair,
                retryable=is_primary_provider_retryable(failure_reason),
            )
        except LLMOutputSafetyViolation as exc:
            return _ProviderAttemptFailure(
                failure_reason=LLMFailureReason.SAFETY_VIOLATION,
                error=str(exc),
                used_repair=used_repair,
                retryable=False,
            )
        except Exception as exc:
            logger.exception(
                "llm_receptionist_analysis_unknown_error",
                extra={
                    "event": "llm_receptionist_analysis_unknown_error",
                },
            )
            return _ProviderAttemptFailure(
                failure_reason=LLMFailureReason.PROVIDER_EXCEPTION,
                error=str(exc),
                used_repair=used_repair,
                retryable=False,
            )

    def _build_llm_request(
        self,
        *,
        request: ReceptionistAnalysisRequest,
        prompt_version: str,
        include_repair_prompt: bool = False,
    ) -> LLMRequest:
        messages = [
            LLMMessage(
                role="system",
                content=build_receptionist_system_prompt(),
            ),
        ]

        context_snapshot = sanitize_conversation_context_for_llm(
            request.conversation_context,
        )
        if context_snapshot:
            snapshot_json = json.dumps(
                context_snapshot,
                separators=(",", ":"),
                sort_keys=True,
            )
            messages.append(
                LLMMessage(
                    role="system",
                    content=(
                        f"{_CONTEXT_SNAPSHOT_PREFIX}\n"
                        f"Conversation context snapshot:\n{snapshot_json}"
                    ),
                ),
            )

        if include_repair_prompt:
            messages.append(
                LLMMessage(
                    role="system",
                    content=_STRUCTURAL_OUTPUT_REPAIR_INSTRUCTION,
                ),
            )

        messages.append(
            LLMMessage(
                role="user",
                content=request.user_message,
            ),
        )

        return LLMRequest(
            messages=messages,
            response_format="json",
            temperature=0.0,
            metadata={
                "component": "chat_receptionist",
                "prompt_version": prompt_version,
            },
        )

    def _validate_safety(self, analysis: ReceptionistLLMAnalysis) -> None:
        if analysis.intent == ReceptionistLLMIntent.EMERGENCY:
            return

        if analysis.urgency == ReceptionistUrgency.EMERGENCY:
            raise LLMOutputSafetyViolation(
                "Emergency urgency must use emergency intent",
            )

    def _confidence_failure_reason(
        self,
        analysis: ReceptionistLLMAnalysis,
    ) -> LLMFailureReason:
        if analysis.confidence < MIN_ACCEPTED_CONFIDENCE:
            return LLMFailureReason.LOW_CONFIDENCE

        return LLMFailureReason.NONE

    def _success_result(
        self,
        *,
        started_at: float,
        response: LLMResponse,
        analysis: ReceptionistLLMAnalysis,
        primary_attempt_count: int,
        fallback_attempt_count: int,
        used_repair: bool,
        failure_reason: LLMFailureReason,
        prompt_version: str,
        provider_name: str,
        used_fallback_provider: bool,
    ) -> ReceptionistAnalysisResult:
        failure_category = failure_category_for_reason(failure_reason)
        return ReceptionistAnalysisResult(
            analysis=analysis,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_micros=response.estimated_cost_micros,
            latency_ms=self._elapsed_ms(started_at),
            attempt_count=primary_attempt_count + fallback_attempt_count,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=fallback_attempt_count,
            used_repair=used_repair,
            used_fallback=False,
            used_fallback_provider=used_fallback_provider,
            primary_provider=self.primary_provider_name.value,
            fallback_provider=(
                self.fallback_provider_name.value
                if self.fallback_provider_name is not None
                else None
            ),
            provider=provider_name,
            failure_reason=failure_reason,
            failure_category=failure_category,
            prompt_version=prompt_version,
            error=None,
        )

    def _deterministic_fallback_result(
        self,
        *,
        started_at: float,
        primary_attempt_count: int,
        fallback_attempt_count: int,
        used_repair: bool,
        failure_reason: LLMFailureReason,
        prompt_version: str,
        error: str,
    ) -> ReceptionistAnalysisResult:
        logger.warning(
            "llm_receptionist_analysis_fallback",
            extra={
                "event": "llm_receptionist_analysis_fallback",
                "failure_reason": failure_reason.value,
                "failure_category": failure_category_for_reason(failure_reason).value,
                "primary_attempt_count": primary_attempt_count,
                "fallback_attempt_count": fallback_attempt_count,
                "error": error,
            },
        )
        return ReceptionistAnalysisResult(
            analysis=fallback_receptionist_analysis(),
            model=None,
            input_tokens=0,
            output_tokens=0,
            estimated_cost_micros=0,
            latency_ms=self._elapsed_ms(started_at),
            attempt_count=primary_attempt_count + fallback_attempt_count,
            primary_attempt_count=primary_attempt_count,
            fallback_attempt_count=fallback_attempt_count,
            used_repair=used_repair,
            used_fallback=True,
            used_fallback_provider=False,
            primary_provider=self.primary_provider_name.value,
            fallback_provider=(
                self.fallback_provider_name.value
                if self.fallback_provider_name is not None
                else None
            ),
            provider=self.primary_provider_name.value,
            failure_reason=failure_reason,
            failure_category=failure_category_for_reason(failure_reason),
            prompt_version=prompt_version,
            error=error,
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)


def build_llm_receptionist_analysis_service_from_settings(
    settings: Settings,
) -> LLMReceptionistAnalysisService | None:
    from app.ai.provider_factory import create_llm_provider_from_settings

    if not settings.llm_enabled:
        return None

    primary_provider = create_llm_provider_from_settings(
        settings,
        settings.resolved_llm_primary_provider,
    )
    fallback_provider = None
    fallback_provider_name = None
    max_fallback_attempts = 0

    if settings.llm_fallback_enabled:
        assert settings.llm_fallback_provider is not None
        fallback_provider_name = settings.llm_fallback_provider
        fallback_provider = create_llm_provider_from_settings(
            settings,
            settings.llm_fallback_provider,
        )
        max_fallback_attempts = settings.llm_max_fallback_attempts

    return LLMReceptionistAnalysisService(
        primary_provider=primary_provider,
        fallback_provider=fallback_provider,
        primary_provider_name=settings.resolved_llm_primary_provider,
        fallback_provider_name=fallback_provider_name,
        max_primary_attempts=settings.llm_max_primary_attempts,
        max_fallback_attempts=max_fallback_attempts,
    )
