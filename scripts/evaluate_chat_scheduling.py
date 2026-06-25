from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.evals.chat_scheduling import (
    ChatSchedulingEvaluationError,
    ChatSchedulingEvaluationScenario,
    ChatSchedulingEvaluationSummary,
    ChatSchedulingScenarioResult,
    build_chat_scheduling_evaluation_report,
    evaluate_chat_scheduling_scenarios,
    load_chat_scheduling_eval_scenarios,
)
from app.evals.chat_scheduling_fixtures import build_chat_scheduling_eval_service

DEFAULT_DATASET_PATH = Path("evals/chat_scheduling.jsonl")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_path = Path(args.dataset)

    try:
        scenarios = load_chat_scheduling_eval_scenarios(dataset_path)
    except ChatSchedulingEvaluationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.scenario_id is not None:
        filtered = _filter_scenarios(scenarios, args.scenario_id, dataset_path)
        if filtered is None:
            return 1
        scenarios = filtered

    summary = evaluate_chat_scheduling_scenarios(
        scenarios,
        service_builder=build_chat_scheduling_eval_service,
    )

    _print_summary(summary)
    _print_failed_scenarios(summary)

    if args.output is not None:
        _write_report(
            output_path=Path(args.output),
            summary=summary,
            dataset_path=dataset_path,
            scenarios=scenarios,
        )

    if args.fail_on_errors and summary.failed_scenarios > 0:
        return 1

    return 0


def _filter_scenarios(
    scenarios: tuple[ChatSchedulingEvaluationScenario, ...],
    scenario_id: str,
    dataset_path: Path,
) -> tuple[ChatSchedulingEvaluationScenario, ...] | None:
    selected = tuple(scenario for scenario in scenarios if scenario.name == scenario_id)
    if selected:
        return selected

    print(
        f"Error: scenario id '{scenario_id}' not found in dataset {dataset_path}",
        file=sys.stderr,
    )
    return None


def _write_report(
    *,
    output_path: Path,
    summary: ChatSchedulingEvaluationSummary,
    dataset_path: Path,
    scenarios: tuple[ChatSchedulingEvaluationScenario, ...],
) -> None:
    report = build_chat_scheduling_evaluation_report(
        summary=summary,
        dataset_path=dataset_path,
        scenarios=scenarios,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate multi-turn chat scheduling scenarios offline.",
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
        help="Exit with status code 1 when one or more scenarios fail.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to write a JSON evaluation report.",
    )
    parser.add_argument(
        "--scenario-id",
        type=str,
        default=None,
        help="Run only the scenario with this id.",
    )
    return parser.parse_args(argv)


def _print_summary(summary: ChatSchedulingEvaluationSummary) -> None:
    print("Chat Scheduling Evaluation Summary")
    print(f"Scenarios: {summary.total_scenarios}")
    print(f"Passed: {summary.passed_scenarios}")
    print(f"Failed: {summary.failed_scenarios}")


def _print_failed_scenarios(summary: ChatSchedulingEvaluationSummary) -> None:
    failed_scenarios = [
        result for result in summary.scenario_results if not result.passed
    ]
    if not failed_scenarios:
        return

    print()
    print("Failed scenarios:")
    print("-----------------")
    for result in failed_scenarios:
        _print_failed_scenario(result)


def _print_failed_scenario(result: ChatSchedulingScenarioResult) -> None:
    print()
    print(f"[{result.scenario_name}]")
    if result.failure_reason is not None and not result.step_results:
        print(f"  reason: {result.failure_reason}")
        return

    for step in result.step_results:
        if step.passed:
            continue

        print(f"  step {step.step_index} ({step.user_message!r})")
        if step.failure_reason is not None:
            print(f"    reason: {step.failure_reason}")

        for expectation in step.expectation_results:
            if expectation.passed:
                continue
            message_suffix = f" ({expectation.message})" if expectation.message else ""
            print(
                "    "
                f"{expectation.field}{message_suffix}: "
                f"expected {_format_value(expectation.expected)!r}, "
                f"got {_format_value(expectation.actual)!r}",
            )


def _format_value(value: object) -> str:
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
