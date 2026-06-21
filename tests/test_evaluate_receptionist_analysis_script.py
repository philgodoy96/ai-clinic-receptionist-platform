from __future__ import annotations

import json
from pathlib import Path

from _pytest.capture import CaptureFixture

from scripts.evaluate_receptionist_analysis import main


def _write_passing_dataset(path: Path) -> None:
    payload = {
        "id": "passing_case",
        "input": {"message": "Hello"},
        "expected": {
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
        "recorded_output": {
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
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _write_failing_dataset(path: Path) -> None:
    payload = {
        "id": "failing_case",
        "input": {"message": "Hello"},
        "expected": {
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
        "recorded_output": {
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
    }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_script_runs_against_temp_dataset_and_exits_success(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Receptionist Analysis Evaluation" in captured.out
    assert "All cases passed." in captured.out


def test_script_exits_non_zero_with_fail_on_errors_when_failures_exist(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "failing.jsonl"
    _write_failing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path), "--fail-on-errors"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Failed cases:" in captured.out
    assert "[failing_case]" in captured.out
