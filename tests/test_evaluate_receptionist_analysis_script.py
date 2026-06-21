from __future__ import annotations

import json
from pathlib import Path

from _pytest.capture import CaptureFixture

from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from scripts.evaluate_receptionist_analysis import main


def _default_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


def _write_passing_dataset(path: Path) -> None:
    payload = {
        "id": "passing_case",
        "prompt_version": _default_prompt_version(),
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
        "prompt_version": _default_prompt_version(),
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
    assert "Prompt versions seen:" in captured.out
    assert _default_prompt_version() in captured.out
    assert "Per prompt version:" in captured.out
    assert "All cases passed." in captured.out


def test_script_prints_per_prompt_version_summary_for_multiple_versions(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "multi_version.jsonl"
    current_version = _default_prompt_version()
    legacy_version = "receptionist-analysis-v0"
    dataset_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "current_case",
                        "prompt_version": current_version,
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
                    },
                ),
                json.dumps(
                    {
                        "id": "legacy_case",
                        "prompt_version": legacy_version,
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
                    },
                ),
            ],
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main(["--dataset", str(dataset_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert f"Prompt versions seen: {legacy_version}, {current_version}" in captured.out
    assert f"  {current_version}: 1/1 passed (100.0%)" in captured.out
    assert f"  {legacy_version}: 1/1 passed (100.0%)" in captured.out


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


def test_script_default_mode_is_recorded(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Mode: recorded" in captured.out


def test_script_mode_recorded_works(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path), "--mode", "recorded"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Mode: recorded" in captured.out
    assert "All cases passed." in captured.out


def test_script_mode_provider_fails_clearly(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path), "--mode", "provider"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "Error: provider mode is not implemented yet\n"
    assert captured.out == ""
