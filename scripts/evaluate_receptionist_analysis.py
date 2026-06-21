from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.evals.receptionist_analysis import (
    EvaluationCaseResult,
    EvaluationError,
    EvaluationMode,
    EvaluationSummary,
    evaluate_receptionist_analysis_cases,
    load_receptionist_analysis_eval_cases,
)

DEFAULT_DATASET_PATH = Path("evals/receptionist_analysis.jsonl")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_path = Path(args.dataset)

    mode = EvaluationMode(args.mode)

    if mode == EvaluationMode.PROVIDER:
        print("Error: provider mode is not implemented yet", file=sys.stderr)
        return 1

    try:
        cases = load_receptionist_analysis_eval_cases(dataset_path)
    except EvaluationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    summary = evaluate_receptionist_analysis_cases(cases, mode=mode)
    _print_summary(summary, dataset_path)
    _print_failed_cases(summary)

    if args.fail_on_errors and summary.failed_cases > 0:
        return 1

    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate recorded receptionist analysis outputs offline.",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=str(DEFAULT_DATASET_PATH),
        help=f"Path to the JSONL evaluation dataset (default: {DEFAULT_DATASET_PATH}).",
    )
    parser.add_argument(
        "--fail-on-errors",
        action="store_true",
        help="Exit with status code 1 when one or more cases fail.",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in EvaluationMode],
        default=EvaluationMode.RECORDED.value,
        help=(
            "Evaluation run mode: recorded compares dataset recorded_output; "
            "provider will call the live LLM provider (not implemented yet)."
        ),
    )
    return parser.parse_args(argv)


def _print_summary(summary: EvaluationSummary, dataset_path: Path) -> None:
    print("Receptionist Analysis Evaluation")
    print("================================")
    print(f"Mode: {summary.mode.value}")
    print(f"Dataset: {dataset_path}")
    print(f"Total cases: {summary.total_cases}")
    print(f"Passed: {summary.passed_cases}")
    print(f"Failed: {summary.failed_cases}")
    print(f"Accuracy: {_format_percent(summary.accuracy)}")
    print()
    print(f"Intent accuracy: {_format_percent(summary.intent_accuracy)}")
    print(f"Urgency accuracy: {_format_percent(summary.urgency_accuracy)}")
    print(f"Requires human accuracy: {_format_percent(summary.requires_human_accuracy)}")
    print(f"Safety flag accuracy: {_format_percent(summary.safety_flag_accuracy)}")
    print(f"Extracted field accuracy: {_format_percent(summary.extracted_field_accuracy)}")
    print()
    print(f"Prompt versions seen: {', '.join(summary.prompt_versions) or 'none'}")
    print("Per prompt version:")
    for prompt_version in summary.prompt_versions:
        metrics = summary.metrics_by_prompt_version[prompt_version]
        print(
            f"  {metrics.prompt_version}: "
            f"{metrics.passed_cases}/{metrics.total_cases} passed "
            f"({_format_percent(metrics.accuracy)})",
        )


def _print_failed_cases(summary: EvaluationSummary) -> None:
    failed_cases = [case for case in summary.case_results if not case.passed]
    if not failed_cases:
        print()
        print("All cases passed.")
        return

    print()
    print("Failed cases:")
    print("-------------")
    for case in failed_cases:
        _print_failed_case(case)


def _print_failed_case(case: EvaluationCaseResult) -> None:
    print()
    print(f"[{case.case_id}]")
    if case.failure_reason is not None:
        print(f"  reason: {case.failure_reason}")
        return

    for field_result in case.field_results:
        if field_result.passed:
            continue
        print(
            "  - "
            f"{field_result.field}: "
            f"expected {_format_value(field_result.expected)!r}, "
            f"got {_format_value(field_result.actual)!r}",
        )


def _format_percent(value: float) -> str:
    return f"{value * 100:.1f}%"


def _format_value(value: object) -> str:
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
