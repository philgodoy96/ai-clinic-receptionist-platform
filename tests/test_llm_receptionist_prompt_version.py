from __future__ import annotations

import json
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture

from app.ai.fake_llm_provider import FakeLLMProvider
from app.ai.prompt_versions import (
    CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION,
    PROMPT_REGISTRY,
    PromptVersionNotFoundError,
    get_current_receptionist_analysis_prompt_metadata,
    get_prompt_metadata,
    list_prompt_versions,
)
from app.ai.receptionist_prompt import (
    build_receptionist_system_prompt,
    get_receptionist_analysis_prompt_version,
)
from app.ai.reliability import LLMFailureReason
from app.evals.receptionist_analysis import (
    EvaluationError,
    evaluate_receptionist_analysis_cases,
    load_receptionist_analysis_eval_cases,
)
from app.services.chat_receptionist import ChatMessageInput, ChatReceptionistService
from app.services.conversations import ConversationService
from app.services.date_parsing import NaturalLanguageDateParser
from app.services.llm_receptionist import (
    LLMReceptionistAnalysisService,
    ReceptionistAnalysisRequest,
)
from app.services.slot_filling import LLMChatSlotFillingService
from app.services.time_preferences import TimePreferenceParser
from scripts.evaluate_receptionist_analysis import main
from tests.llm_provider_test_helpers import RaisingLLMProvider, StaticContentLLMProvider
from tests.test_chat_receptionist_service import create_chat_receptionist_service
from tests.test_conversations import FakeConversationRepository
from tests.test_receptionist_analysis_evals import (
    _build_case,
    _case_payload,
    _matching_recorded_output,
)
from tests.test_scheduling_services import create_demo_scheduling_service


def expected_prompt_version() -> str:
    return get_current_receptionist_analysis_prompt_metadata().version


def _create_shadow_chat_service() -> ChatReceptionistService:
    repository = FakeConversationRepository()
    conversations = ConversationService(repository=repository)
    scheduling = create_demo_scheduling_service()
    llm_analysis = LLMReceptionistAnalysisService(provider=FakeLLMProvider())
    slot_filling = LLMChatSlotFillingService(
        scheduling=scheduling,
        date_parser=NaturalLanguageDateParser(),
        time_preference_parser=TimePreferenceParser(),
    )
    return create_chat_receptionist_service(
        conversations=conversations,
        scheduling=scheduling,
        llm_analysis=llm_analysis,
        slot_filling=slot_filling,
    )


def test_current_receptionist_prompt_version_is_stable() -> None:
    assert CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION == "receptionist-analysis-v2"
    assert (
        get_receptionist_analysis_prompt_version() == CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION
    )
    assert expected_prompt_version() == CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION


def test_prompt_registry_lookup_returns_current_metadata() -> None:
    metadata = get_prompt_metadata(CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION)

    assert metadata.name == "receptionist-analysis"
    assert metadata.schema_name == "ReceptionistLLMAnalysis"
    assert metadata.created_for == "chat_receptionist_assistive_analysis"
    assert metadata == get_current_receptionist_analysis_prompt_metadata()


def test_prompt_registry_lists_registered_versions() -> None:
    assert CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION in PROMPT_REGISTRY
    assert CURRENT_RECEPTIONIST_ANALYSIS_PROMPT_VERSION in list_prompt_versions()


def test_prompt_registry_rejects_unknown_version() -> None:
    with pytest.raises(PromptVersionNotFoundError, match="Unknown prompt version"):
        get_prompt_metadata("receptionist-analysis-v999")


def test_prompt_builder_exposes_current_prompt_version() -> None:
    version = get_receptionist_analysis_prompt_version()
    prompt = build_receptionist_system_prompt()

    assert version == expected_prompt_version()
    assert f"Prompt version: {version}" in prompt


