from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.adapters.retell.scheduling_tools import RetellSchedulingToolAdapter
from app.api.errors import (
    INVALID_RETELL_PAYLOAD_CODE,
    RETELL_TOOL_ARGUMENTS_INVALID_CODE,
    RETELL_TOOL_PROVIDER_CALL_ID_REQUIRED_CODE,
    UNSUPPORTED_RETELL_TOOL_CODE,
)
from app.domain.retell_tools import (
    MissingProviderCallIdError,
    ParsedRetellToolCall,
    RetellSupportedToolName,
    RetellToolCallValidationError,
    UnsupportedRetellToolNameError,
    build_failed_tool_call_response,
    build_rejected_tool_call_response,
    build_retell_tool_call_event_type,
    build_retell_tool_call_idempotency_key,
    build_succeeded_tool_call_response,
    deserialize_tool_call_outcome,
    parse_retell_tool_call,
    serialize_tool_call_outcome,
)
from app.domain.scheduling.appointment_holds import AppointmentHold
from app.models.scheduling import Appointment, AvailabilitySlot, Doctor, Patient, Specialty
from app.models.voice_calls import VoiceCall
from app.schemas.retell_tools import (
    CheckAvailabilityToolArguments,
    HoldAppointmentSlotToolArguments,
    ReleaseAppointmentHoldToolArguments,
    RetellCheckAvailabilityRequest,
    RetellToolCallRequest,
    RetellToolCallResponse,
    RetellToolResponse,
)
from app.services.appointment_holds import (
    AppointmentHoldOwnershipError,
    AppointmentSlotAlreadyHeldError,
    InvalidAppointmentHoldOwnerError,
    InvalidAppointmentHoldWindowError,
)
from app.services.retell_call_lifecycle import DEFAULT_RETELL_PROVIDER
from app.services.retell_tool_registry import is_side_effecting_retell_tool
from app.services.scheduling import (
    AvailabilitySlotNotFoundError,
    AvailabilitySlotUnavailableError,
    PatientLookupCriteria,
)

logger = logging.getLogger("app.retell_tool_adapter")

_RETELL_TOOL_EXECUTION_FAILED_CODE = "retell_tool_execution_failed"


class SchedulingServiceForRetellToolCalling(Protocol):
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

    def get_available_slot_for_hold(self, availability_slot_id: UUID) -> AvailabilitySlot:
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


