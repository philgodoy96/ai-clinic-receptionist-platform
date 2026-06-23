from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ai.llm_provider import LLMProviderName
from app.core.config import Settings, get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
ENV_DEMO_EXAMPLE = PROJECT_ROOT / ".env.demo.example"
CONFIGURATION_DOC = PROJECT_ROOT / "docs" / "configuration.md"

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


def parse_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator:
            env[key.strip()] = value.strip()
    return env


def load_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_example_env_file_names_are_referenced_in_docs() -> None:
    configuration_doc = CONFIGURATION_DOC.read_text(encoding="utf-8")

    assert ENV_EXAMPLE.name in configuration_doc
    assert ENV_DEMO_EXAMPLE.name in configuration_doc


@pytest.mark.parametrize("env_file", [ENV_EXAMPLE, ENV_DEMO_EXAMPLE])
def test_env_templates_do_not_contain_obvious_real_secrets(env_file: Path) -> None:
    for line_number, line in enumerate(env_file.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue

        _, _, value = stripped.partition("=")
        normalized_value = value.strip().strip('"').strip("'")
        if not normalized_value or normalized_value in {"...", "<your-groq-api-key>"}:
            continue
        if normalized_value.startswith("<") and normalized_value.endswith(">"):
            continue
        if "PASSWORD" in normalized_value or "USER:" in normalized_value:
            continue

        for pattern in SECRET_LIKE_PATTERNS:
            assert pattern.search(normalized_value) is None, (
                f"{env_file.name}:{line_number} looks like a real secret placeholder: {stripped}"
            )


def test_config_loads_minimal_local_env_from_example(monkeypatch: pytest.MonkeyPatch) -> None:
    example_env = parse_env_file(ENV_EXAMPLE)

    for key, value in example_env.items():
        monkeypatch.setenv(key, value)

    settings = Settings(_env_file=None)

    assert settings.app_env == "local"
    assert settings.app_debug is True
    assert settings.llm_provider == LLMProviderName.FAKE
    assert settings.email_provider == "fake"
    assert settings.public_demo_mode is False
    assert settings.retell_enabled is False


def test_config_rejects_incomplete_production_provider_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_env = {
        "APP_ENV": "production",
        "APP_DEBUG": "false",
        "PUBLIC_DEMO_MODE": "false",
        "LLM_PROVIDER": "fake",
        "EMAIL_PROVIDER": "fake",
    }

    with pytest.raises(ValidationError, match="GROQ_API_KEY"):
        load_settings(
            monkeypatch,
            **base_env,
            LLM_PRIMARY_PROVIDER="groq",
            GROQ_MODEL="llama-3.3-70b-versatile",
            GROQ_API_KEY="",
        )

    resend_env = {**base_env, "EMAIL_PROVIDER": "resend"}
    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        load_settings(
            monkeypatch,
            **resend_env,
            EMAIL_FROM_ADDRESS="clinic-demo@example.test",
            RESEND_API_KEY="",
        )

    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    with pytest.raises(ValidationError, match="RETELL_WEBHOOK_SECRET"):
        load_settings(
            monkeypatch,
            **base_env,
            RETELL_ENABLED="true",
            RETELL_WEBHOOK_VERIFICATION_ENABLED="true",
            RETELL_WEBHOOK_SECRET="",
        )
