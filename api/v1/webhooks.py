from __future__ import annotations

import hashlib
import hmac
import json
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from api.dependencies import get_secret_box, get_uow
from providers.catalog import get_spec
from providers.receipts import ReceiptAuthError, ReceiptParseError, parse_receipt, supports_receipts, webhook_url
from providers.webhooks import WebhookParseError, known_provider, parse_webhook
from domain.value_objects.ids import InvalidIdError, ProviderId
from repositories.uow import SqlAlchemyUnitOfWork
from services.secrets import SecretBox, SecretsDecryptionError, SecretsNotConfigured
from telemetry.log_context import update_current
from services.receipts import apply_delivery_update, suppress_attempt_address

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookResult(BaseModel):
    processed: int


def _verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/p/{provider_id}", response_model=WebhookResult)
async def saved_provider_webhook(
    provider_id: str,
    request: Request,
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
    box: SecretBox = Depends(get_secret_box),
) -> WebhookResult:
    """
    Delivery receipts for one saved provider (Twilio, Resend, SendGrid).
    The URL names the provider; the callback is verified with *that*
    provider's own secrets, so one tenant's callback can never touch another's.
    Unauthenticated by design: the signature is the credential.
    """
    # Unknown / unsupported / unverifiable all look the same from outside.
    not_found = HTTPException(status_code=404, detail="Unknown webhook")
    try:
        pid = ProviderId.from_str(provider_id)
    except InvalidIdError:
        raise not_found

    async with uow:
        provider = await uow.providers.get_unscoped(pid)
        spec = get_spec(provider.channel, provider.name) if provider else None
        if provider is None or spec is None or not supports_receipts(spec.id):
            raise not_found
        try:
            secrets = box.decrypt(provider.secrets_encrypted)
        except (SecretsNotConfigured, SecretsDecryptionError):
            raise HTTPException(status_code=503, detail="Provider secrets unavailable")

        body = await request.body()
        try:
            events = await parse_receipt(
                spec.id,
                url=webhook_url(request.app.state.settings.api.public_url, pid),
                headers={k.lower(): v for k, v in request.headers.items()},
                body=body,
                config=provider.config,
                secrets=secrets,
                options={
                    "allow_insecure_sns": request.app.state.settings.webhook.allow_insecure_sns
                },
            )
        except ReceiptAuthError:
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
        except ReceiptParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        processed = 0
        for event in events:
            attempt = await uow.delivery_attempts.get_by_provider_message_id(
                provider.tenant_id, provider.id, event.provider_message_id, for_update=True
            )
            if attempt is not None:
                update_current(
                    tenant_id=attempt.tenant_id, notification_id=attempt.notification_id, attempt_id=attempt.id
                )
            if attempt is not None and event.suppress:
                await suppress_attempt_address(uow, attempt, event.suppress, event.reason)
            # Unknown ids are normal (the account also sends mail Pulse doesn't
            # know about); answer 200 so the provider doesn't disable the hook.
            if attempt is not None and await apply_delivery_update(
                uow, attempt, delivered=event.delivered, reason=event.reason
            ):
                processed += 1
        await uow.commit()
    return WebhookResult(processed=processed)


@router.post("/{provider}", response_model=WebhookResult)
async def provider_webhook(
    provider: str,
    request: Request,
    x_pulse_signature: str | None = Header(default=None, alias="X-Pulse-Signature"),
    uow: SqlAlchemyUnitOfWork = Depends(get_uow),
) -> WebhookResult:
    """
    Provider delivery callbacks (email/SMS DLRs). Status vocabularies are
    mapped onto our DeliveryAttempt states in providers.webhooks, not here.
    """
    if not known_provider(provider):
        raise HTTPException(status_code=404, detail=f"Unknown webhook provider '{provider}'")

    secret = request.app.state.settings.webhook.secrets.get(provider)
    if secret is None:
        raise HTTPException(status_code=404, detail=f"No webhook secret configured for '{provider}'")

    body = await request.body()
    if not _verify_signature(secret, body, x_pulse_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        updates = parse_webhook(provider, json.loads(body))
    except (WebhookParseError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    processed = 0
    async with uow:
        for update in updates:
            attempt = await uow.delivery_attempts.get_by_dispatch(
                update.notification_id, update.recipient_id, update.channel
            )
            if attempt is None:
                continue
            if not await apply_delivery_update(
                uow, attempt, delivered=update.delivered, reason=update.reason
            ):
                continue
            processed += 1
        await uow.commit()
    return WebhookResult(processed=processed)