class VoiceCallRepositoryForRetellToolCalling(Protocol):
    def get_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> VoiceCall | None:
        raise NotImplementedError

    def get_or_create_voice_call(
        self,
        *,
        provider: str,
        provider_call_id: str,
    ) -> tuple[VoiceCall, bool]:
        raise NotImplementedError

    def get_tool_call_outcome_by_idempotency_key(
        self,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    def record_tool_call_outcome(
        self,
        *,
        voice_call_id: UUID,
        provider: str,
        provider_call_id: str,
        event_type: str,
        tool_call_id: str,
        idempotency_key: str,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> bool:
        raise NotImplementedError


class AppointmentHoldServiceForRetellToolCalling(Protocol):
    ttl_seconds: int

    def create_hold(
        self,
        *,
        availability_slot_id: UUID,
        doctor_id: UUID,
        start_time: datetime,
        end_time: datetime,
        owner_id: str,
    ) -> AppointmentHold:
        raise NotImplementedError

    def release_hold_by_id(self, *, hold_id: UUID, owner_id: str) -> None:
        raise NotImplementedError


class RetellToolCallingAdapter:
    def __init__(
        self,
        *,
        scheduling_service: SchedulingServiceForRetellToolCalling,
        hold_service: AppointmentHoldServiceForRetellToolCalling,
        voice_calls: VoiceCallRepositoryForRetellToolCalling,
        scheduling_tools: RetellSchedulingToolAdapter | None = None,
        provider: str = DEFAULT_RETELL_PROVIDER,
    ) -> None:
        self.scheduling_service = scheduling_service
        self.hold_service = hold_service
        self.voice_calls = voice_calls
        self.scheduling_tools = scheduling_tools or RetellSchedulingToolAdapter(
            scheduling_service,
        )
        self.provider = provider

    def execute(self, request: RetellToolCallRequest) -> RetellToolCallResponse:
        try:
            parsed = parse_retell_tool_call(request)
        except MissingProviderCallIdError:
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=RETELL_TOOL_PROVIDER_CALL_ID_REQUIRED_CODE,
            )
        except UnsupportedRetellToolNameError:
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=UNSUPPORTED_RETELL_TOOL_CODE,
            )
        except RetellToolCallValidationError:
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=RETELL_TOOL_ARGUMENTS_INVALID_CODE,
            )
        except Exception:
            logger.exception("Unexpected Retell tool call validation failure")
            return build_rejected_tool_call_response(
                tool_name=request.tool_name,
                tool_call_id=request.tool_call_id,
                error_code=INVALID_RETELL_PAYLOAD_CODE,
            )

        duplicate_response = self._load_duplicate_side_effect_response(parsed)

        if duplicate_response is not None:
            return duplicate_response

        try:
            response = self._dispatch(parsed)
        except Exception:
            logger.exception(
                "Unexpected Retell tool execution failure for tool %s",
                parsed.tool_name.value,
            )
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code=_RETELL_TOOL_EXECUTION_FAILED_CODE,
            )

        if (
            response.status == "succeeded"
            and parsed.tool_call_id is not None
            and is_side_effecting_retell_tool(parsed.tool_name)
        ):
            self._record_side_effect_outcome(parsed, response)

        return response

    def _dispatch(self, parsed: ParsedRetellToolCall) -> RetellToolCallResponse:
        if parsed.tool_name is RetellSupportedToolName.CHECK_AVAILABILITY:
            return self._execute_check_availability(parsed)

        if parsed.tool_name is RetellSupportedToolName.HOLD_APPOINTMENT_SLOT:
            return self._execute_hold_appointment_slot(parsed)

        if parsed.tool_name is RetellSupportedToolName.RELEASE_APPOINTMENT_HOLD:
            return self._execute_release_appointment_hold(parsed)

        return build_rejected_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            error_code=UNSUPPORTED_RETELL_TOOL_CODE,
        )

    def _execute_check_availability(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        arguments = _as_check_availability_arguments(parsed.arguments)
        payload = RetellCheckAvailabilityRequest(
            call_id=parsed.provider_call_id,
            doctor_id=arguments.doctor_id,
            doctor_name=arguments.doctor_name,
            specialty_name=arguments.specialty_name,
            start_from=arguments.start_from,
            start_to=arguments.start_to,
        )
        legacy_response = self.scheduling_tools.check_availability(payload)

        response = self._translate_legacy_response(parsed, legacy_response)

        if response.status != "succeeded":
            return response

        result = dict(response.result)
        slots = result.get("available_slots")

        if isinstance(slots, list) and arguments.limit is not None:
            result["available_slots"] = slots[: arguments.limit]

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result=result,
        )

    def _execute_hold_appointment_slot(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        arguments = _as_hold_arguments(parsed.arguments)
        owner_id = self._resolve_hold_owner_id(
            provider_call_id=parsed.provider_call_id,
            owner_id=arguments.owner_id,
        )

        if owner_id is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="missing_hold_owner",
            )

        try:
            slot = self.scheduling_service.get_available_slot_for_hold(
                arguments.availability_slot_id,
            )
            hold = self.hold_service.create_hold(
                availability_slot_id=slot.id,
                doctor_id=slot.doctor_id,
                start_time=slot.start_time,
                end_time=slot.end_time,
                owner_id=owner_id,
            )
        except AvailabilitySlotNotFoundError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="availability_slot_not_found",
            )
        except AvailabilitySlotUnavailableError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="availability_slot_unavailable",
            )
        except AppointmentSlotAlreadyHeldError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="slot_already_held",
            )
        except (
            InvalidAppointmentHoldOwnerError,
            InvalidAppointmentHoldWindowError,
        ):
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="invalid_appointment_hold",
            )
        except AppointmentHoldOwnershipError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_hold_ownership_error",
            )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result={
                "hold_id": str(hold.hold_id),
                "availability_slot_id": str(hold.availability_slot_id),
                "doctor_id": str(hold.doctor_id),
                "start_time": hold.start_time.isoformat(),
                "end_time": hold.end_time.isoformat(),
                "expires_in_seconds": self._resolve_hold_ttl_seconds(arguments.ttl_seconds),
            },
        )

    def _execute_release_appointment_hold(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse:
        arguments = _as_release_arguments(parsed.arguments)
        owner_id = self._resolve_hold_owner_id(
            provider_call_id=parsed.provider_call_id,
            owner_id=arguments.owner_id,
        )

        if owner_id is None:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="missing_hold_owner",
            )

        try:
            self.hold_service.release_hold_by_id(
                hold_id=arguments.hold_id,
                owner_id=owner_id,
            )
        except AppointmentHoldOwnershipError:
            return build_failed_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                error_code="appointment_hold_ownership_error",
            )

        return build_succeeded_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            result={"released": True, "hold_id": str(arguments.hold_id)},
        )

    def _translate_legacy_response(
        self,
        parsed: ParsedRetellToolCall,
        legacy_response: RetellToolResponse,
    ) -> RetellToolCallResponse:
        if legacy_response.ok:
            result = legacy_response.result

            if not isinstance(result, dict):
                result = {}

            return build_succeeded_tool_call_response(
                tool_name=parsed.tool_name.value,
                tool_call_id=parsed.tool_call_id,
                result=result,
            )

        return build_failed_tool_call_response(
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
            error_code=legacy_response.error_code or _RETELL_TOOL_EXECUTION_FAILED_CODE,
        )

    def _load_duplicate_side_effect_response(
        self,
        parsed: ParsedRetellToolCall,
    ) -> RetellToolCallResponse | None:
        if parsed.tool_call_id is None:
            return None

        if not is_side_effecting_retell_tool(parsed.tool_name):
            return None

        idempotency_key = build_retell_tool_call_idempotency_key(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
        )
        stored_outcome = self.voice_calls.get_tool_call_outcome_by_idempotency_key(
            idempotency_key=idempotency_key,
        )

        if stored_outcome is None:
            return None

        response = deserialize_tool_call_outcome(stored_outcome)
        response.duplicate = True

        return response

    def _record_side_effect_outcome(
        self,
        parsed: ParsedRetellToolCall,
        response: RetellToolCallResponse,
    ) -> None:
        if parsed.tool_call_id is None:
            return

        voice_call = self.voice_calls.get_by_provider_call_id(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
        )

        if voice_call is None:
            voice_call, _ = self.voice_calls.get_or_create_voice_call(
                provider=self.provider,
                provider_call_id=parsed.provider_call_id,
            )

        occurred_at = parsed.occurred_at or datetime.now(UTC)
        idempotency_key = build_retell_tool_call_idempotency_key(
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
            tool_name=parsed.tool_name.value,
            tool_call_id=parsed.tool_call_id,
        )

        self.voice_calls.record_tool_call_outcome(
            voice_call_id=voice_call.id,
            provider=self.provider,
            provider_call_id=parsed.provider_call_id,
            event_type=build_retell_tool_call_event_type(parsed.tool_name),
            tool_call_id=parsed.tool_call_id,
            idempotency_key=idempotency_key,
            outcome=serialize_tool_call_outcome(response),
            occurred_at=occurred_at,
        )

    def _resolve_hold_owner_id(
        self,
        *,
        provider_call_id: str,
        owner_id: str | None,
    ) -> str | None:
        for candidate in [owner_id, provider_call_id]:
            if candidate is None:
                continue

            normalized = candidate.strip()

            if normalized:
                return normalized

        return None

    def _resolve_hold_ttl_seconds(self, requested_ttl_seconds: int | None) -> int:
        if requested_ttl_seconds is not None:
            return requested_ttl_seconds

        return self.hold_service.ttl_seconds


def _as_check_availability_arguments(
    arguments: Any,
) -> CheckAvailabilityToolArguments:
    if not isinstance(arguments, CheckAvailabilityToolArguments):
        msg = "expected check availability tool arguments"
        raise TypeError(msg)

    return arguments


def _as_hold_arguments(
    arguments: Any,
) -> HoldAppointmentSlotToolArguments:
    if not isinstance(arguments, HoldAppointmentSlotToolArguments):
        msg = "expected hold appointment slot tool arguments"
        raise TypeError(msg)

    return arguments


def _as_release_arguments(
    arguments: Any,
) -> ReleaseAppointmentHoldToolArguments:
    if not isinstance(arguments, ReleaseAppointmentHoldToolArguments):
        msg = "expected release appointment hold tool arguments"
        raise TypeError(msg)

    return arguments
