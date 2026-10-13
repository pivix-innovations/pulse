from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from typing import Any

from providers.push.base import PushProvider
from services.ports.providers import OutboundMessage, ProviderSendError, SendResult

_apps: dict[str, Any] = {}
_apps_lock = threading.Lock()


def _get_app(credentials_json: str | None, credentials_path: str | None) -> Any:
    """
    One firebase_admin app per distinct credential, created lazily. Each
    saved FCM provider (potentially a different Firebase project per tenant)
    gets its own app instead of sharing one process-global default.
    """
    import firebase_admin
    from firebase_admin import credentials

    material = credentials_json or credentials_path or "<application-default>"
    name = hashlib.sha256(material.encode()).hexdigest()[:16]
    with _apps_lock:
        app = _apps.get(name)
        if app is None:
            if credentials_json:
                cred = credentials.Certificate(json.loads(credentials_json))
            elif credentials_path:
                cred = credentials.Certificate(credentials_path)
            else:
                cred = credentials.ApplicationDefault()
            app = firebase_admin.initialize_app(cred, name=name)
            _apps[name] = app
        return app


# Outcomes of one token's send, deciding what the provider reports.
STALE = "stale"  # the token is dead; drop it, other providers can't help
TRANSIENT = "transient"  # provider/network trouble; retry and fail over
AUTH = "auth"  # our credentials are bad; retrying won't help, another provider might
PERMANENT = "permanent"  # the request itself is invalid (not a dead token); nobody can send it


def classify_fcm_error(exc: BaseException) -> str:
    from firebase_admin import exceptions, messaging

    if isinstance(exc, (messaging.UnregisteredError, messaging.SenderIdMismatchError)):
        return STALE
    if isinstance(exc, exceptions.InvalidArgumentError):
        # Malformed token *or* payload; FCM doesn't say which, so never prune on it.
        return PERMANENT
    if isinstance(exc, (messaging.ThirdPartyAuthError, exceptions.PermissionDeniedError,
                        exceptions.UnauthenticatedError)):
        return AUTH
    return TRANSIENT  # unavailable, internal, quota, deadline, network, unknown


class FcmPushProvider(PushProvider):
    """
    FCM HTTP v1 driver sending DATA messages (not notification messages),
    so app code runs on receipt and can call the ack endpoint -- that ack
    is what moves the attempt from `sent` to `delivered`.

    Dead tokens are reported back (`invalid_destinations`) for pruning; only
    provider-side trouble (outage, quota, bad credentials) counts as a
    provider failure that the workflow's chain may fail over from.
    """

    name = "fcm"
    confirms_delivery = True

    def __init__(
        self,
        *,
        credentials_json: str | None = None,
        credentials_path: str | None = None,
    ) -> None:
        self._credentials_json = credentials_json
        self._credentials_path = credentials_path

    def _send_one(self, token: str, data: dict[str, str]) -> str:
        from firebase_admin import messaging

        app = _get_app(self._credentials_json, self._credentials_path)
        return messaging.send(messaging.Message(data=data, token=token), app=app)

    async def send(self, message: OutboundMessage) -> SendResult:
        tokens: list[str] = message.metadata.get("device_tokens", [])
        if not tokens:
            raise ProviderSendError("No device tokens for recipient", retryable=False, failover=False)

        data = {
            "attempt_id": str(message.metadata.get("attempt_id") or ""),
            "notification_id": str(message.metadata.get("notification_id") or ""),
            "title": message.subject or "",
            "body": message.body_text,
        }

        def _send_all() -> tuple[list[str], dict[str, list[str]]]:
            accepted: list[str] = []
            failed: dict[str, list[str]] = {STALE: [], TRANSIENT: [], AUTH: [], PERMANENT: []}
            errors: list[str] = []
            for token in tokens:
                try:
                    accepted.append(self._send_one(token, data))
                except Exception as exc:
                    failed[classify_fcm_error(exc)].append(token)
                    errors.append(f"{token[:12]}…: {exc}")
            failed["errors"] = errors  # type: ignore[assignment]
            return accepted, failed

        accepted, failed = await asyncio.to_thread(_send_all)
        stale = tuple(failed[STALE])
        if accepted:
            return SendResult(provider_message_id=accepted[0], invalid_destinations=stale)

        detail = "; ".join(failed["errors"])
        if failed[AUTH]:
            raise ProviderSendError(
                f"FCM credentials rejected: {detail}",
                retryable=False,
                invalid_destinations=stale,
            )
        if failed[TRANSIENT]:
            raise ProviderSendError(
                f"FCM unavailable: {detail}", retryable=True, invalid_destinations=stale
            )
        # Dead tokens or an invalid request: the recipient is unreachable by
        # push, and no other push provider can fix that.
        raise ProviderSendError(
            f"Push rejected: {detail}",
            retryable=False,
            failover=False,
            invalid_destinations=stale,
        )


async def check_fcm(credentials_json: str) -> tuple[bool, str]:
    """
    Validates a service account without notifying anyone: a dry-run send to
    a dummy token. FCM answering "bad token" proves the credentials worked.
    """
    try:
        info = json.loads(credentials_json)
    except ValueError:
        return False, "Service account JSON is not valid JSON"
    if not isinstance(info, dict) or info.get("type") != "service_account":
        return False, "This is not a Firebase service account key (\"type\" must be service_account)"

    def _probe() -> tuple[bool, str]:
        from firebase_admin import messaging

        try:
            app = _get_app(credentials_json, None)
            messaging.send(
                messaging.Message(data={"probe": "1"}, token="pulse-credential-check"),
                dry_run=True,
                app=app,
            )
        except Exception as exc:
            kind = classify_fcm_error(exc)
            if kind in (STALE, PERMANENT):
                return True, f"Credentials accepted by project {info.get('project_id', '?')}"
            if kind == AUTH:
                return False, f"Firebase rejected the credentials: {exc}"
            return False, f"Could not verify (Firebase unavailable?): {exc}"
        return True, "Credentials accepted"

    try:
        return await asyncio.to_thread(_probe)
    except ValueError as exc:  # malformed key material
        return False, f"Invalid service account key: {exc}"
