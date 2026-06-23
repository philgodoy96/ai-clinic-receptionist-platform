from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from app.domain.scheduling.expressions import (
    DateExpression,
    DateExpressionKind,
    DateResolutionStatus,
    TimeWindowExpression,
    TimeWindowResolutionStatus,
)
from app.schemas.retell_tools import CheckAvailabilityToolArguments
from app.schemas.scheduling_expressions import (
    DateExpressionSchema,
)
from app.services.clinic_time import ClinicTimeService
from app.services.date_parsing import DateParseStatus, NaturalLanguageDateParser


@dataclass(frozen=True, slots=True)
class ResolvedAvailabilityWindow:
    start_from: datetime | None = None
    end_to: datetime | None = None
    resolved_date: date | None = None
    error_code: str | None = None
    reason: str | None = None

    @property
    def is_resolved(self) -> bool:
        return (
            self.error_code is None
            and self.start_from is not None
            and self.end_to is not None
            and self.start_from < self.end_to
        )


@dataclass(frozen=True, slots=True)
class SlotSchedulingValidation:
    is_valid: bool
    error_code: str | None = None
    reason: str | None = None


class SchedulingAvailabilityResolver:
    def __init__(
        self,
        clinic_time: ClinicTimeService,
        *,
        date_parser: NaturalLanguageDateParser | None = None,
    ) -> None:
        self._clinic_time = clinic_time
        self._date_parser = date_parser
        self._timezone = clinic_time.timezone

    def resolve_check_availability(
        self,
        arguments: CheckAvailabilityToolArguments,
        voice_context: dict[str, Any] | None = None,
    ) -> ResolvedAvailabilityWindow:
        voice_context = voice_context or {}

        if (
            arguments.date_expression is not None
            or arguments.time_window_expression is not None
            or arguments.requested_date_text is not None
        ):
            return self._resolve_structured_arguments(arguments)

        if arguments.start_from is not None and arguments.start_to is not None:
            return self._resolve_legacy_datetime_window(arguments)

        requested_date = _safe_string(voice_context.get("requested_date"))
        if requested_date is not None:
            legacy_arguments = arguments.model_copy(
                update={
                    "date_expression": DateExpressionSchema(
                        kind=DateExpressionKind.EXACT_DATE,
                        exact_date=date.fromisoformat(requested_date),
                    ),
                },
            )
            window = self._resolve_structured_arguments(legacy_arguments)
            if not window.is_resolved:
                return window

            requested_time_window = _safe_time_window(
                voice_context.get("requested_time_window"),
            )
            if requested_time_window is None:
                return window

            return self._apply_voice_context_time_window(
                resolved_date=window.resolved_date,
                requested_time_window=requested_time_window,
                fallback_start=window.start_from,
                fallback_end=window.end_to,
            )

        return ResolvedAvailabilityWindow(
            error_code="invalid_scheduling_expression",
            reason="missing_date_expression",
        )

    def validate_slot_start_time(self, slot_start: datetime) -> SlotSchedulingValidation:
        local_start = slot_start.astimezone(self._timezone)
        resolved_date = self._clinic_time.resolve_date(
            DateExpression(
                kind=DateExpressionKind.EXACT_DATE,
                exact_date=local_start.date(),
            ),
        )

        if resolved_date.status is DateResolutionStatus.PAST_DATE:
            return SlotSchedulingValidation(
                is_valid=False,
                error_code="past_date",
                reason="past_date",
            )

        if resolved_date.status is DateResolutionStatus.CLOSED_DAY:
            return SlotSchedulingValidation(
                is_valid=False,
                error_code="clinic_closed",
                reason="closed_day",
            )

        if resolved_date.status is not DateResolutionStatus.RESOLVED:
            return SlotSchedulingValidation(
                is_valid=False,
                error_code="invalid_scheduling_expression",
                reason=resolved_date.reason,
            )

        time_label = local_start.strftime("%H:%M")
        if not self._clinic_time.is_within_business_hours(time_label):
            return SlotSchedulingValidation(
                is_valid=False,
                error_code="outside_business_hours",
                reason="outside_business_hours",
            )

        return SlotSchedulingValidation(is_valid=True)

    def _resolve_structured_arguments(
        self,
        arguments: CheckAvailabilityToolArguments,
    ) -> ResolvedAvailabilityWindow:
        date_expression = self._resolve_date_expression(arguments)
        if date_expression is None:
            return ResolvedAvailabilityWindow(
                error_code="invalid_scheduling_expression",
                reason="missing_date_expression",
            )

        resolved_date = self._clinic_time.resolve_date(date_expression)
        if resolved_date.status is DateResolutionStatus.PAST_DATE:
            return ResolvedAvailabilityWindow(
                error_code="past_date",
                reason="past_date",
            )
        if resolved_date.status is DateResolutionStatus.CLOSED_DAY:
            return ResolvedAvailabilityWindow(
                error_code="clinic_closed",
                reason="closed_day",
            )
        if resolved_date.status is not DateResolutionStatus.RESOLVED:
            return ResolvedAvailabilityWindow(
                error_code="invalid_scheduling_expression",
                reason=resolved_date.reason,
            )

        assert resolved_date.resolved_date is not None
        time_window_expression = self._resolve_time_window_expression(arguments)
        if time_window_expression is not None:
            resolved_time_window = self._clinic_time.resolve_time_window(
                time_window_expression,
            )
            if resolved_time_window.status is TimeWindowResolutionStatus.OUTSIDE_BUSINESS_HOURS:
                return ResolvedAvailabilityWindow(
                    error_code="outside_business_hours",
                    reason="outside_business_hours",
                )
            if resolved_time_window.status is not TimeWindowResolutionStatus.RESOLVED:
                return ResolvedAvailabilityWindow(
                    error_code="invalid_scheduling_expression",
                    reason=resolved_time_window.reason,
                )

            start_from, end_to = self._build_local_window(
                resolved_date=resolved_date.resolved_date,
                resolved_time_window=resolved_time_window,
            )
            if start_from >= end_to:
                return ResolvedAvailabilityWindow(
                    error_code="outside_business_hours",
                    reason="outside_business_hours",
                )

            return ResolvedAvailabilityWindow(
                start_from=start_from.astimezone(UTC),
                end_to=end_to.astimezone(UTC),
                resolved_date=resolved_date.resolved_date,
            )

        start_from, end_to = self._build_business_hours_window(
            resolved_date.resolved_date,
        )
        return ResolvedAvailabilityWindow(
            start_from=start_from.astimezone(UTC),
            end_to=end_to.astimezone(UTC),
            resolved_date=resolved_date.resolved_date,
        )

    def _resolve_legacy_datetime_window(
        self,
        arguments: CheckAvailabilityToolArguments,
    ) -> ResolvedAvailabilityWindow:
        assert arguments.start_from is not None
        assert arguments.start_to is not None

        if arguments.start_to <= arguments.start_from:
            return ResolvedAvailabilityWindow(
                error_code="invalid_scheduling_expression",
                reason="invalid_availability_window",
            )

        local_start = arguments.start_from.astimezone(self._timezone)
        resolved_date = self._clinic_time.resolve_date(
            DateExpression(
                kind=DateExpressionKind.EXACT_DATE,
                exact_date=local_start.date(),
            ),
        )

        if resolved_date.status is DateResolutionStatus.PAST_DATE:
            return ResolvedAvailabilityWindow(
                error_code="past_date",
                reason="past_date",
            )
        if resolved_date.status is DateResolutionStatus.CLOSED_DAY:
            return ResolvedAvailabilityWindow(
                error_code="clinic_closed",
                reason="closed_day",
            )
        if resolved_date.status is not DateResolutionStatus.RESOLVED:
            return ResolvedAvailabilityWindow(
                error_code="invalid_scheduling_expression",
                reason=resolved_date.reason,
            )

        return ResolvedAvailabilityWindow(
            start_from=arguments.start_from,
            end_to=arguments.start_to,
            resolved_date=local_start.date(),
        )

    def _resolve_date_expression(
        self,
        arguments: CheckAvailabilityToolArguments,
    ) -> DateExpression | None:
        if arguments.date_expression is not None:
            return arguments.date_expression.to_domain()

        if arguments.requested_date_text:
            parsed = self._parse_requested_date_text(arguments.requested_date_text)
            if parsed is not None:
                return parsed

        return None

    def _resolve_time_window_expression(
        self,
        arguments: CheckAvailabilityToolArguments,
    ) -> TimeWindowExpression | None:
        if arguments.time_window_expression is not None:
            return arguments.time_window_expression.to_domain()
        return None

    def _parse_requested_date_text(self, requested_date_text: str) -> DateExpression | None:
        normalized = requested_date_text.strip()
        if not normalized:
            return None

        if self._date_parser is not None:
            parse_result = self._date_parser.parse(normalized)
            if (
                parse_result.status is DateParseStatus.PARSED
                and parse_result.normalized_date is not None
            ):
                return DateExpression(
                    kind=DateExpressionKind.EXACT_DATE,
                    exact_date=date.fromisoformat(parse_result.normalized_date),
                )

        try:
            return DateExpression(
                kind=DateExpressionKind.EXACT_DATE,
                exact_date=date.fromisoformat(normalized),
            )
        except ValueError:
            return None

    def _build_business_hours_window(
        self,
        resolved_date: date,
    ) -> tuple[datetime, datetime]:
        context = self._clinic_time.get_current_clinic_context()
        start_time = _parse_hhmm(context.business_hours_start)
        end_time = _parse_hhmm(context.business_hours_end)
        start_local = datetime.combine(resolved_date, start_time, tzinfo=self._timezone)
        end_local = datetime.combine(resolved_date, end_time, tzinfo=self._timezone)
        return start_local, end_local

    def _build_local_window(
        self,
        *,
        resolved_date: date,
        resolved_time_window: Any,
    ) -> tuple[datetime, datetime]:
        context = self._clinic_time.get_current_clinic_context()
        business_start = _parse_hhmm(context.business_hours_start)
        business_end = _parse_hhmm(context.business_hours_end)

        if resolved_time_window.exact_time is not None:
            exact_time = _parse_hhmm(resolved_time_window.exact_time)
            start_local = datetime.combine(resolved_date, exact_time, tzinfo=self._timezone)
            end_local = start_local + timedelta(hours=1)
            end_cap = datetime.combine(resolved_date, business_end, tzinfo=self._timezone)
            if end_local > end_cap:
                end_local = end_cap
            return start_local, end_local

        window_start = _parse_hhmm(resolved_time_window.start_time)
        window_end = _parse_hhmm(resolved_time_window.end_time)
        effective_start = max(business_start, window_start)
        effective_end = min(business_end, window_end)
        start_local = datetime.combine(resolved_date, effective_start, tzinfo=self._timezone)
        end_local = datetime.combine(resolved_date, effective_end, tzinfo=self._timezone)
        return start_local, end_local

    def _apply_voice_context_time_window(
        self,
        *,
        resolved_date: date | None,
        requested_time_window: dict[str, str],
        fallback_start: datetime | None,
        fallback_end: datetime | None,
    ) -> ResolvedAvailabilityWindow:
        if resolved_date is None or fallback_start is None or fallback_end is None:
            return ResolvedAvailabilityWindow(
                error_code="invalid_scheduling_expression",
                reason="missing_resolved_date",
            )

        start_label = requested_time_window.get("start")
        end_label = requested_time_window.get("end")
        if not start_label or not end_label:
            return ResolvedAvailabilityWindow(
                start_from=fallback_start,
                end_to=fallback_end,
                resolved_date=resolved_date,
            )

        start_local = datetime.combine(
            resolved_date,
            _parse_hhmm(start_label),
            tzinfo=self._timezone,
        )
        end_local = datetime.combine(
            resolved_date,
            _parse_hhmm(end_label),
            tzinfo=self._timezone,
        )
        context = self._clinic_time.get_current_clinic_context()
        business_start = datetime.combine(
            resolved_date,
            _parse_hhmm(context.business_hours_start),
            tzinfo=self._timezone,
        )
        business_end = datetime.combine(
            resolved_date,
            _parse_hhmm(context.business_hours_end),
            tzinfo=self._timezone,
        )

        effective_start = max(start_local, business_start)
        effective_end = min(end_local, business_end)
        if effective_start >= effective_end:
            return ResolvedAvailabilityWindow(
                error_code="outside_business_hours",
                reason="outside_business_hours",
            )

        return ResolvedAvailabilityWindow(
            start_from=effective_start.astimezone(UTC),
            end_to=effective_end.astimezone(UTC),
            resolved_date=resolved_date,
        )


def _parse_hhmm(value: str) -> time:
    hour, minute = map(int, value.split(":"))
    return time(hour=hour, minute=minute)


def _safe_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _safe_time_window(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None

    safe_window: dict[str, str] = {}
    for key in ("label", "start", "end"):
        normalized = _safe_string(value.get(key))
        if normalized is not None:
            safe_window[key] = normalized

    return safe_window or None
