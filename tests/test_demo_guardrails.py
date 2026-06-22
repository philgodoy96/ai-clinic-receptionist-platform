from __future__ import annotations

import pytest

from app.services.client_ip import get_client_ip
from app.services.demo_guardrails import (
    APPOINTMENTS_PER_DAY_PER_IP,
    CHAT_MESSAGES_PER_DAY_PER_IP,
    CHAT_MESSAGES_PER_MINUTE_PER_IP,
    CONFIRMATION_EMAILS_PER_DAY_PER_IP,
    GLOBAL_APPOINTMENTS_PER_DAY,
    GLOBAL_CHAT_MESSAGES_PER_DAY,
    GLOBAL_CONFIRMATION_EMAILS_PER_DAY,
    RETELL_TOOL_CALLS_PER_DAY_PER_IP,
    RETELL_TOOL_CALLS_PER_MINUTE_PER_IP,
    DemoGuardrailLimitExceeded,
    DemoGuardrailStoreUnavailable,
)
from tests.demo_guardrail_support import (
    FailingRedisClient,
    FakeRedisClient,
    TrackingRedisClient,
    make_guardrail_settings,
    make_request,
    make_service,
)


def test_get_client_ip_uses_direct_client_host() -> None:
    request = make_request(client_host="203.0.113.10")

    assert get_client_ip(request, trust_proxy_headers=False) == "203.0.113.10"


def test_get_client_ip_returns_unknown_without_client() -> None:
    request = make_request(client_host=None)

    assert get_client_ip(request, trust_proxy_headers=False) == "unknown"


def test_get_client_ip_ignores_forwarded_for_by_default() -> None:
    request = make_request(
        client_host="203.0.113.10",
        headers={"X-Forwarded-For": "198.51.100.1, 10.0.0.1"},
    )

    assert get_client_ip(request, trust_proxy_headers=False) == "203.0.113.10"


def test_get_client_ip_uses_forwarded_for_when_trust_proxy_headers_enabled() -> None:
    request = make_request(
        client_host="203.0.113.10",
        headers={"X-Forwarded-For": "198.51.100.1, 10.0.0.1"},
    )

    assert get_client_ip(request, trust_proxy_headers=True) == "198.51.100.1"


def test_get_client_ip_falls_back_to_client_host_when_forwarded_for_missing() -> None:
    request = make_request(client_host="203.0.113.10")

    assert get_client_ip(request, trust_proxy_headers=True) == "203.0.113.10"


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


def test_per_minute_chat_limit_exceeded_raises() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_chat_message_allowed("203.0.113.10")
    service.check_chat_message_allowed("203.0.113.10")

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_chat_message_allowed("203.0.113.10")

    assert exc_info.value.limit_name == CHAT_MESSAGES_PER_MINUTE_PER_IP
    assert exc_info.value.retry_after_seconds == 15


def test_per_day_chat_limit_exceeded_raises() -> None:
    redis_client = FakeRedisClient()
    service = make_service(
        redis_client,
        settings=make_guardrail_settings(
            DEMO_CHAT_MESSAGES_PER_MINUTE_PER_IP=100,
            DEMO_CHAT_MESSAGES_PER_DAY_PER_IP=2,
            DEMO_GLOBAL_CHAT_MESSAGES_PER_DAY=100,
        ),
    )
    ip = "203.0.113.10"

    service.check_chat_message_allowed(ip)
    service.check_chat_message_allowed(ip)

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_chat_message_allowed(ip)

    assert exc_info.value.limit_name == CHAT_MESSAGES_PER_DAY_PER_IP


def test_global_daily_chat_limit_exceeded_raises() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)

    service.check_chat_message_allowed("203.0.113.10")
    service.check_chat_message_allowed("203.0.113.11")
    service.check_chat_message_allowed("203.0.113.12")

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_chat_message_allowed("203.0.113.13")

    assert exc_info.value.limit_name == GLOBAL_CHAT_MESSAGES_PER_DAY


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


def test_retell_per_minute_limit_exceeded_raises() -> None:
    redis_client = FakeRedisClient()
    service = make_service(redis_client)
    ip = "203.0.113.10"

    service.check_retell_tool_allowed(ip)
    service.check_retell_tool_allowed(ip)

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_retell_tool_allowed(ip)

    assert exc_info.value.limit_name == RETELL_TOOL_CALLS_PER_MINUTE_PER_IP


def test_retell_per_day_limit_exceeded_raises() -> None:
    redis_client = FakeRedisClient()
    service = make_service(
        redis_client,
        settings=make_guardrail_settings(
            DEMO_RETELL_TOOL_CALLS_PER_MINUTE_PER_IP=100,
            DEMO_RETELL_TOOL_CALLS_PER_DAY_PER_IP=2,
        ),
    )
    ip = "203.0.113.10"

    service.check_retell_tool_allowed(ip)
    service.check_retell_tool_allowed(ip)

    with pytest.raises(DemoGuardrailLimitExceeded) as exc_info:
        service.check_retell_tool_allowed(ip)

    assert exc_info.value.limit_name == RETELL_TOOL_CALLS_PER_DAY_PER_IP


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

    with pytest.raises(DemoGuardrailStoreUnavailable):
        service.check_appointment_creation_allowed("203.0.113.10")
