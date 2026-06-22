"""Backward-compatible email delivery exports."""

from app.email.fake_provider import FakeEmailProvider as FakeEmailDeliveryProvider
from app.email.types import EmailProvider as EmailDeliveryProvider
from app.email.types import EmailProviderError as EmailDeliveryError
from app.email.types import OutboundEmailMessage as EmailMessage

__all__ = [
    "EmailDeliveryError",
    "EmailDeliveryProvider",
    "EmailMessage",
    "FakeEmailDeliveryProvider",
]
