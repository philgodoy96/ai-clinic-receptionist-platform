from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai.fake_llm_provider import FakeLLMProvider
from app.evals.chat_scheduling import (
    ChatSchedulingEvaluationError,
    ChatSchedulingEvaluationScenario,
    format_chat_scheduling_scenario_failure,
    load_chat_scheduling_eval_scenarios,
    run_chat_scheduling_scenario,
)
from app.services.chat_receptionist import ChatReceptionistService
from tests.llm_provider_test_helpers import RaisingLLMProvider
from tests.test_chat_receptionist_service import (
    _create_availability_guidance_service,
    _create_hold_service,
    create_appointment_booking_service_for_scheduling,
)
from tests.test_chat_structured_slot_filling import create_structured_slot_filling_chat_service
from tests.test_scheduling_services import (
    create_demo_scheduling_service_with_emily_july_availability,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_DATASET_PATH = REPO_ROOT / "evals" / "chat_scheduling.jsonl"


def _build_chat_scheduling_eval_service(fixture: str) -> ChatReceptionistService:
    scheduling = create_demo_scheduling_service_with_emily_july_availability()

    if fixture == "availability_guidance":
        service, _repository, _hold_service = _create_availability_guidance_service(scheduling)
        return service

    if fixture == "structured_slot_filling":
        hold_service = _create_hold_service()
        appointment_booking = create_appointment_booking_service_for_scheduling(
            scheduling,
            hold_service,
        )
        return create_structured_slot_filling_chat_service(
            llm_provider=FakeLLMProvider(),
            scheduling=scheduling,
            appointment_booking=appointment_booking,
        )

    if fixture == "llm_failure_fallback":
        return create_structured_slot_filling_chat_service(
            llm_provider=RaisingLLMProvider(),
            scheduling=scheduling,
        )

    raise ValueError(f"Unsupported chat scheduling eval fixture: {fixture}")


def _scenario_payload(
    *,
    scenario_id: str,
    fixture: str = "availability_guidance",
    steps: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": scenario_id,
        "description": "Test scenario",
        "fixture": fixture,
        "tags": ["test"],
        "steps": steps
        or [
            {
                "user_message": "Hello there",
                "expected_booking_confirmed": False,
            },
        ],
    }


def test_load_committed_chat_scheduling_dataset() -> None:
    scenarios = load_chat_scheduling_eval_scenarios(COMMITTED_DATASET_PATH)

    assert scenarios
    assert len({scenario.name for scenario in scenarios}) == len(scenarios)


def test_load_chat_scheduling_eval_scenarios_rejects_missing_steps(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "missing_steps.jsonl"
    payload = _scenario_payload(scenario_id="missing_steps")
    del payload["steps"]
    dataset_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(
        ChatSchedulingEvaluationError,
        match="Field 'steps' must be a non-empty list at line 1",
    ):
        load_chat_scheduling_eval_scenarios(dataset_path)


def test_load_chat_scheduling_eval_scenarios_rejects_invalid_step_shape(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "invalid_step.jsonl"
    dataset_path.write_text(
        json.dumps(
            _scenario_payload(
                scenario_id="invalid_step",
                steps=[{"user_message": 123}],
            ),
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ChatSchedulingEvaluationError,
        match="Field 'user_message' must be a non-empty string at line 1 step 0",
    ):
        load_chat_scheduling_eval_scenarios(dataset_path)


def test_load_chat_scheduling_eval_scenarios_rejects_duplicate_scenario_id(
    tmp_path: Path,
) -> None:
    dataset_path = tmp_path / "duplicate_ids.jsonl"
    row = json.dumps(_scenario_payload(scenario_id="duplicate_case"))
    dataset_path.write_text(f"{row}\n{row}\n", encoding="utf-8")

    with pytest.raises(
        ChatSchedulingEvaluationError,
        match="Duplicate scenario id 'duplicate_case' at line 2",
    ):
        load_chat_scheduling_eval_scenarios(dataset_path)


@pytest.mark.parametrize(
    "scenario",
    load_chat_scheduling_eval_scenarios(COMMITTED_DATASET_PATH),
    ids=lambda scenario: scenario.name,
)
def test_committed_chat_scheduling_dataset_scenarios_pass(
    scenario: ChatSchedulingEvaluationScenario,
) -> None:
    service = _build_chat_scheduling_eval_service(scenario.fixture)
    result = run_chat_scheduling_scenario(service=service, scenario=scenario)

    assert result.passed, format_chat_scheduling_scenario_failure(
        scenario_name=scenario.name,
        result=result,
    )


def test_committed_dataset_expectations_remain_semantic() -> None:
    scenarios = load_chat_scheduling_eval_scenarios(COMMITTED_DATASET_PATH)

    for scenario in scenarios:
        for step in scenario.steps:
            assert not step.user_message.startswith("assert reply ==")
            if step.expected_reply_contains_any:
                assert all(
                    isinstance(phrase, str) and phrase
                    for phrase in step.expected_reply_contains_any
                )
