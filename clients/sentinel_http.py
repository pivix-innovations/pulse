from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

import httpx

from config.sentinel import SentinelSettings
from domain.value_objects.ids import TenantId
from services.ports.clients import ResolvedRecipient, SentinelUnavailable

logger = logging.getLogger(__name__)


class HttpSentinelClient:
    """One call to Sentinel's resolve endpoint (docs/sentinel-contract.md); no resilience."""

    def __init__(
        self, settings: SentinelSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._s = settings
        self._client = httpx.AsyncClient(
            base_url=(settings.base_url or "").rstrip("/"),
            timeout=httpx.Timeout(
                settings.read_timeout_seconds, connect=settings.connect_timeout_seconds
            ),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _body(self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        keep = self._s.payload_keys
        forwarded = {k: v for k, v in payload.items() if k != "recipients" and (keep is None or k in keep)}
        return {"tenant_id": str(tenant_id), "event_type": event_type, "payload": forwarded}

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]:
        headers = {"X-Pulse-Tenant-Id": str(tenant_id)}
        if self._s.token:
            headers["Authorization"] = f"Bearer {self._s.token}"
        try:
            response = await self._client.post(
                self._s.resolve_path, json=self._body(tenant_id, event_type, payload), headers=headers
            )
        except httpx.HTTPError as exc:  # timeouts, refused connections, TLS, ...
            raise SentinelUnavailable(f"Sentinel unreachable: {exc!r}") from exc

        status = response.status_code
        if status == 404:
            return []                                          # Sentinel knows of nobody: a real answer
        if status in (401, 403):
            raise SentinelUnavailable(
                f"Sentinel rejected our credentials ({status}); check PULSE_SENTINEL__TOKEN", retryable=False
            )
        if status == 429 or status >= 500:
            raise SentinelUnavailable(f"Sentinel returned {status}")
        if status != 200:
            raise SentinelUnavailable(f"Sentinel returned unexpected {status}", retryable=False)
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> list[ResolvedRecipient]:
        try:
            items = response.json()["recipients"]
            if not isinstance(items, list):
                raise TypeError("recipients is not a list")
        except (ValueError, KeyError, TypeError) as exc:
            raise SentinelUnavailable(f"Sentinel sent an unusable response: {exc}", retryable=False) from exc
        if len(items) > self._s.max_recipients:
            raise SentinelUnavailable(
                f"Sentinel returned {len(items)} recipients (limit {self._s.max_recipients})", retryable=False
            )
        out: list[ResolvedRecipient] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("external_ref"):
                logger.warning("Ignoring a Sentinel recipient without an external_ref: %r", item)
                continue
            out.append(
                ResolvedRecipient(
                    external_ref=str(item["external_ref"]),
                    email=item.get("email"),
                    phone=item.get("phone"),
                    locale=item.get("locale") or "en",
                    timezone=item.get("timezone") or "UTC",
                )
            )
        return out


@dataclass(slots=True)
class _Entry:
    value: list[ResolvedRecipient]
    fresh_until: float
    stale_until: float


class ResilientSentinelClient:
    """
    Wraps a Sentinel client with what a hot path needs:

    - a short cache (and a shorter one for "nobody"), so repeated events for
      the same recipients don't hammer Sentinel;
    - single-flight: identical concurrent lookups share one call;
    - retries with jittered backoff for transient failures;
    - a circuit breaker, so a dead Sentinel is not retried on every event;
    - stale-if-error: when Sentinel is down, the last good answer is served
      (up to `stale_ttl_seconds`). With no answer to fall back on it raises
      SentinelUnavailable; it never pretends there are no recipients.
    """

    def __init__(
        self,
        inner,
        settings: SentinelSettings,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._s = settings
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[str, _Entry] = {}
        self._inflight: dict[str, asyncio.Future] = {}
        self._failures = 0
        self._open_until = 0.0

    @staticmethod
    def _key(tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]) -> str:
        body = {k: v for k, v in payload.items() if k != "recipients"}
        raw = json.dumps([str(tenant_id), event_type, body], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def circuit_open(self) -> bool:
        return self._clock() < self._open_until

    async def resolve_recipients(
        self, tenant_id: TenantId, event_type: str, payload: Mapping[str, Any]
    ) -> list[ResolvedRecipient]:
        key = self._key(tenant_id, event_type, payload)
        now = self._clock()
        entry = self._cache.get(key)
        if entry and now < entry.fresh_until:
            return entry.value

        pending = self._inflight.get(key)
        if pending is not None:                       # someone is already asking
            return await asyncio.shield(pending)

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            result = await self._fetch(key, entry, tenant_id, event_type, payload)
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()                        # mark retrieved: no "never retrieved" noise
            raise
        finally:
            self._inflight.pop(key, None)

    async def _fetch(self, key, entry, tenant_id, event_type, payload) -> list[ResolvedRecipient]:
        if self.circuit_open:
            return self._stale_or_raise(entry, SentinelUnavailable("Sentinel circuit open", retryable=True))
        try:
            value = await self._call(tenant_id, event_type, payload)
        except SentinelUnavailable as exc:
            self._record_failure(exc)
            return self._stale_or_raise(entry, exc)
        self._failures = 0
        ttl = self._s.cache_ttl_seconds if value else self._s.negative_cache_ttl_seconds
        now = self._clock()
        self._cache[key] = _Entry(value, now + ttl, now + max(ttl, self._s.stale_ttl_seconds))
        return value

    async def _call(self, tenant_id, event_type, payload) -> list[ResolvedRecipient]:
        attempt = 0
        while True:
            try:
                return await self._inner.resolve_recipients(tenant_id, event_type, payload)
            except SentinelUnavailable as exc:
                if not exc.retryable or attempt >= self._s.retries:
                    raise
                attempt += 1
                delay = self._s.retry_backoff_seconds * (2 ** (attempt - 1))
                await self._sleep(delay * (0.5 + random.random() / 2))

    def _record_failure(self, exc: SentinelUnavailable) -> None:
        self._failures += 1
        if self._failures >= self._s.breaker_failure_threshold:
            first = self._clock() >= self._open_until
            self._open_until = self._clock() + self._s.breaker_cooldown_seconds
            if first:
                logger.error(
                    "Sentinel failed %d lookups in a row (%s); not calling it for %.0fs",
                    self._failures, exc, self._s.breaker_cooldown_seconds,
                )

    def _stale_or_raise(self, entry: _Entry | None, exc: SentinelUnavailable) -> list[ResolvedRecipient]:
        if entry is not None and self._clock() < entry.stale_until:
            logger.warning("Sentinel unavailable (%s); serving the last known recipients", exc)
            return entry.value
        raise exc
