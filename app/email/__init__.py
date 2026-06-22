from app.email.factory import EmailProviderConfigurationError, create_email_provider_from_settings
from app.email.fake_provider import FakeEmailProvider
from app.email.resend_provider import ResendEmailProvider
from app.email.types import EmailProvider, EmailProviderError, EmailSendResult, OutboundEmailMessage

__all__ = [
    "EmailProvider",
    "EmailProviderConfigurationError",
    "EmailProviderError",
    "EmailSendResult",
    "FakeEmailProvider",
    "OutboundEmailMessage",
    "ResendEmailProvider",
    "create_email_provider_from_settings",
]
