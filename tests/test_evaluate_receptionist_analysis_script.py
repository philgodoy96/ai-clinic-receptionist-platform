from __future__ import annotations

import json
from pathlib import Path

from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.llm_provider import LLMProvider
from app.ai.prompt_versions import get_current_receptionist_analysis_prompt_metadata
from scripts.evaluate_receptionist_analysis import main
from tests.llm_provider_test_helpers import (
    StaticContentLLMProvider,
    build_receptionist_analysis_payload,
)


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


def test_script_mode_provider_fails_without_allow_provider_calls(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    exit_code = main(["--dataset", str(dataset_path), "--mode", "provider"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "Error: provider mode requires --allow-provider-calls\n"
    assert captured.out == ""


def test_script_mode_provider_runs_with_fake_provider(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)
    monkeypatch.setattr(
        "scripts.evaluate_receptionist_analysis._create_llm_provider_for_evaluation",
        lambda: FakeLLMProvider(),
    )

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--mode",
            "provider",
            "--allow-provider-calls",
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Mode: provider" in captured.out
    assert "All cases passed." in captured.out


def test_script_output_writes_report_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    report_path = tmp_path / "reports" / "evaluation.json"
    _write_passing_dataset(dataset_path)
    monkeypatch.setattr(
        "scripts.evaluate_receptionist_analysis._create_llm_provider_for_evaluation",
        lambda: FakeLLMProvider(),
    )

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--mode",
            "provider",
            "--allow-provider-calls",
            "--output",
            str(report_path),
        ],
    )

    assert exit_code == 0
    assert report_path.exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "provider"
    assert report["cases"][0]["case_id"] == "passing_case"
    assert report["cases"][0]["input_message"] == "Hello"
    assert report["cases"][0]["provider"]["provider"] == "FakeLLMProvider"
    assert "retell_api_key" not in json.dumps(report).lower()
    assert "openai_api_key" not in json.dumps(report).lower()


def test_script_provider_mode_fail_on_errors_with_failing_stub(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "passing.jsonl"
    _write_passing_dataset(dataset_path)

    def failing_provider() -> LLMProvider:
        return StaticContentLLMProvider(
            build_receptionist_analysis_payload(intent="fallback"),
        )

    monkeypatch.setattr(
        "scripts.evaluate_receptionist_analysis._create_llm_provider_for_evaluation",
        failing_provider,
    )

    exit_code = main(
        [
            "--dataset",
            str(dataset_path),
            "--mode",
            "provider",
            "--allow-provider-calls",
            "--fail-on-errors",
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Failed cases:" in captured.out
    assert "[passing_case]" in captured.out
