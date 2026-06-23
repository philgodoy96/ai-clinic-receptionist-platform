from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.groq_provider import GroqLLMProvider
from app.ai.llm_provider import LLMProviderError, LLMRequest, LLMResponse
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from app.evals.provider_receptionist_analysis import (
    ProviderReceptionistAnalysisEvaluator,
    build_evaluation_report,
    run_provider_receptionist_analysis_evaluation,
)
from app.evals.receptionist_analysis import (
    EvaluationExpected,
    EvaluationInput,
    EvaluationMode,
    ReceptionistAnalysisEvalCase,
    evaluate_receptionist_analysis_cases,
    load_receptionist_analysis_eval_cases,
)
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    build_llm_receptionist_analysis_service_from_settings,
)
from tests.eval_report_test_helpers import assert_report_excludes_secret_like_keys
from tests.llm_provider_test_helpers import (
    RaisingLLMProvider,
    StaticContentLLMProvider,
    build_receptionist_analysis_payload,
)
from tests.test_llm_provider_config import load_settings


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


def test_provider_eval_runs_with_fake_llm_provider_without_real_calls() -> None:
    service = LLMReceptionistAnalysisService(provider=FakeLLMProvider())
    case = _build_case(message="Hello", intent="greeting")
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)

    summary = evaluator.evaluate([case])
    case_output = evaluator.last_case_outputs[0]

    assert summary.passed_cases == 1
    assert summary.failed_cases == 0
    assert case_output.failure_reason is None
    assert case_output.actual["intent"] == "greeting"
    assert case_output.prompt_version == _default_prompt_version()


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
    assert case_output.failure_reason == "provider_exception"
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
    assert case_output.failure_reason == "json_parse_failed"
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


def test_provider_error_on_one_case_does_not_crash_whole_run() -> None:
    class SelectiveFailProvider:
        def complete(self, request: LLMRequest) -> LLMResponse:
            user_message = next(
                message.content for message in reversed(request.messages) if message.role == "user"
            )
            if user_message == "trigger provider failure":
                raise LLMProviderError("simulated provider failure")

            return LLMResponse(
                content=build_receptionist_analysis_payload(),
                model="test-model",
                input_tokens=10,
                output_tokens=8,
                estimated_cost_micros=0,
            )

    service = LLMReceptionistAnalysisService(provider=SelectiveFailProvider())
    cases = [
        _build_case(case_id="passing_case", message="Hello"),
        _build_case(case_id="failing_case", message="trigger provider failure"),
    ]
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)

    summary = evaluator.evaluate(cases)
    case_outputs = evaluator.last_case_outputs

    assert summary.total_cases == 2
    assert summary.passed_cases == 1
    assert summary.failed_cases == 1
    assert case_outputs[0].failure_reason is None
    assert case_outputs[1].failure_reason == "provider_exception"
    assert case_outputs[1].actual["intent"] == "fallback"


def test_build_evaluation_report_includes_provider_mode_metrics(tmp_path: Path) -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(build_receptionist_analysis_payload()),
    )
    case = _build_case()
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)
    summary = evaluator.evaluate([case])
    dataset_path = tmp_path / "cases.jsonl"

    report = build_evaluation_report(
        summary=summary,
        dataset_path=dataset_path,
        cases=[case],
        provider_case_outputs=evaluator.last_case_outputs,
    )
    report_data = cast(dict[str, Any], report)
    summary_data = cast(dict[str, Any], report_data["summary"])
    cases_data = cast(list[dict[str, Any]], report_data["cases"])
    provider_data = cast(dict[str, Any], cases_data[0]["provider"])
    metrics_by_prompt_version = cast(dict[str, Any], report_data["metrics_by_prompt_version"])

    assert report_data["mode"] == "provider"
    assert summary_data["total_cases"] == 1
    assert summary_data["passed_cases"] == 1
    assert summary_data["failed_cases"] == 0
    assert summary_data["intent_accuracy"] == 1.0
    assert provider_data["provider"] == "StaticContentLLMProvider"
    assert provider_data["prompt_version"] == _default_prompt_version()
    assert _default_prompt_version() in metrics_by_prompt_version


def test_build_evaluation_report_excludes_secret_like_keys(tmp_path: Path) -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider(build_receptionist_analysis_payload()),
    )
    case = _build_case()
    evaluator = ProviderReceptionistAnalysisEvaluator(llm_analysis_service=service)
    summary = evaluator.evaluate([case])

    report = build_evaluation_report(
        summary=summary,
        dataset_path=tmp_path / "cases.jsonl",
        cases=[case],
        provider_case_outputs=evaluator.last_case_outputs,
    )

    assert_report_excludes_secret_like_keys(cast(dict[str, Any], report))


def test_provider_eval_builds_groq_service_from_settings_without_real_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        LLM_PRIMARY_PROVIDER="groq",
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
    )
    case = _build_case(message="Hello", intent="greeting")

    service = build_llm_receptionist_analysis_service_from_settings(settings)
    assert service is not None
    assert isinstance(service.primary_provider, GroqLLMProvider)

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            service.primary_provider,
            "complete",
            FakeLLMProvider().complete,
        )
        summary = run_provider_receptionist_analysis_evaluation(
            cases=[case],
            llm_analysis_service=service,
        )

    assert summary.passed_cases == 1
    assert summary.failed_cases == 0


def test_recorded_eval_stays_offline_without_groq_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    case = _build_case()
    dataset_path = tmp_path / "cases.jsonl"
    dataset_path.write_text(
        (
            "{"
            f'"id":"{case.id}",'
            f'"prompt_version":"{case.prompt_version}",'
            '"input":{"message":"Hello"},'
            '"expected":{"intent":"greeting","urgency":"normal","requires_human":false,'
            '"safety_flags":[],"extracted":{"specialty":null,"doctor":null,"date":null,"time":null}},'
            '"recorded_output":{"intent":"greeting","urgency":"normal","requires_human":false,'
            '"safety_flags":[],"extracted":{"specialty":null,"doctor":null,"date":null,"time":null}}'
            "}\n"
        ),
        encoding="utf-8",
    )

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            "app.ai.groq_provider.GroqLLMProvider",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("recorded eval must not instantiate Groq"),
            ),
        )
        cases = load_receptionist_analysis_eval_cases(dataset_path)
        summary = evaluate_receptionist_analysis_cases(cases, mode=EvaluationMode.RECORDED)

    assert summary.mode == EvaluationMode.RECORDED
    assert summary.passed_cases == 1
    assert summary.failed_cases == 0
