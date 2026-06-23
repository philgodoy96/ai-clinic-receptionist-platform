from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import RedisError

from app.core.config import Settings
from app.services.clock import Clock, SystemClock

CHAT_MESSAGES_PER_MINUTE_PER_IP = "chat_messages_per_minute_per_ip"
CHAT_MESSAGES_PER_DAY_PER_IP = "chat_messages_per_day_per_ip"
GLOBAL_CHAT_MESSAGES_PER_DAY = "global_chat_messages_per_day"
RETELL_TOOL_CALLS_PER_MINUTE_PER_IP = "retell_tool_calls_per_minute_per_ip"
RETELL_TOOL_CALLS_PER_DAY_PER_IP = "retell_tool_calls_per_day_per_ip"
APPOINTMENTS_PER_DAY_PER_IP = "appointments_per_day_per_ip"
GLOBAL_APPOINTMENTS_PER_DAY = "global_appointments_per_day"
CONFIRMATION_EMAILS_PER_DAY_PER_IP = "confirmation_emails_per_day_per_ip"
GLOBAL_CONFIRMATION_EMAILS_PER_DAY = "global_confirmation_emails_per_day"


class DemoGuardrailLimitExceeded(Exception):
    def __init__(
        self,
        *,
        limit_name: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.limit_name = limit_name
        self.retry_after_seconds = retry_after_seconds
        super().__init__(limit_name)


class DemoGuardrailStoreUnavailable(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DemoGuardrailDecision:
    allowed: bool
    limit_name: str | None
    retry_after_seconds: int | None


class DemoGuardrailService:
    def __init__(
        self,
        redis_client: Any,
        settings: Settings,
        *,
        clock: Clock | None = None,
        key_prefix: str = "demo_guardrail",
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        self._clock = clock or SystemClock()
        self._key_prefix = key_prefix

    def check_chat_message_allowed(self, ip: str) -> None:
        if not self._guardrails_enabled():
            return

        now = self._clock.now()
        minute_bucket = self._minute_bucket(now)
        day_bucket = self._day_bucket(now)
        safe_ip = self._safe_ip(ip)

        self._consume(
            self._key("chat", "ip", safe_ip, "minute", minute_bucket),
            self._seconds_until_end_of_minute(now),
            self._settings.demo_chat_messages_per_minute_per_ip,
            CHAT_MESSAGES_PER_MINUTE_PER_IP,
            self._seconds_until_end_of_minute(now),
        )
        self._consume(
            self._key("chat", "ip", safe_ip, "day", day_bucket),
            self._seconds_until_end_of_utc_day(now),
            self._settings.demo_chat_messages_per_day_per_ip,
            CHAT_MESSAGES_PER_DAY_PER_IP,
            self._seconds_until_end_of_utc_day(now),
        )
        self._consume(
            self._key("chat", "global", "day", day_bucket),
            self._seconds_until_end_of_utc_day(now),
            self._settings.demo_global_chat_messages_per_day,
            GLOBAL_CHAT_MESSAGES_PER_DAY,
            self._seconds_until_end_of_utc_day(now),
        )

    def check_retell_tool_allowed(self, ip: str) -> None:
        if not self._guardrails_enabled():
            return

        now = self._clock.now()
        minute_bucket = self._minute_bucket(now)
        day_bucket = self._day_bucket(now)
        safe_ip = self._safe_ip(ip)

        self._consume(
            self._key("retell", "ip", safe_ip, "minute", minute_bucket),
            self._seconds_until_end_of_minute(now),
            self._settings.demo_retell_tool_calls_per_minute_per_ip,
            RETELL_TOOL_CALLS_PER_MINUTE_PER_IP,
            self._seconds_until_end_of_minute(now),
        )
        self._consume(
            self._key("retell", "ip", safe_ip, "day", day_bucket),
            self._seconds_until_end_of_utc_day(now),
            self._settings.demo_retell_tool_calls_per_day_per_ip,
            RETELL_TOOL_CALLS_PER_DAY_PER_IP,
            self._seconds_until_end_of_utc_day(now),
        )

    def check_voice_web_call_allowed(self, ip: str) -> None:
        self.check_retell_tool_allowed(ip)

    def check_appointment_creation_allowed(self, ip: str) -> None:
        if not self._guardrails_enabled():
            return

        now = self._clock.now()
        day_bucket = self._day_bucket(now)
        safe_ip = self._safe_ip(ip)
        retry_after = self._seconds_until_end_of_utc_day(now)

        self._assert_below_limit(
            self._current_count(self._key("appointment", "ip", safe_ip, "day", day_bucket)),
            self._settings.demo_appointments_per_day_per_ip,
            APPOINTMENTS_PER_DAY_PER_IP,
            retry_after,
        )
        self._assert_below_limit(
            self._current_count(self._key("appointment", "global", "day", day_bucket)),
            self._settings.demo_global_appointments_per_day,
            GLOBAL_APPOINTMENTS_PER_DAY,
            retry_after,
        )

    def record_appointment_created(self, ip: str) -> None:
        if not self._guardrails_enabled():
            return

        now = self._clock.now()
        day_bucket = self._day_bucket(now)
        safe_ip = self._safe_ip(ip)
        ttl = self._seconds_until_end_of_utc_day(now)

        self._increment(self._key("appointment", "ip", safe_ip, "day", day_bucket), ttl)
        self._increment(self._key("appointment", "global", "day", day_bucket), ttl)

    def check_confirmation_email_allowed(self, ip: str) -> None:
        if not self._guardrails_enabled():
            return

        now = self._clock.now()
        day_bucket = self._day_bucket(now)
        safe_ip = self._safe_ip(ip)
        retry_after = self._seconds_until_end_of_utc_day(now)

        self._assert_below_limit(
            self._current_count(self._key("email", "ip", safe_ip, "day", day_bucket)),
            self._settings.demo_confirmation_emails_per_day_per_ip,
            CONFIRMATION_EMAILS_PER_DAY_PER_IP,
            retry_after,
        )
        self._assert_below_limit(
            self._current_count(self._key("email", "global", "day", day_bucket)),
            self._settings.demo_global_confirmation_emails_per_day,
            GLOBAL_CONFIRMATION_EMAILS_PER_DAY,
            retry_after,
        )

    def record_confirmation_email_created(self, ip: str) -> None:
        if not self._guardrails_enabled():
            return

        now = self._clock.now()
        day_bucket = self._day_bucket(now)
        safe_ip = self._safe_ip(ip)
        ttl = self._seconds_until_end_of_utc_day(now)

        self._increment(self._key("email", "ip", safe_ip, "day", day_bucket), ttl)
        self._increment(self._key("email", "global", "day", day_bucket), ttl)

    def _guardrails_enabled(self) -> bool:
        return self._settings.public_demo_guardrails_enabled

    def _key(self, *parts: str) -> str:
        return ":".join((self._key_prefix, *parts))

    def _safe_ip(self, ip: str) -> str:
        return ip.replace(":", "_")

    def _minute_bucket(self, now: datetime) -> str:
        return now.astimezone(UTC).strftime("%Y%m%d%H%M")

    def _day_bucket(self, now: datetime) -> str:
        return now.astimezone(UTC).strftime("%Y%m%d")

    def _seconds_until_end_of_minute(self, now: datetime) -> int:
        utc_now = now.astimezone(UTC)
        next_minute = utc_now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        return max(1, int((next_minute - utc_now).total_seconds()))

    def _seconds_until_end_of_utc_day(self, now: datetime) -> int:
        utc_now = now.astimezone(UTC)
        next_day = utc_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return max(1, int((next_day - utc_now).total_seconds()))

    def _assert_below_limit(
        self,
        current: int,
        limit: int,
        limit_name: str,
        retry_after_seconds: int | None,
    ) -> None:
        if current >= limit:
            raise DemoGuardrailLimitExceeded(
                limit_name=limit_name,
                retry_after_seconds=retry_after_seconds,
            )

    def _consume(
        self,
        key: str,
        ttl_seconds: int,
        limit: int,
        limit_name: str,
        retry_after_seconds: int | None,
    ) -> None:
        count = self._increment(key, ttl_seconds)
        if count > limit:
            raise DemoGuardrailLimitExceeded(
                limit_name=limit_name,
                retry_after_seconds=retry_after_seconds,
            )

    def _current_count(self, key: str) -> int:
        try:
            raw_value = self._redis.get(key)
        except RedisError as exc:
            raise DemoGuardrailStoreUnavailable() from exc

        if raw_value is None:
            return 0

        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")

        return int(raw_value)

    def _increment(self, key: str, ttl_seconds: int) -> int:
        try:
            count = self._redis.incr(key)
            if count == 1:
                self._redis.expire(key, ttl_seconds)
        except RedisError as exc:
            raise DemoGuardrailStoreUnavailable() from exc

        return int(count)
