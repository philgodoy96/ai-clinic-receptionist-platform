from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest

from app.core.config import get_settings

pytest_plugins = ["tests.test_retell_tool_adapter"]

# Provider/config env vars that may leak from a developer's shell (e.g. a session
# that dot-sourced .env). They would otherwise take precedence over Settings'
# .env file and break hermetic Settings(_env_file=None) tests, so we strip them
# before each test to keep settings-related tests deterministic.
_AMBIENT_SETTINGS_ENV_VARS = (
    "APP_ENV",
    "APP_DEBUG",
    "LLM_ENABLED",
    "LLM_PROVIDER",
    "LLM_PRIMARY_PROVIDER",
    "LLM_FALLBACK_ENABLED",
    "LLM_FALLBACK_PROVIDER",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GROQ_RESPONSE_FORMAT",
    "CHAT_TURN_UNDERSTANDING_INTERPRETER",
    "RECEPTIONIST_RESPONSE_MODE",
    "EMAIL_PROVIDER",
    "RESEND_API_KEY",
    "EMAIL_FROM_ADDRESS",
    "EMAIL_REPLY_TO",
    "EMAIL_JOB_DISPATCH_ENABLED",
    "RETELL_ENABLED",
    "RETELL_WEB_CALL_ENABLED",
    "RETELL_API_KEY",
    "RETELL_AGENT_ID",
    "RETELL_AGENT_VERSION",
    "RETELL_WEBHOOK_SECRET",
    "RETELL_WEBHOOK_VERIFICATION_ENABLED",
    "RETELL_ALLOW_INSECURE_WEBHOOKS",
    "VOICE_PATIENT_INTAKE_MODE",
)


@pytest.fixture(autouse=True)
def isolate_settings_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Keep settings tests hermetic regardless of the developer's shell state."""

    for env_var in _AMBIENT_SETTINGS_ENV_VARS:
        monkeypatch.delenv(env_var, raising=False)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def block_live_retell_http() -> Generator[None, None, None]:
    """CI must never reach api.retellai.com; tests inject stub clients instead."""

    def _reject_live_retell_http(*args: object, **kwargs: object) -> None:
        request = args[0] if args else kwargs.get("request")
        url = getattr(request, "full_url", None) or str(request)
        if "api.retellai.com" in url:
            raise AssertionError(
                "live Retell HTTP is blocked in tests; inject a stub RetellWebCallClient",
            )

    with patch(
        "app.integrations.retell.web_call_client.urlopen",
        side_effect=_reject_live_retell_http,
    ):
        yield
