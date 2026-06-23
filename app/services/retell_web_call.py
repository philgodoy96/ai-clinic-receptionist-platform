from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.domain.retell_web_call import RetellWebCallRequest, RetellWebCallResult
from app.integrations.retell.web_call_client import (
    RetellWebCallClient,
    RetellWebCallClientError,
    RetellWebCallTimeoutError,
    UrllibRetellWebCallClient,
)
from app.services.clinic_time import ClinicContext, ClinicTimeService
from app.services.retell_call_lifecycle import DEFAULT_RETELL_PROVIDER

RETELL_WEB_CALL_SOURCE = "public_demo_web"
RETELL_WEB_CALL_ACCESS_TOKEN_TTL_SECONDS = 30


class RetellWebCallError(Exception):
    """Base exception for Retell web call creation errors."""


class RetellWebCallDisabledError(RetellWebCallError):
    """Raised when Retell web calls are disabled."""


class RetellWebCallConfigurationError(RetellWebCallError):
    """Raised when Retell web call configuration is incomplete."""


class RetellWebCallProviderError(RetellWebCallError):
    """Raised when Retell web call creation fails at the provider."""


class RetellWebCallService:
    def __init__(
        self,
        *,
        enabled: bool,
        api_key: str,
        agent_id: str,
        agent_version: str | None,
        timeout_seconds: int,
        clinic_time_service: ClinicTimeService,
        client: RetellWebCallClient | None = None,
    ) -> None:
        self._enabled = enabled
        self._api_key = api_key
        self._agent_id = agent_id
        self._agent_version = agent_version
        self._timeout_seconds = timeout_seconds
        self._clinic_time_service = clinic_time_service
        self._client = client

    def create_web_call(self, request: RetellWebCallRequest) -> RetellWebCallResult:
        self._ensure_ready()

        clinic_context = self._clinic_time_service.get_current_clinic_context()
        payload = self._build_provider_payload(request=request, clinic_context=clinic_context)

        try:
            provider_response = self._get_client().create_web_call(
                payload=payload,
                timeout_seconds=self._timeout_seconds,
            )
        except RetellWebCallTimeoutError as exc:
            raise RetellWebCallProviderError("retell web call request timed out") from exc
        except RetellWebCallClientError as exc:
            raise RetellWebCallProviderError("retell web call request failed") from exc

        return self._map_provider_response(
            provider_response=provider_response,
            conversation_id=request.conversation_id,
        )

    def _ensure_ready(self) -> None:
        if not self._enabled:
            raise RetellWebCallDisabledError("retell web calls are disabled")

        if not self._api_key.strip():
            raise RetellWebCallConfigurationError(
                "RETELL_API_KEY is required when RETELL_WEB_CALL_ENABLED is true",
            )

        if not self._agent_id.strip():
            raise RetellWebCallConfigurationError(
                "RETELL_AGENT_ID is required when RETELL_WEB_CALL_ENABLED is true",
            )

    def _get_client(self) -> RetellWebCallClient:
        if self._client is not None:
            return self._client
        return UrllibRetellWebCallClient(api_key=self._api_key)

    def _build_provider_payload(
        self,
        *,
        request: RetellWebCallRequest,
        clinic_context: ClinicContext,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "agent_id": self._agent_id.strip(),
            "metadata": self._build_metadata(request=request, clinic_context=clinic_context),
            "retell_llm_dynamic_variables": self._build_dynamic_variables(
                request=request,
                clinic_context=clinic_context,
            ),
        }

        resolved_agent_version = _resolve_agent_version(self._agent_version)
        if resolved_agent_version is not None:
            payload["agent_version"] = resolved_agent_version

        return payload

    @staticmethod
    def _build_metadata(
        *,
        request: RetellWebCallRequest,
        clinic_context: ClinicContext,
    ) -> dict[str, str]:
        metadata: dict[str, str] = {
            "source": RETELL_WEB_CALL_SOURCE,
            "clinic_timezone": clinic_context.clinic_timezone,
        }

        if request.demo_session_id is not None and request.demo_session_id.strip():
            metadata["demo_session_id"] = request.demo_session_id.strip()

        if request.conversation_id is not None:
            metadata["conversation_id"] = str(request.conversation_id)

        return metadata

    @staticmethod
    def _build_dynamic_variables(
        *,
        request: RetellWebCallRequest,
        clinic_context: ClinicContext,
    ) -> dict[str, str]:
        dynamic_variables = {
            "source": RETELL_WEB_CALL_SOURCE,
            "clinic_name": clinic_context.clinic_name,
            "clinic_timezone": clinic_context.clinic_timezone,
            "current_date": clinic_context.current_date,
            "current_weekday": clinic_context.current_weekday,
            "business_days": ",".join(clinic_context.business_days),
            "business_hours_start": clinic_context.business_hours_start,
            "business_hours_end": clinic_context.business_hours_end,
        }

        if request.demo_session_id is not None and request.demo_session_id.strip():
            dynamic_variables["demo_session_id"] = request.demo_session_id.strip()

        if request.conversation_id is not None:
            dynamic_variables["conversation_id"] = str(request.conversation_id)

        return dynamic_variables

    @staticmethod
    def _map_provider_response(
        *,
        provider_response: dict[str, Any],
        conversation_id: UUID | None,
    ) -> RetellWebCallResult:
        call_id = provider_response.get("call_id")
        access_token = provider_response.get("access_token")

        if not isinstance(call_id, str) or not call_id.strip():
            raise RetellWebCallProviderError("retell web call response missing call_id")

        if not isinstance(access_token, str) or not access_token.strip():
            raise RetellWebCallProviderError("retell web call response missing access_token")

        return RetellWebCallResult(
            provider=DEFAULT_RETELL_PROVIDER,
            call_id=call_id.strip(),
            access_token=access_token.strip(),
            expires_in_seconds=RETELL_WEB_CALL_ACCESS_TOKEN_TTL_SECONDS,
            conversation_id=conversation_id,
        )


def _resolve_agent_version(value: str | None) -> int | str | None:
    if value is None:
        return None

    normalized = value.strip()
    if not normalized:
        return None

    if normalized.isdigit():
        return int(normalized)

    return normalized


def create_retell_web_call_service_from_settings(
    settings: Settings,
    *,
    clinic_time_service: ClinicTimeService | None = None,
    client: RetellWebCallClient | None = None,
) -> RetellWebCallService:
    return RetellWebCallService(
        enabled=settings.retell_web_call_enabled,
        api_key=settings.retell_api_key,
        agent_id=settings.retell_agent_id,
        agent_version=settings.retell_agent_version,
        timeout_seconds=settings.retell_web_call_timeout_seconds,
        clinic_time_service=clinic_time_service or ClinicTimeService.from_settings(settings),
        client=client,
    )
