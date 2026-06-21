from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class EvaluationError(Exception):
    pass


class EvaluationIntent(StrEnum):
    GREETING = "greeting"
    APPOINTMENT_REQUEST = "appointment_request"
    AVAILABILITY_REQUEST = "availability_request"
    HOLD_REQUEST = "hold_request"
    BOOKING_CONFIRMATION = "booking_confirmation"
    CANCEL_REQUEST = "cancel_request"
    RESCHEDULE_REQUEST = "reschedule_request"
    HUMAN_ESCALATION_REQUEST = "human_escalation_request"
    EMERGENCY = "emergency"
    FALLBACK = "fallback"


class EvaluationUrgency(StrEnum):
    NORMAL = "normal"
    URGENT = "urgent"
    EMERGENCY = "emergency"


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    message: str


@dataclass(frozen=True, slots=True)
class EvaluationExpected:
    intent: str
    urgency: str
    requires_human: bool
    safety_flags: list[str]
    extracted: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReceptionistAnalysisEvalCase:
    id: str
    input: EvaluationInput
    expected: EvaluationExpected
    recorded_output: dict[str, Any] | None = None


def load_receptionist_analysis_eval_cases(path: Path) -> list[ReceptionistAnalysisEvalCase]:
    if not path.exists():
        raise EvaluationError(f"Evaluation dataset not found: {path}")

    cases: list[ReceptionistAnalysisEvalCase] = []

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()

        if not line:
            continue

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(
                f"Invalid JSONL at {path}:{line_number}: {exc.msg}"
            ) from exc

        cases.append(_parse_case(payload=payload, line_number=line_number))

    return cases


def _parse_case(*, payload: dict[str, Any], line_number: int) -> ReceptionistAnalysisEvalCase:
    case_id = _required_str(payload, "id", line_number)
    input_payload = _required_dict(payload, "input", line_number)
    expected_payload = _required_dict(payload, "expected", line_number)

    message = _required_str(input_payload, "message", line_number)

    intent = _required_str(expected_payload, "intent", line_number)
    urgency = _required_str(expected_payload, "urgency", line_number)
    requires_human = _required_bool(expected_payload, "requires_human", line_number)
    safety_flags = _required_str_list(expected_payload, "safety_flags", line_number)
    extracted = _required_dict(expected_payload, "extracted", line_number)

    _validate_enum_value(
        value=intent,
        allowed={item.value for item in EvaluationIntent},
        field="expected.intent",
        line_number=line_number,
    )
    _validate_enum_value(
        value=urgency,
        allowed={item.value for item in EvaluationUrgency},
        field="expected.urgency",
        line_number=line_number,
    )

    recorded_output = payload.get("recorded_output")
    if recorded_output is not None and not isinstance(recorded_output, dict):
        raise EvaluationError(
            f"Field 'recorded_output' must be an object at line {line_number}"
        )

    return ReceptionistAnalysisEvalCase(
        id=case_id,
        input=EvaluationInput(message=message),
        expected=EvaluationExpected(
            intent=intent,
            urgency=urgency,
            requires_human=requires_human,
            safety_flags=safety_flags,
            extracted=extracted,
        ),
        recorded_output=recorded_output,
    )


def _required_str(payload: dict[str, Any], field: str, line_number: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"Field '{field}' must be a non-empty string at line {line_number}")
    return value


def _required_bool(payload: dict[str, Any], field: str, line_number: int) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise EvaluationError(f"Field '{field}' must be a boolean at line {line_number}")
    return value


def _required_dict(payload: dict[str, Any], field: str, line_number: int) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise EvaluationError(f"Field '{field}' must be an object at line {line_number}")
    return value


def _required_str_list(payload: dict[str, Any], field: str, line_number: int) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EvaluationError(f"Field '{field}' must be a list of strings at line {line_number}")
    return value


def _validate_enum_value(
    *,
    value: str,
    allowed: set[str],
    field: str,
    line_number: int,
) -> None:
    if value not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise EvaluationError(
            f"Invalid {field} '{value}' at line {line_number}. "
            f"Allowed values: {allowed_values}"
        )