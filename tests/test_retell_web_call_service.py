from __future__ import annotations

from collections.abc import Generator
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.domain.retell_web_call import RetellWebCallRequest
from app.integrations.retell.web_call_client import RetellWebCallTimeoutError
from app.schemas.retell_web_call import retell_web_call_result_to_response
from app.services.clinic_time import ClinicTimeService
from app.services.retell_web_call import (
    RETELL_WEB_CALL_ACCESS_TOKEN_TTL_SECONDS,
    RetellWebCallConfigurationError,
    RetellWebCallDisabledError,
    RetellWebCallProviderError,
    RetellWebCallService,
    create_retell_web_call_service_from_settings,
)

TEST_AGENT_ID = "agent_test_123"
TEST_API_KEY = "key_test_secret_value"
TEST_CALL_ID = "call_test_abc"
TEST_ACCESS_TOKEN = "access_token_test_value"


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


class StubRetellWebCallClient:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.last_payload: dict[str, Any] | None = None
        self.last_timeout_seconds: int | None = None

    def create_web_call(
        self,
        *,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        self.last_payload = payload
        self.last_timeout_seconds = timeout_seconds
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def build_service(
    *,
    enabled: bool = True,
    api_key: str = TEST_API_KEY,
    agent_id: str = TEST_AGENT_ID,
    agent_version: str | None = None,
    timeout_seconds: int = 10,
    client: StubRetellWebCallClient | None = None,
) -> RetellWebCallService:
    return RetellWebCallService(
        enabled=enabled,
        api_key=api_key,
        agent_id=agent_id,
        agent_version=agent_version,
        timeout_seconds=timeout_seconds,
        clinic_time_service=ClinicTimeService(
            clinic_name="Demo Clinic",
            timezone="America/New_York",
            business_days="monday,tuesday,wednesday,thursday,friday",
            business_hours_start="09:00",
            business_hours_end="17:00",
        ),
        client=client,
    )


def test_disabled_config_rejects() -> None:
    service = build_service(enabled=False)

    with pytest.raises(RetellWebCallDisabledError, match="disabled"):
        service.create_web_call(RetellWebCallRequest())


def test_missing_api_key_rejected_when_enabled() -> None:
    service = build_service(api_key="")

    with pytest.raises(RetellWebCallConfigurationError, match="RETELL_API_KEY"):
        service.create_web_call(RetellWebCallRequest())


def test_missing_agent_id_rejected_when_enabled() -> None:
    service = build_service(agent_id="")

    with pytest.raises(RetellWebCallConfigurationError, match="RETELL_AGENT_ID"):
        service.create_web_call(RetellWebCallRequest())


def test_settings_reject_missing_api_key_when_web_call_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_API_KEY"):
        load_settings(
            monkeypatch,
            RETELL_WEB_CALL_ENABLED="true",
            RETELL_AGENT_ID=TEST_AGENT_ID,
            RETELL_API_KEY="",
        )


def test_settings_reject_missing_agent_id_when_web_call_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_AGENT_ID"):
        load_settings(
            monkeypatch,
            RETELL_WEB_CALL_ENABLED="true",
            RETELL_API_KEY=TEST_API_KEY,
            RETELL_AGENT_ID="",
        )


def test_service_maps_provider_response_safely() -> None:
    conversation_id = uuid4()
    client = StubRetellWebCallClient(
        response={
            "call_type": "web_call",
            "call_id": TEST_CALL_ID,
            "access_token": TEST_ACCESS_TOKEN,
            "agent_id": TEST_AGENT_ID,
            "agent_name": "Hidden Agent",
            "agent_version": 2,
            "call_status": "registered",
            "metadata": {"internal_customer_id": "cust_secret"},
        },
    )
    service = build_service(client=client)

    result = service.create_web_call(
        RetellWebCallRequest(
            demo_session_id="demo-session-1",
            conversation_id=conversation_id,
        ),
    )

    assert result.provider == "retell"
    assert result.call_id == TEST_CALL_ID
    assert result.access_token == TEST_ACCESS_TOKEN
    assert result.expires_in_seconds == RETELL_WEB_CALL_ACCESS_TOKEN_TTL_SECONDS
    assert result.conversation_id == conversation_id

    response = retell_web_call_result_to_response(result)
    payload = response.model_dump()
    assert set(payload) == {
        "provider",
        "call_id",
        "access_token",
        "expires_in_seconds",
        "conversation_id",
    }
    assert TEST_API_KEY not in payload.values()
    assert "agent_name" not in payload
    assert "metadata" not in payload

    assert client.last_payload is not None
    assert client.last_payload["agent_id"] == TEST_AGENT_ID
    assert client.last_payload["metadata"]["source"] == "public_demo_web"
    assert client.last_payload["metadata"]["demo_session_id"] == "demo-session-1"
    assert client.last_payload["metadata"]["conversation_id"] == str(conversation_id)
    assert client.last_payload["retell_llm_dynamic_variables"]["clinic_timezone"] == (
        "America/New_York"
    )
    assert client.last_payload["retell_llm_dynamic_variables"]["source"] == "public_demo_web"


def test_provider_timeout_handled() -> None:
    client = StubRetellWebCallClient(error=RetellWebCallTimeoutError("timed out"))
    service = build_service(client=client)

    with pytest.raises(RetellWebCallProviderError, match="timed out"):
        service.create_web_call(RetellWebCallRequest())


def test_no_secrets_in_result() -> None:
    client = StubRetellWebCallClient(
        response={
            "call_id": TEST_CALL_ID,
            "access_token": TEST_ACCESS_TOKEN,
            "agent_id": TEST_AGENT_ID,
            "metadata": {"api_key": TEST_API_KEY},
        },
    )
    service = build_service(client=client)

    result = service.create_web_call(RetellWebCallRequest())
    serialized = retell_web_call_result_to_response(result).model_dump_json()

    assert TEST_API_KEY not in serialized
    assert "agent_id" not in serialized
    assert "metadata" not in serialized


def test_create_service_from_settings_uses_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = load_settings(
        monkeypatch,
        RETELL_WEB_CALL_ENABLED="true",
        RETELL_API_KEY=TEST_API_KEY,
        RETELL_AGENT_ID=TEST_AGENT_ID,
        RETELL_WEB_CALL_TIMEOUT_SECONDS="15",
    )
    client = StubRetellWebCallClient(
        response={
            "call_id": TEST_CALL_ID,
            "access_token": TEST_ACCESS_TOKEN,
        },
    )
    service = create_retell_web_call_service_from_settings(settings, client=client)

    service.create_web_call(RetellWebCallRequest())

    assert client.last_timeout_seconds == 15


def test_optional_agent_version_is_forwarded_to_provider() -> None:
    client = StubRetellWebCallClient(
        response={
            "call_id": TEST_CALL_ID,
            "access_token": TEST_ACCESS_TOKEN,
        },
    )
    service = build_service(agent_version="3", client=client)

    service.create_web_call(RetellWebCallRequest())

    assert client.last_payload is not None
    assert client.last_payload["agent_version"] == 3


def test_invalid_provider_response_raises_without_leaking_payload() -> None:
    client = StubRetellWebCallClient(response={"call_id": "", "access_token": TEST_ACCESS_TOKEN})
    service = build_service(client=client)

    with pytest.raises(RetellWebCallProviderError, match="missing call_id"):
        service.create_web_call(RetellWebCallRequest())


def test_conversation_id_omitted_from_result_when_not_requested() -> None:
    client = StubRetellWebCallClient(
        response={
            "call_id": TEST_CALL_ID,
            "access_token": TEST_ACCESS_TOKEN,
        },
    )
    service = build_service(client=client)

    result = service.create_web_call(RetellWebCallRequest())

    assert result.conversation_id is None


def test_retell_web_call_module_import_has_no_network_side_effects() -> None:
    import app.integrations.retell.web_call_client as web_call_client_module
    import app.services.retell_web_call as retell_web_call_module

    assert callable(retell_web_call_module.RetellWebCallService)
    assert callable(web_call_client_module.UrllibRetellWebCallClient)
