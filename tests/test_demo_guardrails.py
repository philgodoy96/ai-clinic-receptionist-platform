from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from redis.exceptions import RedisError
from starlette.requests import Request

from app.core.config import Settings
from app.services.client_ip import get_client_ip
from app.services.clock import FixedClock
from app.services.demo_guardrails import (
    APPOINTMENTS_PER_DAY_PER_IP,
    CHAT_MESSAGES_PER_MINUTE_PER_IP,
    CONFIRMATION_EMAILS_PER_DAY_PER_IP,
    GLOBAL_APPOINTMENTS_PER_DAY,
    GLOBAL_CHAT_MESSAGES_PER_DAY,
    GLOBAL_CONFIRMATION_EMAILS_PER_DAY,
    DemoGuardrailLimitExceeded,
    DemoGuardrailService,
    DemoGuardrailStoreUnavailable,
)


def make_request(
    *,
    client_host: str | None = "203.0.113.10",
    headers: dict[str, str] | None = None,
) -> Request:
    raw_headers = [
        (name.lower().encode("utf-8"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    ]
    client = None if client_host is None else (client_host, 0)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": client,
    }
    return Request(scope)


def test_get_client_ip_uses_direct_client_host() -> None:
    request = make_request(client_host="203.0.113.10")

    assert get_client_ip(request, trust_proxy_headers=False) == "203.0.113.10"


def test_get_client_ip_returns_unknown_without_client() -> None:
    request = make_request(client_host=None)

    assert get_client_ip(request, trust_proxy_headers=False) == "unknown"


def test_get_client_ip_ignores_forwarded_for_when_proxy_headers_untrusted() -> None:
    request = make_request(
        client_host="203.0.113.10",
        headers={"X-Forwarded-For": "198.51.100.1, 10.0.0.1"},
    )

    assert get_client_ip(request, trust_proxy_headers=False) == "203.0.113.10"


def test_get_client_ip_uses_first_forwarded_for_ip_when_trusted() -> None:
    request = make_request(
        client_host="203.0.113.10",
        headers={"X-Forwarded-For": "198.51.100.1, 10.0.0.1"},
    )

    assert get_client_ip(request, trust_proxy_headers=True) == "198.51.100.1"


def test_get_client_ip_falls_back_to_client_host_when_forwarded_for_missing() -> None:
    request = make_request(client_host="203.0.113.10")

    assert get_client_ip(request, trust_proxy_headers=True) == "203.0.113.10"


def make_guardrail_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "PUBLIC_DEMO_GUARDRAILS_ENABLED": True,
        "DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP": 2,
        "DEMO_CHAT_MESSAGES_PER_DAY_PER_IP": 5,
        "DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP": 2,
        "DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP": 5,
        "DEMO_APPOINTMENTS_PER_DAY_PER_IP": 2,
        "DEMO_CONFIRMATION_EMAILS_PER_DAY_PER_IP": 2,
        "DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY": 3,
        "DEMO_GLOBAL_APPOINTMENTS_PER_DAY": 2,
        "DEMO_GLOBAL_CONFIRMATION_EMAILS_PER_DAY": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_service(
    redis_client: FakeRedisClient,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DemoGuardrailService:
    return DemoGuardrailService(
        redis_client=redis_client,
        settings=settings or make_guardrail_settings(),
        clock=FixedClock(now or datetime(2026, 6, 21, 12, 30, 45, tzinfo=UTC)),
    )


def test_disabled_guardrails_do_not_call_redis() -> None:
    redis_client = TrackingRedisClient()
    service = make_service(
        redis_client,
        settings=make_guardrail_settings(PUBLIC_DEMO_GUARDRAILS_ENABLED=False),
    )

    service.check_chat_message_allowed("203.0.113.10")
    service.check_retell_tool_allowed("203.0.113.10")
    service.check_appointment_creation_allowed("203.0.113.10")
    service.record_appointment_created("203.0.113.10")
    service.check_confirmation_email_allowed("203.0.113.10")
    service.record_confirmation_email_created("203.0.113.10")

    assert redis_client.calls == []


def test_first_chat_request_under_limit_is_allowed() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_chat_message_allowed("203.0.113.10")

    assert len(redis_client.values) == 3


def test_chat_request_above_per_minute_limit_raises() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_chat_message_allowed("203.0.113.10")
    service.check_chat_message_allowed("203.0.113.10")

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_chat_message_allowed("203.0.113.10")

    assert exc_info.value.limit_name == CHAT_MESSAGES_PER_MINUTE_PER_IP
    assert exc_info.value.retry_after_seconds == 15


def test_expiration_is_set_when_key_is_first_created() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_chat_message_allowed("203.0.113.10")

    minute_key = "demo_guardrail:chat:ip:203.0.113.10:minute:202606211230"
    day_key = "demo_guardrail:chat:ip:203.0.113.10:day:20260621"
    global_key = "demo_guardrail:chat:global:day:20260621"

    assert redis_client.expirations[minute_key] == 15
    assert redis_client.expirations[day_key] == 41355
    assert redis_client.expirations[global_key] == 41355


def test_global_daily_chat_limit_is_enforced_across_ips() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_chat_message_allowed("203.0.113.10")
    service.check_chat_message_allowed("203.0.113.11")
    service.check_chat_message_allowed("203.0.113.12")

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_chat_message_allowed("203.0.113.13")

    assert exc_info.value.limit_name == GLOBAL_CHAT_MESSAGES_PER_DAY


def test_appointment_check_and_record_enforces_daily_quota() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)
    ip = "203.0.113.10"

    service.check_appointment_creation_allowed(ip)
    service.record_appointment_created(ip)
    service.check_appointment_creation_allowed(ip)
    service.record_appointment_created(ip)

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_appointment_creation_allowed(ip)

    assert exc_info.value.limit_name == APPOINTMENTS_PER_DAY_PER_IP


def test_global_appointment_limit_is_enforced_after_record() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_appointment_creation_allowed("203.0.113.10")
    service.record_appointment_created("203.0.113.10")
    service.check_appointment_creation_allowed("203.0.113.11")
    service.record_appointment_created("203.0.113.11")

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_appointment_creation_allowed("203.0.113.12")

    assert exc_info.value.limit_name == GLOBAL_APPOINTMENTS_PER_DAY


def test_confirmation_email_check_and_record_enforces_daily_quota() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)
    ip = "203.0.113.10"

    service.check_confirmation_email_allowed(ip)
    service.record_confirmation_email_created(ip)
    service.check_confirmation_email_allowed(ip)
    service.record_confirmation_email_created(ip)

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_confirmation_email_allowed(ip)

    assert exc_info.value.limit_name == CONFIRMATION_EMAILS_PER_DAY_PER_IP


