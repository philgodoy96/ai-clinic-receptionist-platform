from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.ai.llm_provider import LLMProviderName
from app.api.routes import health as health_route
from app.core.config import Settings, get_settings
from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE_FILES = (
    PROJECT_ROOT / ".env.example",
    PROJECT_ROOT / ".env.demo.example",
)
ENV_DOCUMENTATION_FILES = (
    PROJECT_ROOT / "docs" / "configuration.md",
    PROJECT_ROOT / "docs" / "operations" / "public-demo-deployment.md",
)
COMMITTED_CONFIG_FILES = (PROJECT_ROOT / "docker-compose.yml",)

SECRET_LIKE_PATTERNS = (
    re.compile(r"gsk_[a-zA-Z0-9]{10,}"),
    re.compile(r"re_[a-zA-Z0-9]{10,}"),
    re.compile(r"whsec_[a-zA-Z0-9]{10,}"),
    re.compile(r"key_[a-zA-Z0-9]{10,}"),
    re.compile(r"sk-[a-zA-Z0-9]{10,}"),
)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def production_public_demo_env(**overrides: str) -> dict[str, str]:
    env = {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "PUBLIC_DEMO_MODE": "true",
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": "true",
        "DATABASE_URL": "postgresql+psycopg://clinic:clinic@db.example.com:5432/clinic_receptionist",
        "REDIS_URL": "redis://redis.example.com:6379/0",
    }
    env.update(overrides)
    return env


def test_production_public_demo_rejects_missing_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings(monkeypatch, **production_public_demo_env(DATABASE_URL=""))

    with pytest.raises(ValidationError, match="DATABASE_URL"):
        load_settings(
            monkeypatch,
            **production_public_demo_env(
                DATABASE_URL="postgresql+psycopg://clinic:clinic@localhost:5432/clinic_receptionist",
            ),
        )


def test_production_public_demo_rejects_disabled_guardrails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="PUBLIC_DEMO_GUARDRAILS_ENABLED"):
        load_settings(
            monkeypatch,
            **production_public_demo_env(PUBLIC_DEMO_GUARDRAILS_ENABLED="false"),
        )


def test_groq_provider_enabled_requires_api_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(
            monkeypatch,
            LLM_PROVIDER="groq",
            GROQ_MODEL="llama-3.3-70b-versatile",
            GROQ_API_KEY="",
        )

    with pytest.raises(ValidationError, match="GROQ_MODEL"):
        load_settings(
            monkeypatch,
            LLM_PROVIDER="groq",
            GROQ_API_KEY="gsk_test_key_for_validation",
            GROQ_MODEL="",
        )


def test_resend_provider_enabled_requires_api_key_and_from_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        load_settings(
            monkeypatch,
            EMAIL_PROVIDER="resend",
            EMAIL_FROM_ADDRESS="clinic-demo@example.test",
            RESEND_API_KEY="",
        )

    with pytest.raises(ValidationError, match="EMAIL_FROM_ADDRESS"):
        load_settings(
            monkeypatch,
            EMAIL_PROVIDER="resend",
            RESEND_API_KEY="re_test_key_for_validation",
            EMAIL_FROM_ADDRESS="",
        )


def test_retell_enabled_requires_webhook_verification_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_WEBHOOK_SECRET"):
        load_settings(
            monkeypatch,
            RETELL_ENABLED="true",
            RETELL_WEBHOOK_VERIFICATION_ENABLED="true",
            RETELL_WEBHOOK_SECRET="",
        )


def test_retell_insecure_mode_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="RETELL_ALLOW_INSECURE_WEBHOOKS"):
        load_settings(
            monkeypatch,
            APP_ENV="production",
            RETELL_ALLOW_INSECURE_WEBHOOKS="true",
        )


def test_local_fake_mode_does_not_require_real_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RETELL_WEBHOOK_SECRET", raising=False)

    settings = load_settings(
        monkeypatch,
        APP_ENV="local",
        LLM_PROVIDER="fake",
        EMAIL_PROVIDER="fake",
        RETELL_ENABLED="false",
    )

    assert settings.llm_provider == LLMProviderName.FAKE
    assert settings.email_provider == "fake"
    assert settings.groq_api_key == ""
    assert settings.resend_api_key == ""
    assert settings.retell_enabled is False


