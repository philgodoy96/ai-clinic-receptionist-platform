from __future__ import annotations

from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.evals.provider_receptionist_analysis import (
    ProviderReceptionistAnalysisEvaluator,
    run_provider_receptionist_analysis_evaluation,
)
from app.evals.receptionist_analysis import (
    EvaluationExpected,
    EvaluationInput,
    EvaluationMode,
    ReceptionistAnalysisEvalCase,
)
from app.services.llm_receptionist import LLMReceptionistAnalysisService
from tests.llm_provider_test_helpers import (
    RaisingLLMProvider,
    StaticContentLLMProvider,
    build_receptionist_analysis_payload,
)


def _default_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


def _build_case(
    *,
    case_id: str = "case_1",
    message: str = "Hello",
    intent: str = "greeting",
    urgency: str = "normal",
    requires_human: bool = False,
    safety_flags: list[str] | None = None,
    extracted: dict[str, object] | None = None,
) -> ReceptionistAnalysisEvalCase:
    return ReceptionistAnalysisEvalCase(
        id=case_id,
        prompt_version=_default_prompt_version(),
        input=EvaluationInput(message=message),
        expected=EvaluationExpected(
            intent=intent,
            urgency=urgency,
            requires_human=requires_human,
            safety_flags=safety_flags or [],
            extracted=extracted
            or {
                "specialty": None,
                "doctor": None,
                "date": None,
                "time": None,
            },
        ),
    )


def test_perfect_provider_outputs_pass() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(build_receptionist_analysis_payload()),
    )
    case = _build_case()

    summary = run_provider_receptionist_analysis_evaluation(
        cases=[case],
        llm_analysis_service=service,
    )

    assert summary.mode == EvaluationMode.PROVIDER
    assert summary.passed_cases == 1
    assert summary.failed_cases == 0
    assert summary.accuracy == 1.0


def test_wrong_provider_output_fails_metrics() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(
            build_receptionist_analysis_payload(intent="fallback"),
        ),
    )
    case = _build_case()

    summary = run_provider_receptionist_analysis_evaluation(
        cases=[case],
        llm_analysis_service=service,
    )

    assert summary.failed_cases == 1
    assert summary.intent_accuracy == 0.0
    assert summary.case_results[0].passed is False


def test_provider_error_becomes_failed_case_with_fallback_result() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())
    case = _build_case()
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)

    summary = evaluator.evaluate([case])
    case_output = evaluator.last_case_outputs[0]

    assert summary.failed_cases == 1
    assert summary.case_results[0].passed is False
    assert case_output.failure_reason == "provider_error"
    assert case_output.actual["intent"] == "fallback"


def test_invalid_json_becomes_failed_case_with_fallback_result() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("this is not valid json"),
    )
    case = _build_case()
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)

    summary = evaluator.evaluate([case])
    case_output = evaluator.last_case_outputs[0]

    assert summary.failed_cases == 1
    assert summary.case_results[0].passed is False
    assert case_output.failure_reason == "invalid_json"
    assert case_output.actual["intent"] == "fallback"


def test_prompt_version_appears_in_provider_run_case_output_and_summary() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(build_receptionist_analysis_payload()),
    )
    case = _build_case()
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)

    summary = evaluator.evaluate([case])
    case_output = evaluator.last_case_outputs[0]

    assert case_output.prompt_version == _default_prompt_version()
    assert summary.case_results[0].prompt_version == _default_prompt_version()
    assert _default_prompt_version() in summary.prompt_versions