def test_global_confirmation_email_limit_is_enforced_after_record() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_confirmation_email_allowed("203.0.113.10")
    service.record_confirmation_email_created("203.0.113.10")
    service.check_confirmation_email_allowed("203.0.113.11")
    service.record_confirmation_email_created("203.0.113.11")

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_confirmation_email_allowed("203.0.113.12")

    assert exc_info.value.limit_name == GLOBAL_CONFIRMATION_EMAILS_PER_DAY


def test_redis_failure_raises_store_unavailable() -> None:
    service = make_service(FailingRedisClient())

    with pytest.raises(DemoGuardrailStoreUnavailable):
        service.check_chat_message_allowed("203.0.113.10")


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.expirations[key] = seconds
        return True

    def get(self, key: str) -> str | None:
        value = self.values.get(key)
        if value is None:
            return None
        return str(value)


class TrackingRedisClient(FakeRedisClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def incr(self, key: str) -> int:
        self.calls.append(f"incr:{key}")
        return super().incr(key)

    def expire(self, key: str, seconds: int) -> bool:
        self.calls.append(f"expire:{key}")
        return super().expire(key, seconds)

    def get(self, key: str) -> str | None:
        self.calls.append(f"get:{key}")
        return super().get(key)


class FailingRedisClient(FakeRedisClient):
    def incr(self, key: str) -> int:
        raise RedisError("redis unavailable")
