from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai.receptionist_output import ReceptionistLLMAnalysis
from app.ai.reliability import LLMFailureReason
from app.evals.receptionist_analysis import (
    EvaluationCaseResult,
    EvaluationFieldResult,
    EvaluationMode,
    EvaluationSummary,
    PromptVersionEvaluationMetrics,
    ReceptionistAnalysisEvalCase,
    build_evaluation_summary,
    evaluate_receptionist_analysis_case_against_actual,
)
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)


@dataclass(frozen=True, slots=True)
class ProviderEvaluationCaseOutput:
    case_id: str
    provider: str
    model: str | None
    prompt_version: str
    actual: dict[str, object]
    failure_reason: str | None
    latency_ms: int | None
    input_tokens: int
    output_tokens: int


class ProviderReceptionistAnalysisEvaluator:
    def __init__(self, *, llm_analysis_service: LLMReceptionistAnalysisService) -> None:
        self._llm_analysis_service = llm_analysis_service
        self._last_case_outputs: tuple[ProviderEvaluationCaseOutput, ...] = ()

    @property
    def last_case_outputs(self) -> tuple[ProviderEvaluationCaseOutput, ...]:
        return self._last_case_outputs

    def evaluate(self, cases: list[ReceptionistAnalysisEvalCase]) -> EvaluationSummary:
        provider_name = _provider_name(self._llm_analysis_service)
        case_outputs: list[ProviderEvaluationCaseOutput] = []
        case_results: list[EvaluationCaseResult] = []

        for case in cases:
            try:
                analysis_result = self._llm_analysis_service.analyze_message(
                    ReceptionistAnalysisRequest(
                        user_message=case.input.message,
                        conversation_context={},
                    ),
                )
                actual = analysis_to_evaluation_actual(analysis_result.analysis)
                failure_reason = _provider_failure_reason(analysis_result.failure_reason)
                case_output = ProviderEvaluationCaseOutput(
                    case_id=case.id,
                    provider=provider_name,
                    model=analysis_result.model,
                    prompt_version=analysis_result.prompt_version,
                    actual=actual,
                    failure_reason=failure_reason,
                    latency_ms=analysis_result.latency_ms,
                    input_tokens=analysis_result.input_tokens,
                    output_tokens=analysis_result.output_tokens,
                )
                case_result = evaluate_receptionist_analysis_case_against_actual(
                    case,
                    actual,
                    prompt_version=analysis_result.prompt_version,
                )
            except Exception as exc:
                case_output = ProviderEvaluationCaseOutput(
                    case_id=case.id,
                    provider=provider_name,
                    model=None,
                    prompt_version=case.prompt_version,
                    actual={},
                    failure_reason=str(exc),
                    latency_ms=None,
                    input_tokens=0,
                    output_tokens=0,
                )
                case_result = EvaluationCaseResult(
                    case_id=case.id,
                    prompt_version=case.prompt_version,
                    passed=False,
                    field_results=(),
                    failure_reason=f"evaluation_error:{exc}",
                )

            case_outputs.append(case_output)
            case_results.append(case_result)

        self._last_case_outputs = tuple(case_outputs)
        return build_evaluation_summary(case_results, mode=EvaluationMode.PROVIDER)


def run_provider_receptionist_analysis_evaluation(
    *,
    cases: list[ReceptionistAnalysisEvalCase],
    llm_analysis_service: LLMReceptionistAnalysisService,
) -> EvaluationSummary:
    return ProviderReceptionistAnalysisEvaluator(
        llm_analysis_service=llm_analysis_service,
    ).evaluate(cases)


def analysis_to_evaluation_actual(analysis: ReceptionistLLMAnalysis) -> dict[str, object]:
    extracted = analysis.extracted
    return {
        "intent": analysis.intent.value,
        "urgency": analysis.urgency.value,
        "requires_human": analysis.requires_human,
        "safety_flags": list(analysis.safety_flags),
        "extracted": {
            "specialty": extracted.specialty,
            "doctor": extracted.doctor_name,
            "date": extracted.date,
            "time": extracted.time,
        },
    }


def _provider_name(service: LLMReceptionistAnalysisService) -> str:
    return type(service.provider).__name__


def _provider_failure_reason(failure_reason: LLMFailureReason) -> str | None:
    if failure_reason == LLMFailureReason.NONE:
        return None
    return failure_reason.value


def build_evaluation_report(
    *,
    summary: EvaluationSummary,
    dataset_path: Path,
    cases: list[ReceptionistAnalysisEvalCase],
    provider_case_outputs: tuple[ProviderEvaluationCaseOutput, ...] | None = None,
) -> dict[str, object]:
    cases_by_id = {case.id: case for case in cases}
    provider_outputs_by_id = {
        case_output.case_id: case_output for case_output in provider_case_outputs or ()
    }

    case_reports: list[dict[str, object]] = []
    for case_result in summary.case_results:
        eval_case = cases_by_id[case_result.case_id]
        case_report: dict[str, object] = {
            "case_id": case_result.case_id,
            "input_message": eval_case.input.message,
            "prompt_version": case_result.prompt_version,
            "passed": case_result.passed,
            "failure_reason": case_result.failure_reason,
            "field_results": [
                _field_result_to_dict(field_result) for field_result in case_result.field_results
            ],
        }
        provider_output = provider_outputs_by_id.get(case_result.case_id)
        if provider_output is not None:
            case_report["provider"] = _provider_output_to_dict(provider_output)
        case_reports.append(case_report)

    return {
        "mode": summary.mode.value,
        "dataset": str(dataset_path),
        "summary": _summary_metrics_to_dict(summary),
        "prompt_versions": list(summary.prompt_versions),
        "metrics_by_prompt_version": {
            prompt_version: _prompt_version_metrics_to_dict(metrics)
            for prompt_version, metrics in summary.metrics_by_prompt_version.items()
        },
        "cases": case_reports,
    }


def _summary_metrics_to_dict(summary: EvaluationSummary) -> dict[str, object]:
    return {
        "total_cases": summary.total_cases,
        "passed_cases": summary.passed_cases,
        "failed_cases": summary.failed_cases,
        "accuracy": summary.accuracy,
        "intent_accuracy": summary.intent_accuracy,
        "urgency_accuracy": summary.urgency_accuracy,
        "requires_human_accuracy": summary.requires_human_accuracy,
        "safety_flag_accuracy": summary.safety_flag_accuracy,
        "extracted_field_accuracy": summary.extracted_field_accuracy,
    }


def _prompt_version_metrics_to_dict(
    metrics: PromptVersionEvaluationMetrics,
) -> dict[str, object]:
    return {
        "prompt_version": metrics.prompt_version,
        "total_cases": metrics.total_cases,
        "passed_cases": metrics.passed_cases,
        "failed_cases": metrics.failed_cases,
        "accuracy": metrics.accuracy,
    }


def _field_result_to_dict(field_result: EvaluationFieldResult) -> dict[str, Any]:
    return {
        "field": field_result.field,
        "passed": field_result.passed,
        "expected": field_result.expected,
        "actual": field_result.actual,
    }


def _provider_output_to_dict(
    provider_output: ProviderEvaluationCaseOutput,
) -> dict[str, object]:
    return {
        "provider": provider_output.provider,
        "model": provider_output.model,
        "prompt_version": provider_output.prompt_version,
        "actual": provider_output.actual,
        "failure_reason": provider_output.failure_reason,
        "latency_ms": provider_output.latency_ms,
        "input_tokens": provider_output.input_tokens,
        "output_tokens": provider_output.output_tokens,
    }
