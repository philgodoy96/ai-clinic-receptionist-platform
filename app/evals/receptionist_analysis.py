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


class EvaluationMode(StrEnum):
    RECORDED = "recorded"
    PROVIDER = "provider"


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
    prompt_version: str
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
            raise EvaluationError(f"Invalid JSONL at {path}:{line_number}: {exc.msg}") from exc

        cases.append(_parse_case(payload=payload, line_number=line_number))

    return cases


def _parse_case(*, payload: dict[str, Any], line_number: int) -> ReceptionistAnalysisEvalCase:
    case_id = _required_str(payload, "id", line_number)
    prompt_version = _required_str(payload, "prompt_version", line_number)
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
        raise EvaluationError(f"Field 'recorded_output' must be an object at line {line_number}")

    return ReceptionistAnalysisEvalCase(
        id=case_id,
        prompt_version=prompt_version,
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
            f"Invalid {field} '{value}' at line {line_number}. Allowed values: {allowed_values}"
        )


@dataclass(frozen=True, slots=True)
class EvaluationFieldResult:
    field: str
    passed: bool
    expected: Any
    actual: Any


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    case_id: str
    prompt_version: str
    passed: bool
    field_results: tuple[EvaluationFieldResult, ...]
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PromptVersionEvaluationMetrics:
    prompt_version: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    accuracy: float


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    mode: EvaluationMode
    total_cases: int
    passed_cases: int
    failed_cases: int
    accuracy: float
    intent_accuracy: float
    urgency_accuracy: float
    requires_human_accuracy: float
    safety_flag_accuracy: float
    extracted_field_accuracy: float
    prompt_versions: tuple[str, ...]
    metrics_by_prompt_version: dict[str, PromptVersionEvaluationMetrics]
    case_results: tuple[EvaluationCaseResult, ...]


def evaluate_receptionist_analysis_cases(
    cases: list[ReceptionistAnalysisEvalCase],
    *,
    mode: EvaluationMode = EvaluationMode.RECORDED,
) -> EvaluationSummary:
    if mode == EvaluationMode.PROVIDER:
        raise EvaluationError("provider mode is not implemented yet")

    case_results = [_evaluate_recorded_case(case) for case in cases]
    return build_evaluation_summary(case_results, mode=mode)


def evaluate_receptionist_analysis_case_against_actual(
    case: ReceptionistAnalysisEvalCase,
    actual: dict[str, Any],
    *,
    prompt_version: str | None = None,
) -> EvaluationCaseResult:
    resolved_prompt_version = prompt_version or case.prompt_version
    field_results = (
        _compare_scalar_field(
            field="intent",
            expected=case.expected.intent,
            recorded=actual,
            key="intent",
        ),
        _compare_scalar_field(
            field="urgency",
            expected=case.expected.urgency,
            recorded=actual,
            key="urgency",
        ),
        _compare_scalar_field(
            field="requires_human",
            expected=case.expected.requires_human,
            recorded=actual,
            key="requires_human",
        ),
        _compare_safety_flags(
            expected=case.expected.safety_flags,
            recorded=actual,
        ),
        *_compare_extracted_fields(
            expected=case.expected.extracted,
            recorded=actual,
        ),
    )
    passed = all(result.passed for result in field_results)
    return EvaluationCaseResult(
        case_id=case.id,
        prompt_version=resolved_prompt_version,
        passed=passed,
        field_results=field_results,
    )


def _evaluate_recorded_case(case: ReceptionistAnalysisEvalCase) -> EvaluationCaseResult:
    if case.recorded_output is None:
        return EvaluationCaseResult(
            case_id=case.id,
            prompt_version=case.prompt_version,
            passed=False,
            field_results=(),
            failure_reason="missing_recorded_output",
        )

    return evaluate_receptionist_analysis_case_against_actual(
        case,
        case.recorded_output,
    )


def _compare_scalar_field(
    *,
    field: str,
    expected: Any,
    recorded: dict[str, Any],
    key: str,
) -> EvaluationFieldResult:
    actual = _normalize_scalar(recorded.get(key))
    normalized_expected = _normalize_scalar(expected)
    return EvaluationFieldResult(
        field=field,
        passed=actual == normalized_expected,
        expected=normalized_expected,
        actual=actual,
    )


