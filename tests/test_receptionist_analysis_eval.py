from __future__ import annotations

from app.evals.receptionist_analysis import (
    EvaluationExpected,
    EvaluationInput,
    ReceptionistAnalysisEvalCase,
    evaluate_receptionist_analysis_cases,
)


def _build_case(
    *,
    case_id: str,
    expected_extracted: dict[str, object] | None = None,
    recorded_output: dict[str, object] | None = None,
) -> ReceptionistAnalysisEvalCase:
    return ReceptionistAnalysisEvalCase(
        id=case_id,
        input=EvaluationInput(message="Hello"),
        expected=EvaluationExpected(
            intent="greeting",
            urgency="normal",
            requires_human=False,
            safety_flags=[],
            extracted=expected_extracted
            or {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        ),
        recorded_output=recorded_output,
    )


def test_evaluate_receptionist_analysis_cases_passes_matching_recorded_output() -> None:
    case = _build_case(
        case_id="match",
        recorded_output={
            "intent": "greeting",
            "urgency": "normal",
            "requires_human": False,
            "safety_flags": [],
            "extracted": {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        },
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.total_cases == 1
    assert summary.passed_cases == 1
    assert summary.failed_cases == 0
    assert summary.accuracy == 1.0


def test_evaluate_receptionist_analysis_cases_fails_when_recorded_output_missing() -> None:
    case = _build_case(case_id="missing", recorded_output=None)

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.case_results[0].failure_reason == "missing_recorded_output"


def test_evaluate_receptionist_analysis_cases_normalizes_missing_extracted_values_as_null() -> None:
    case = _build_case(
        case_id="missing_extracted_field",
        recorded_output={
            "intent": "greeting",
            "urgency": "normal",
            "requires_human": False,
            "safety_flags": [],
            "extracted": {},
        },
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.passed_cases == 1
    assert summary.extracted_field_accuracy == 1.0


def test_evaluate_receptionist_analysis_cases_compares_safety_flags_as_sets() -> None:
    case = ReceptionistAnalysisEvalCase(
        id="safety_flags",
        input=EvaluationInput(message="Emergency"),
        expected=EvaluationExpected(
            intent="emergency",
            urgency="emergency",
            requires_human=True,
            safety_flags=["medical_emergency"],
            extracted={"specialty": None, "doctor": None, "date": None, "time": None},
        ),
        recorded_output={
            "intent": "emergency",
            "urgency": "emergency",
            "requires_human": True,
            "safety_flags": ["medical_emergency"],
            "extracted": {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        },
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.passed_cases == 1
    assert summary.safety_flag_accuracy == 1.0


def test_evaluate_receptionist_analysis_cases_reports_field_mismatch() -> None:
    case = _build_case(
        case_id="intent_mismatch",
        recorded_output={
            "intent": "fallback",
            "urgency": "normal",
            "requires_human": False,
            "safety_flags": [],
            "extracted": {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        },
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.intent_accuracy == 0.0
    intent_result = summary.case_results[0].field_results[0]
    assert intent_result.field == "intent"
    assert intent_result.passed is False
