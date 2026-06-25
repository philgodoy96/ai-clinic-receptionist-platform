from __future__ import annotations

import json
from pathlib import Path

from _pytest.capture import CaptureFixture

from scripts.evaluate_chat_scheduling import main
from tests.eval_report_test_helpers import (
    assert_report_excludes_secret_like_keys,
    is_secret_like_key,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_DATASET_PATH = REPO_ROOT / "evals" / "chat_scheduling.jsonl"


def _write_failing_dataset(path: Path) -> None:
    payload = {
        "id": "failing_scenario",
        "description": "Scenario with impossible expectation",
        "fixture": "availability_guidance",
        "tags": ["test"],
        "steps": [
            {
                "user_message": "I'd like to book an appointment with a cardiologist.",
                "expected_reply_contains_any": ["this phrase will never appear in the reply"],
            },
        ],
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_passing_dataset(path: Path) -> None:
    payload = {
        "id": "passing_scenario",
        "description": "Minimal passing scenario",
        "fixture": "availability_guidance",
        "tags": ["test"],
        "steps": [
            {
                "user_message": "I'd like to book an appointment with a cardiologist.",
                "expected_booking_confirmed": False,
                "expected_appointment_created": False,
            },
        ],
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_script_runs_committed_dataset_successfully(
    capsys: CaptureFixture[str],
) -> None:
    exit_code = main(["--dataset", str(COMMITTED_DATASET_PATH), "--fail-on-errors"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Chat Scheduling Evaluation Summary" in captured.out
    assert "Scenarios:" in captured.out
    assert "Passed:" in captured.out


def test_script_runs_against_temp_dataset_and_exits_success(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Chat Scheduling Evaluation Summary" in captured.out
    assert "Passed: 1" in captured.out
    assert "Failed: 0" in captured.out


def test_script_exits_non_zero_with_fail_on_errors_when_failures_exist(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "failing.jsonl"
    _write_failing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path), "--fail-on-errors"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Failed scenarios:" in captured.out
    assert "[failing_scenario]" in captured.out


def test_script_default_exit_is_zero_when_failures_exist_without_fail_on_errors(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "failing.jsonl"
    _write_failing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path)])

    assert exit_code == 0


def test_script_output_writes_json_report(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    report_path = tmp_path / "reports" / "chat-scheduling.json"
    _write_passing_dataset(dataset_path)

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--output",
            str(report_path),
        ],
    )

    assert exit_code == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["total_scenarios"] == 1
    assert report["passed_scenarios"] == 1
    assert report["failed_scenarios"] == 0
    assert report["dataset"] == str(dataset_path)
    assert len(report["scenarios"]) == 1
    assert report["scenarios"][0]["scenario_id"] == "passing_scenario"
    assert report["scenarios"][0]["passed"] is True
    assert report["scenarios"][0]["step_count"] == 1
    assert_report_excludes_secret_like_keys(report)
    assert is_secret_like_key("api_key")


def test_script_invalid_dataset_path_fails_clearly(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    missing_path = tmp_path / "missing.jsonl"

    exit_code = main(["--dataset", str(missing_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err.startswith("Error: Evaluation dataset not found:")
    assert str(missing_path) in captured.err


def test_script_scenario_id_runs_only_selected_scenario(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "multi.jsonl"
    first = {
        "id": "first_scenario",
        "description": "First",
        "fixture": "availability_guidance",
        "tags": ["test"],
        "steps": [
            {
                "user_message": "I'd like to book an appointment with a cardiologist.",
                "expected_booking_confirmed": False,
            },
        ],
    }
    second = {
        "id": "second_scenario",
        "description": "Second",
        "fixture": "availability_guidance",
        "tags": ["test"],
        "steps": [
            {
                "user_message": "Do you have anything next month with Dr. Michael Reed?",
                "expected_booking_confirmed": False,
                "expect_clarification_wording": True,
            },
        ],
    }
    dataset_path.write_text(
        json.dumps(first) + "\n" + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--scenario-id",
            "second_scenario",
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Scenarios: 1" in captured.out
    assert "Passed: 1" in captured.out


def test_script_unknown_scenario_id_fails_clearly(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--scenario-id",
            "does_not_exist",
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: scenario id 'does_not_exist' not found" in captured.err


def test_build_chat_scheduling_evaluation_report_excludes_reply_text(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    from app.evals.chat_scheduling import (
        ChatSchedulingEvaluationScenario,
        ChatSchedulingEvaluationStep,
        ChatSchedulingEvaluationSummary,
        ChatSchedulingScenarioResult,
        ChatSchedulingStepResult,
        build_chat_scheduling_evaluation_report,
    )
    from app.services.chat_receptionist import ChatReceptionistIntent

    scenario = ChatSchedulingEvaluationScenario(
        name="sample",
        description="Sample scenario",
        steps=(ChatSchedulingEvaluationStep(user_message="Hello"),),
        fixture="availability_guidance",
    )
    step_result = ChatSchedulingStepResult(
        step_index=0,
        user_message="Hello",
        passed=True,
        conversation_id=uuid4(),
        intent=ChatReceptionistIntent.GREETING,
        reply="This reply text should not appear in the JSON report.",
        booking_confirmed=False,
        appointment_id=None,
        expectation_results=(),
    )
    summary = ChatSchedulingEvaluationSummary(
        total_scenarios=1,
        passed_scenarios=1,
        failed_scenarios=0,
        scenario_results=(
            ChatSchedulingScenarioResult(
                scenario_name="sample",
                passed=True,
                conversation_id=step_result.conversation_id,
                step_results=(step_result,),
                fixture="availability_guidance",
            ),
        ),
    )

    report = build_chat_scheduling_evaluation_report(
        summary=summary,
        dataset_path=tmp_path / "dataset.jsonl",
        scenarios=(scenario,),
    )

    serialized = json.dumps(report)
    assert "This reply text should not appear in the JSON report." not in serialized
