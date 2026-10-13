"""Shared plumbing for drivers that talk to a provider's HTTPS API."""
from __future__ import annotations

from typing import Any

import httpx

from services.ports.providers import ProviderSendError

DEFAULT_TIMEOUT = 20.0


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, transport=transport)


def raise_for_status(
    response: httpx.Response,
    *,
    provider: str,
    detail: str = "",
    bad_destination_statuses: frozenset[int] = frozenset(),
    bad_destination_codes: frozenset[Any] = frozenset(),
    code: Any = None,
) -> None:
    """
    Maps an HTTP failure onto ProviderSendError semantics:

    - 5xx / 429 / 408: the provider is struggling -> retry and fail over.
    - 401 / 403: our credentials are wrong -> don't retry, but another
      provider may work.
    - a rejected destination (bad number, invalid address): the recipient's
      problem -> no retry, no failover.
    - any other 4xx: the request itself is invalid -> no retry, no failover.
    """
    status = response.status_code
    if status < 400:
        return
    text = f"{provider} returned {status}" + (f": {detail}" if detail else "")
    if status in (408, 429) or status >= 500:
        raise ProviderSendError(text, retryable=True, failover=True)
    if status in (401, 403):
        raise ProviderSendError(text, retryable=False, failover=True)
    if status in bad_destination_statuses or code in bad_destination_codes:
        raise ProviderSendError(text, retryable=False, failover=False)
    raise ProviderSendError(text, retryable=False, failover=False)


def transport_error(provider: str, exc: Exception) -> ProviderSendError:
    """Timeouts and connection failures: provider unreachable -> retry and fail over."""
    return ProviderSendError(f"{provider} unreachable: {exc!r}", retryable=True, failover=True)


def error_detail(response: httpx.Response) -> tuple[str, Any]:
    """Best-effort (message, code) from a JSON error body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200], None
    if isinstance(body, dict):
        message = body.get("message") or body.get("error") or body.get("detail")
        if message is None and isinstance(body.get("errors"), list) and body["errors"]:
            first = body["errors"][0]
            message = first.get("message") if isinstance(first, dict) else str(first)
        return str(message or body)[:300], body.get("code") or body.get("name")
    return str(body)[:300], None