def test_worker_entrypoint_import_has_no_external_side_effects() -> None:
    module_name = "scripts.run_email_worker"
    sys.modules.pop(module_name, None)

    with (
        patch("pika.BlockingConnection", MagicMock()) as blocking_connection,
        patch("sqlalchemy.create_engine", MagicMock()) as create_engine,
        patch(
            "app.email.factory.create_email_provider_from_settings",
            side_effect=AssertionError("provider must not be created at import time"),
        ),
    ):
        module = importlib.import_module(module_name)

    assert callable(module.main)
    blocking_connection.assert_not_called()
    create_engine.assert_not_called()


def test_health_endpoint_does_not_call_providers() -> None:
    with (
        patch(
            "app.email.factory.create_email_provider_from_settings",
            side_effect=AssertionError("health must not create email provider"),
        ),
        patch(
            "app.ai.provider_factory.create_llm_provider_from_settings",
            side_effect=AssertionError("health must not create llm provider"),
        ),
    ):
        client = TestClient(create_app())
        health_response = client.get("/health")
        readiness_response = client.get("/health/dependencies")

    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    assert readiness_response.status_code == 200
    assert readiness_response.json()["status"] in {"ok", "degraded"}


def test_readiness_endpoint_handles_missing_dependencies_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app())

    def unavailable_database_check() -> None:
        raise RuntimeError("database is unavailable")

    monkeypatch.setattr(health_route, "check_database_connection", unavailable_database_check)

    response = client.get("/health/dependencies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["dependencies"]["database"]["status"] == "unavailable"
    assert payload["dependencies"]["database"]["detail"] == "RuntimeError"


def _line_looks_like_real_secret(value: str) -> bool:
    normalized_value = value.strip().strip('"').strip("'")
    if not normalized_value or normalized_value in {"...", "<your-groq-api-key>"}:
        return False
    if normalized_value.startswith("<") and normalized_value.endswith(">"):
        return False
    if "PASSWORD" in normalized_value or "USER:" in normalized_value:
        return False
    return any(pattern.search(normalized_value) for pattern in SECRET_LIKE_PATTERNS)


def _iter_env_assignment_values(path: Path) -> list[tuple[int, str]]:
    values: list[tuple[int, str]] = []
    in_env_block = False

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()

        if stripped.startswith("```env"):
            in_env_block = True
            continue
        if in_env_block and stripped.startswith("```"):
            in_env_block = False
            continue

        if path.suffix == ".example" or in_env_block:
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            _, _, value = stripped.partition("=")
            values.append((line_number, value))
            continue

        match = re.match(r"^[A-Z][A-Z0-9_]*\s*=\s*(.*)$", stripped)
        if match:
            values.append((line_number, match.group(1)))

    return values


@pytest.mark.parametrize("env_file", ENV_EXAMPLE_FILES)
def test_example_env_files_contain_no_real_secrets(env_file: Path) -> None:
    for line_number, value in _iter_env_assignment_values(env_file):
        assert not _line_looks_like_real_secret(value), (
            f"{env_file.name}:{line_number} looks like a real secret placeholder"
        )


@pytest.mark.parametrize("doc_file", ENV_DOCUMENTATION_FILES)
def test_example_env_docs_contain_no_real_secrets(doc_file: Path) -> None:
    for line_number, value in _iter_env_assignment_values(doc_file):
        assert not _line_looks_like_real_secret(value), (
            f"{doc_file.name}:{line_number} looks like a real secret placeholder"
        )


@pytest.mark.parametrize("config_file", COMMITTED_CONFIG_FILES)
def test_committed_config_files_contain_no_real_secrets(config_file: Path) -> None:
    for line_number, line in enumerate(
        config_file.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        _, _, value = stripped.partition(":")
        normalized_value = value.strip()
        if normalized_value.startswith("${") and normalized_value.endswith("}"):
            continue
        assert not _line_looks_like_real_secret(normalized_value), (
            f"{config_file.name}:{line_number} looks like a real secret placeholder"
        )
