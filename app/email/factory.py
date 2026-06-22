from __future__ import annotations

import logging

from app.core.config import Settings
from app.email.fake_provider import FakeEmailProvider
from app.email.resend_client import HttpResendEmailClient
from app.email.resend_provider import ResendEmailProvider
from app.email.types import EmailProvider

logger = logging.getLogger("app.email_provider")


class EmailProviderConfigurationError(RuntimeError):
    pass


def create_email_provider_from_settings(settings: Settings) -> EmailProvider:
    provider_name = settings.email_provider.strip().lower()

    logger.info(
        "email_provider_selected",
        extra={
            "event": "email_provider_selected",
            "provider": provider_name,
        },
    )

    if provider_name == "fake":
        return FakeEmailProvider()

    if provider_name == "resend":
        return ResendEmailProvider(
            client=HttpResendEmailClient(
                api_key=settings.resend_api_key,
                timeout_seconds=settings.email_provider_request_timeout_seconds,
            ),
            from_address=settings.email_from_address,
            reply_to=settings.email_reply_to or None,
        )

    raise EmailProviderConfigurationError(
        f"Unsupported email provider: {settings.email_provider}",
    )
