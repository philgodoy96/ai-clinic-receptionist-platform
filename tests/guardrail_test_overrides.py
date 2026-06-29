from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.api.dependencies import get_demo_guardrail_service
from app.core.config import Settings
from app.services.clock import FixedClock
from app.services.demo_guardrails import DemoGuardrailService

_FIXED_GUARDRAIL_NOW = datetime(2026, 6, 21, 12, 30, 45, tzinfo=UTC)


class _NoopRedisClient:
    def incr(self, key: str) -> int:
        return 1

    def expire(self, key: str, seconds: int) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return None


def _make_disabled_guardrail_settings() -> Settings:
    return Settings(
        _env_file=None,
        PUBLIC_DEMO_MODE=False,
        PUBLIC_DEMO_GUARDRAILS_ENABLED=False,
        TRUST_PROXY_HEADERS=False,
    )


def disable_demo_guardrails_for_test_app(app: Any) -> None:
    """Disable public demo guardrails on a test FastAPI app."""

    def override_guardrails() -> DemoGuardrailService:
        return DemoGuardrailService(
            redis_client=_NoopRedisClient(),
            settings=_make_disabled_guardrail_settings(),
            clock=FixedClock(_FIXED_GUARDRAIL_NOW),
        )

    app.dependency_overrides[get_demo_guardrail_service] = override_guardrails
