from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from api.dependencies import get_uow
from domain.enums import DeliveryStatus
from providers.webhooks import WebhookParseError, known_provider, parse_webhook
from repositories.uow import SqlAlchemyUnitOfWork

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookResult(BaseModel):
    processed: int


def _verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


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
            if update.delivered:
                if attempt.status is DeliveryStatus.DELIVERED:
                    processed += 1  # idempotent replay
                    continue
                if attempt.status is not DeliveryStatus.SENT:
                    continue
                attempt.mark_delivered(datetime.now(timezone.utc))
            else:
                if attempt.status is not DeliveryStatus.SENT:
                    continue
                attempt.mark_failed(update.reason or "provider reported failure")
            await uow.delivery_attempts.update(attempt)
            processed += 1
        await uow.commit()
    return WebhookResult(processed=processed)