def test_llm_analysis_success_result_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(provider=FakeLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is False
    assert result.prompt_version == expected_prompt_version()


def test_llm_analysis_provider_failure_fallback_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(provider=RaisingLLMProvider())

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.PROVIDER_EXCEPTION
    assert result.prompt_version == expected_prompt_version()


def test_llm_analysis_invalid_json_fallback_includes_prompt_version() -> None:
    service = LLMReceptionistAnalysisService(
        provider=StaticContentLLMProvider("this is not valid json"),
    )

    result = service.analyze_message(
        ReceptionistAnalysisRequest(
            user_message="Hello",
            conversation_context={},
        ),
    )

    assert result.used_fallback is True
    assert result.failure_reason == LLMFailureReason.JSON_PARSE_FAILED
    assert result.prompt_version == expected_prompt_version()


def test_chat_llm_shadow_analysis_includes_prompt_version() -> None:
    service = _create_shadow_chat_service()

    result = service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )
    shadow = result.assistant_message.message_metadata["llm_shadow_analysis"]

    assert shadow["prompt_version"] == expected_prompt_version()


def test_chat_metadata_excludes_full_prompt_text() -> None:
    service = _create_shadow_chat_service()
    system_prompt = build_receptionist_system_prompt()

    result = service.handle_message(
        ChatMessageInput(message="I need an appointment"),
    )
    metadata = result.assistant_message.message_metadata

    assert system_prompt not in json.dumps(metadata)
    assert "raw_prompt" not in metadata["llm_shadow_analysis"]
    assert "system_prompt" not in metadata["llm_shadow_analysis"]


def test_evaluation_loader_rejects_missing_prompt_version(tmp_path: Path) -> None:
    dataset_path = tmp_path / "missing_prompt_version.jsonl"
    payload = _case_payload()
    del payload["prompt_version"]
    dataset_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(
        EvaluationError,
        match="Field 'prompt_version' must be a non-empty string at line 1",
    ):
        load_receptionist_analysis_eval_cases(dataset_path)


def test_evaluation_loader_accepts_historical_prompt_version(tmp_path: Path) -> None:
    legacy_version = "receptionist-analysis-v0"
    dataset_path = tmp_path / "historical.jsonl"
    dataset_path.write_text(
        json.dumps(
            _case_payload(
                prompt_version=legacy_version,
                recorded_output=_matching_recorded_output(),
            ),
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_receptionist_analysis_eval_cases(dataset_path)

    assert len(cases) == 1
    assert cases[0].prompt_version == legacy_version
    assert cases[0].prompt_version != expected_prompt_version()


def test_evaluation_summary_lists_prompt_versions_seen() -> None:
    current_version = expected_prompt_version()
    legacy_version = "receptionist-analysis-v0"
    summary = evaluate_receptionist_analysis_cases(
        [
            _build_case(
                case_id="current",
                prompt_version=current_version,
                recorded_output=_matching_recorded_output(),
            ),
            _build_case(
                case_id="legacy",
                prompt_version=legacy_version,
                recorded_output=_matching_recorded_output(),
            ),
        ],
    )

    assert summary.prompt_versions == (legacy_version, current_version)


def test_evaluation_summary_includes_metrics_by_prompt_version() -> None:
    current_version = expected_prompt_version()
    legacy_version = "receptionist-analysis-v0"
    summary = evaluate_receptionist_analysis_cases(
        [
            _build_case(
                case_id="current_pass",
                prompt_version=current_version,
                recorded_output=_matching_recorded_output(),
            ),
            _build_case(
                case_id="current_fail",
                prompt_version=current_version,
                recorded_output=_matching_recorded_output(intent="fallback"),
            ),
            _build_case(
                case_id="legacy_pass",
                prompt_version=legacy_version,
                recorded_output=_matching_recorded_output(),
            ),
        ],
    )

    current_metrics = summary.metrics_by_prompt_version[current_version]
    legacy_metrics = summary.metrics_by_prompt_version[legacy_version]

    assert current_metrics.total_cases == 2
    assert current_metrics.passed_cases == 1
    assert current_metrics.failed_cases == 1
    assert current_metrics.accuracy == 0.5
    assert legacy_metrics.total_cases == 1
    assert legacy_metrics.passed_cases == 1
    assert legacy_metrics.accuracy == 1.0


def test_evaluate_receptionist_analysis_command_passes_with_fail_on_errors(
    capsys: CaptureFixture[str],
) -> None:
    exit_code = main(["--fail-on-errors"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "All cases passed." in captured.out
    assert expected_prompt_version() in captured.out
    assert "Per prompt version:" in captured.out
