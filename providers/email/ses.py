from __future__ import annotations

import asyncio
from typing import Any

from providers.email.base import EmailProvider
from services.ports.providers import OutboundMessage, ProviderSendError, SendResult

_AUTH = {
    "InvalidClientTokenId", "UnrecognizedClientException", "SignatureDoesNotMatch",
    "AccessDenied", "AccessDeniedException", "AccountSuspendedException",
    "SendingPausedException", "ExpiredToken", "ExpiredTokenException",
}
_THROTTLE_OR_OUTAGE = {
    "Throttling", "ThrottlingException", "TooManyRequestsException", "RequestLimitExceeded",
    "LimitExceededException", "ServiceUnavailable", "InternalFailure", "InternalServerError",
    "RequestTimeout", "RequestTimeoutException",
}
_BAD_REQUEST = {"BadRequestException", "ValidationError", "InvalidParameterValue"}


def build_client(region: str, access_key_id: str, secret_access_key: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "sesv2",
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        # Retries are Pulse's job (visible, backed-off, failover-aware).
        config=Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 0}),
    )


def classify_ses_error(exc: BaseException) -> ProviderSendError:
    from botocore.exceptions import BotoCoreError, ClientError

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        text = f"SES {code}: {exc.response.get('Error', {}).get('Message', '')}".strip()
        if code in _AUTH:
            return ProviderSendError(text, retryable=False, failover=True)
        if code in _THROTTLE_OR_OUTAGE:
            return ProviderSendError(text, retryable=True, failover=True)
        if code in _BAD_REQUEST:
            return ProviderSendError(text, retryable=False, failover=False)
        if code == "MessageRejected":
            # Unverified sender, sandbox recipient, bad content: this provider
            # refused it, so another one may still accept it.
            return ProviderSendError(text, retryable=False, failover=True)
        return ProviderSendError(text, retryable=True, failover=True)
    if isinstance(exc, BotoCoreError):  # connect/read timeouts, DNS, endpoint errors
        return ProviderSendError(f"SES unreachable: {exc}", retryable=True, failover=True)
    return ProviderSendError(f"SES error: {exc!r}", retryable=True, failover=True)


class SesEmailProvider(EmailProvider):
    """
    Amazon SES v2. Delivery and bounce events come back through SNS: once the
    tenant configures `sns_topic_arn`, delivery is confirmed by those events;
    until then `sent` is final.
    """

    name = "ses"

    def __init__(
        self,
        *,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        from_address: str,
        configuration_set: str | None = None,
        receipts: bool = False,
        client: Any = None,
    ) -> None:
        self.confirms_delivery = receipts
        self._from = from_address
        self._configuration_set = configuration_set
        self._client = client or build_client(region, access_key_id, secret_access_key)

    def _send_sync(self, message: OutboundMessage) -> str:
        body: dict[str, Any] = {"Text": {"Data": message.body_text or " ", "Charset": "UTF-8"}}
        if message.body_html:
            body["Html"] = {"Data": message.body_html, "Charset": "UTF-8"}
        params: dict[str, Any] = {
            "FromEmailAddress": self._from,
            "Destination": {"ToAddresses": [message.to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject or "(no subject)", "Charset": "UTF-8"},
                    "Body": body,
                }
            },
        }
        if self._configuration_set:
            params["ConfigurationSetName"] = self._configuration_set
        return self._client.send_email(**params)["MessageId"]

    async def send(self, message: OutboundMessage) -> SendResult:
        try:
            message_id = await asyncio.to_thread(self._send_sync, message)
        except Exception as exc:
            raise classify_ses_error(exc) from exc
        return SendResult(provider_message_id=message_id)


async def check_ses(
    region: str, access_key_id: str, secret_access_key: str, client: Any = None
) -> tuple[bool, str]:
    def _probe() -> tuple[bool, str]:
        c = client or build_client(region, access_key_id, secret_access_key)
        try:
            account = c.get_account()
        except Exception as exc:
            err = classify_ses_error(exc)
            return False, str(err)
        if not account.get("SendingEnabled", True):
            return False, "Credentials are valid, but sending is disabled for this SES account"
        mode = "sandbox (verified recipients only)" if not account.get("ProductionAccessEnabled") else "production"
        return True, f"Connected to SES in {region}; account is in {mode}"

    return await asyncio.to_thread(_probe)