def _compare_safety_flags(
    *,
    expected: list[str],
    recorded: dict[str, Any],
) -> EvaluationFieldResult:
    actual_flags = recorded.get("safety_flags")
    if actual_flags is None:
        actual_set: set[str] = set()
    elif isinstance(actual_flags, list):
        actual_set = {str(item) for item in actual_flags}
    else:
        actual_set = {str(actual_flags)}

    expected_set = set(expected)
    return EvaluationFieldResult(
        field="safety_flags",
        passed=actual_set == expected_set,
        expected=sorted(expected_set),
        actual=sorted(actual_set),
    )


def _compare_extracted_fields(
    *,
    expected: dict[str, Any],
    recorded: dict[str, Any],
) -> tuple[EvaluationFieldResult, ...]:
    recorded_extracted = recorded.get("extracted")
    extracted: dict[str, Any] = recorded_extracted if isinstance(recorded_extracted, dict) else {}

    results: list[EvaluationFieldResult] = []
    for field_name, expected_value in expected.items():
        actual_value = _normalize_extracted_scalar(extracted.get(field_name))
        normalized_expected = _normalize_extracted_scalar(expected_value)
        results.append(
            EvaluationFieldResult(
                field=f"extracted.{field_name}",
                passed=actual_value == normalized_expected,
                expected=normalized_expected,
                actual=actual_value,
            ),
        )

    return tuple(results)


def _normalize_scalar(value: Any) -> Any:
    if value is None:
        return None
    return value


def _normalize_extracted_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().casefold()
    return value


def build_evaluation_summary(
    case_results: list[EvaluationCaseResult],
    *,
    mode: EvaluationMode,
) -> EvaluationSummary:
    total_cases = len(case_results)
    passed_cases = sum(1 for result in case_results if result.passed)
    failed_cases = total_cases - passed_cases

    intent_results = _collect_field_results(case_results, "intent")
    urgency_results = _collect_field_results(case_results, "urgency")
    requires_human_results = _collect_field_results(case_results, "requires_human")
    safety_flag_results = _collect_field_results(case_results, "safety_flags")
    extracted_results = _collect_field_results(case_results, prefix="extracted.")
    prompt_versions = tuple(sorted({result.prompt_version for result in case_results}))
    metrics_by_prompt_version = {
        prompt_version: _build_prompt_version_metrics(prompt_version, case_results)
        for prompt_version in prompt_versions
    }

    return EvaluationSummary(
        mode=mode,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        accuracy=_ratio(passed_cases, total_cases),
        intent_accuracy=_ratio(
            sum(1 for item in intent_results if item.passed),
            len(intent_results),
        ),
        urgency_accuracy=_ratio(
            sum(1 for item in urgency_results if item.passed),
            len(urgency_results),
        ),
        requires_human_accuracy=_ratio(
            sum(1 for item in requires_human_results if item.passed),
            len(requires_human_results),
        ),
        safety_flag_accuracy=_ratio(
            sum(1 for item in safety_flag_results if item.passed),
            len(safety_flag_results),
        ),
        extracted_field_accuracy=_ratio(
            sum(1 for item in extracted_results if item.passed),
            len(extracted_results),
        ),
        prompt_versions=prompt_versions,
        metrics_by_prompt_version=metrics_by_prompt_version,
        case_results=tuple(case_results),
    )


def _build_prompt_version_metrics(
    prompt_version: str,
    case_results: list[EvaluationCaseResult],
) -> PromptVersionEvaluationMetrics:
    version_results = [result for result in case_results if result.prompt_version == prompt_version]
    total_cases = len(version_results)
    passed_cases = sum(1 for result in version_results if result.passed)
    failed_cases = total_cases - passed_cases

    return PromptVersionEvaluationMetrics(
        prompt_version=prompt_version,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        accuracy=_ratio(passed_cases, total_cases),
    )


def _collect_field_results(
    case_results: list[EvaluationCaseResult],
    field: str | None = None,
    *,
    prefix: str | None = None,
) -> list[EvaluationFieldResult]:
    collected: list[EvaluationFieldResult] = []
    for case_result in case_results:
        for field_result in case_result.field_results:
            if field is not None and field_result.field == field:
                collected.append(field_result)
            elif prefix is not None and field_result.field.startswith(prefix):
                collected.append(field_result)
    return collected


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
