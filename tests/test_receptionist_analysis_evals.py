from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.evals.receptionist_analysis import (
    EvaluationError,
    EvaluationExpected,
    EvaluationInput,
    ReceptionistAnalysisEvalCase,
    evaluate_receptionist_analysis_cases,
    load_receptionist_analysis_eval_cases,
)


def _default_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


def _case_payload(
    *,
    case_id: str = "case_1",
    prompt_version: str | None = None,
    intent: str = "greeting",
    urgency: str = "normal",
    requires_human: bool = False,
    safety_flags: list[str] | None = None,
    extracted: dict[str, object] | None = None,
    recorded_output: dict[str, object] | None | object = ...,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": case_id,
        "prompt_version": prompt_version or _default_prompt_version(),
        "input": {"message": "Hello"},
        "expected": {
            "intent": intent,
            "urgency": urgency,
            "requires_human": requires_human,
            "safety_flags": safety_flags or [],
            "extracted": extracted
            or {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        },
    }
    if recorded_output is not ...:
        payload["recorded_output"] = recorded_output
    return payload


def _matching_recorded_output(**overrides: object) -> dict[str, object]:
    recorded: dict[str, object] = {
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
    }
    recorded.update(overrides)
    return recorded


def _build_case(
    *,
    case_id: str,
    prompt_version: str | None = None,
    expected: EvaluationExpected | None = None,
    recorded_output: dict[str, object] | None = None,
) -> ReceptionistAnalysisEvalCase:
    return ReceptionistAnalysisEvalCase(
        id=case_id,
        prompt_version=prompt_version or _default_prompt_version(),
        input=EvaluationInput(message="Hello"),
        expected=expected
        or EvaluationExpected(
            intent="greeting",
            urgency="normal",
            requires_human=False,
            safety_flags=[],
            extracted={
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        ),
        recorded_output=recorded_output,
    )


def test_load_receptionist_analysis_eval_cases_loads_jsonl_dataset(tmp_path: Path) -> None:
    dataset_path = tmp_path / "cases.jsonl"
    dataset_path.write_text(
        json.dumps(_case_payload(recorded_output=_matching_recorded_output())) + "\n",
        encoding="utf-8",
    )

    cases = load_receptionist_analysis_eval_cases(dataset_path)

    assert len(cases) == 1
    assert cases[0].id == "case_1"
    assert cases[0].prompt_version == _default_prompt_version()
    assert cases[0].input.message == "Hello"
    assert cases[0].expected.intent == "greeting"
    assert cases[0].recorded_output is not None
    assert cases[0].recorded_output["intent"] == "greeting"


def test_load_receptionist_analysis_eval_cases_invalid_jsonl_raises_with_line_number(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "invalid.jsonl"
    dataset_path.write_text("{ not valid json }\n", encoding="utf-8")

    with pytest.raises(EvaluationError, match=r"Invalid JSONL at .*:1:"):
        load_receptionist_analysis_eval_cases(dataset_path)


def test_load_receptionist_analysis_eval_cases_missing_prompt_version_raises(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "missing_prompt_version.jsonl"
    payload = _case_payload()
    del payload["prompt_version"]
    dataset_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(
        EvaluationError,
        match="Field 'prompt_version' must be a non-empty string at line 1",
    ):
        load_receptionist_analysis_eval_cases(dataset_path)


def test_load_receptionist_analysis_eval_cases_invalid_expected_intent_raises(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "invalid_intent.jsonl"
    dataset_path.write_text(
        json.dumps(_case_payload(intent="made_up_intent")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvaluationError, match="Invalid expected.intent 'made_up_intent' at line 1"):
        load_receptionist_analysis_eval_cases(dataset_path)


def test_load_receptionist_analysis_eval_cases_accepts_historical_prompt_version(
    tmp_path: Path,
) -> None:
    legacy_version = "receptionist-analysis-v0"
    dataset_path = tmp_path / "historical_prompt_version.jsonl"
    dataset_path.write_text(
        json.dumps(
            _case_payload(
                prompt_version=legacy_version,
                recorded_output=_matching_recorded_output(),
            ),
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_receptionist_analysis_eval_cases(dataset_path)

    assert len(cases) == 1
    assert cases[0].prompt_version == legacy_version
    assert cases[0].prompt_version != _default_prompt_version()


def test_evaluation_summary_lists_prompt_versions_seen() -> None:
    current_version = _default_prompt_version()
    legacy_version = "receptionist-analysis-v0"
    summary = evaluate_receptionist_analysis_cases(
        [
            _build_case(
                case_id="current",
                prompt_version=current_version,
                recorded_output=_matching_recorded_output(),
            ),
            _build_case(
                case_id="legacy",
                prompt_version=legacy_version,
                recorded_output=_matching_recorded_output(),
            ),
        ],
    )

    assert summary.prompt_versions == (legacy_version, current_version)
    assert legacy_version in summary.metrics_by_prompt_version
    assert current_version in summary.metrics_by_prompt_version


def test_perfect_recorded_output_gives_full_accuracy() -> None:
    case = _build_case(
        case_id="perfect",
        recorded_output=_matching_recorded_output(),
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.total_cases == 1
    assert summary.passed_cases == 1
    assert summary.failed_cases == 0
    assert summary.accuracy == 1.0
    assert summary.intent_accuracy == 1.0
    assert summary.urgency_accuracy == 1.0
    assert summary.requires_human_accuracy == 1.0
    assert summary.safety_flag_accuracy == 1.0
    assert summary.extracted_field_accuracy == 1.0
    assert summary.case_results[0].passed is True


def test_wrong_intent_fails_intent_metric_and_case() -> None:
    case = _build_case(
        case_id="wrong_intent",
        recorded_output=_matching_recorded_output(intent="fallback"),
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.passed_cases == 0
    assert summary.intent_accuracy == 0.0
    assert summary.case_results[0].passed is False
    intent_result = next(
        result for result in summary.case_results[0].field_results if result.field == "intent"
    )
    assert intent_result.passed is False
    assert intent_result.expected == "greeting"
    assert intent_result.actual == "fallback"


def test_wrong_urgency_fails_urgency_metric() -> None:
    case = _build_case(
        case_id="wrong_urgency",
        recorded_output=_matching_recorded_output(urgency="urgent"),
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.urgency_accuracy == 0.0
    assert summary.case_results[0].passed is False


def test_wrong_requires_human_fails_requires_human_metric() -> None:
    case = _build_case(
        case_id="wrong_requires_human",
        recorded_output=_matching_recorded_output(requires_human=True),
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.requires_human_accuracy == 0.0
    assert summary.case_results[0].passed is False


def test_safety_flags_compare_as_sets_not_order_sensitive() -> None:
    matching_case = ReceptionistAnalysisEvalCase(
        id="safety_flags_match",
        prompt_version=_default_prompt_version(),
        input=EvaluationInput(message="Emergency"),
        expected=EvaluationExpected(
            intent="emergency",
            urgency="emergency",
            requires_human=True,
            safety_flags=["medical_emergency", "other_flag"],
            extracted={"specialty": None, "doctor": None, "date": None, "time": None},
        ),
        recorded_output={
            "intent": "emergency",
            "urgency": "emergency",
            "requires_human": True,
            "safety_flags": ["other_flag", "medical_emergency"],
            "extracted": {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        },
    )

    matching_summary = evaluate_receptionist_analysis_cases([matching_case])

    assert matching_summary.passed_cases == 1
    assert matching_summary.safety_flag_accuracy == 1.0

    mismatch_case = ReceptionistAnalysisEvalCase(
        id="safety_flags_mismatch",
        prompt_version=_default_prompt_version(),
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
            "safety_flags": [],
            "extracted": {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        },
    )

    mismatch_summary = evaluate_receptionist_analysis_cases([mismatch_case])

    assert mismatch_summary.failed_cases == 1
    assert mismatch_summary.safety_flag_accuracy == 0.0


def test_extracted_field_mismatch_fails_extracted_field_accuracy() -> None:
    case = _build_case(
        case_id="extracted_mismatch",
        expected=EvaluationExpected(
            intent="greeting",
            urgency="normal",
            requires_human=False,
            safety_flags=[],
            extracted={
                "specialty": "Dermatology",
                "doctor": None,
                "date": None,
                "time": None,
            },
        ),
        recorded_output=_matching_recorded_output(
            extracted={
                "specialty": "Cardiology",
                "doctor": None,
                "date": None,
                "time": None,
            },
        ),
    )

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.extracted_field_accuracy == 0.75
    assert summary.case_results[0].passed is False
    specialty_result = next(
        result
        for result in summary.case_results[0].field_results
        if result.field == "extracted.specialty"
    )
    assert specialty_result.passed is False


def test_missing_recorded_output_fails_case() -> None:
    case = _build_case(case_id="missing_recorded_output", recorded_output=None)

    summary = evaluate_receptionist_analysis_cases([case])

    assert summary.failed_cases == 1
    assert summary.passed_cases == 0
    assert summary.accuracy == 0.0
    assert summary.case_results[0].passed is False
    assert summary.case_results[0].failure_reason == "missing_recorded_output"
    assert summary.case_results[0].field_results == ()


def test_multiple_prompt_versions_produce_grouped_metrics() -> None:
    current_version = _default_prompt_version()
    legacy_version = "receptionist-analysis-v0"

    cases = [
        _build_case(
            case_id="current_pass",
            prompt_version=current_version,
            recorded_output=_matching_recorded_output(),
        ),
        _build_case(
            case_id="current_fail",
            prompt_version=current_version,
            recorded_output=_matching_recorded_output(intent="fallback"),
        ),
        _build_case(
            case_id="legacy_pass",
            prompt_version=legacy_version,
            recorded_output=_matching_recorded_output(),
        ),
    ]

    summary = evaluate_receptionist_analysis_cases(cases)

    assert summary.prompt_versions == (legacy_version, current_version)
    assert summary.metrics_by_prompt_version[current_version].total_cases == 2
    assert summary.metrics_by_prompt_version[current_version].passed_cases == 1
    assert summary.metrics_by_prompt_version[current_version].failed_cases == 1
    assert summary.metrics_by_prompt_version[current_version].accuracy == 0.5
    assert summary.metrics_by_prompt_version[legacy_version].total_cases == 1
    assert summary.metrics_by_prompt_version[legacy_version].passed_cases == 1
    assert summary.metrics_by_prompt_version[legacy_version].accuracy == 1.0
