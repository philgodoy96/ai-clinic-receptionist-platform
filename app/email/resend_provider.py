from __future__ import annotations

from app.email.idempotency import normalize_resend_idempotency_key
from app.email.resend_client import ResendEmailClient, ResendEmailClientError
from app.email.types import EmailProviderError, EmailSendResult, OutboundEmailMessage


class ResendEmailProvider:
    def __init__(
        self,
        *,
        client: ResendEmailClient,
        from_address: str,
        reply_to: str | None = None,
    ) -> None:
        self._client = client
        self._from_address = from_address
        self._reply_to = reply_to

    def send(self, message: OutboundEmailMessage) -> EmailSendResult:
        payload: dict[str, object] = {
            "from": self._from_address,
            "to": [message.to],
            "subject": message.subject,
            "text": message.body,
        }

        if self._reply_to is not None:
            payload["reply_to"] = self._reply_to

        idempotency_key = (
            normalize_resend_idempotency_key(message.idempotency_key)
            if message.idempotency_key is not None
            else None
        )

        try:
            response = self._client.send_email(
                payload=payload,
                idempotency_key=idempotency_key,
            )
        except ResendEmailClientError as exc:
            raise EmailProviderError("resend email delivery failed") from exc

        provider_message_id = response.get("id")
        if not isinstance(provider_message_id, str) or not provider_message_id:
            raise EmailProviderError("resend email delivery returned no message id")

        return EmailSendResult(provider_message_id=provider_message_id)
