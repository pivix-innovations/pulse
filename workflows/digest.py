"""
Digest / batching (E3): instead of sending every event, collect a recipient's
events for a while and send ONE consolidated message.

    "digest": {"window": "10m", "max_items": 50, "key": "post_id"}

  window     how long after the first buffered event the digest is sent
             (seconds or "30s"/"10m"/"2h"/"1d", 1 second to 7 days)
  max_items  send early once this many events are buffered (default 100)
  key        optional payload field: events with different values digest
             separately (e.g. one digest per post). Dotted paths work.

Templates render with the last event's payload plus a `digest` object:
`digest.count`, `digest.items` (every payload, oldest first), `digest.first`,
`digest.last`. One template covers both a single event and a batch:
`{% if digest.count > 1 %}{{ digest.count }} new comments{% else %}{{ comment }}{% endif %}`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from workflows.steps import InvalidSteps, parse_duration

MAX_WINDOW_SECONDS = 7 * 24 * 3600
DEFAULT_MAX_ITEMS = 100
MAX_ITEMS_LIMIT = 1000


class InvalidDigest(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DigestConfig:
    window_seconds: int
    max_items: int = DEFAULT_MAX_ITEMS
    key: str | None = None

    def key_for(self, payload: dict[str, Any]) -> str:
        """The grouping value for an event ("" when there is no key or the field is missing)."""
        if not self.key:
            return ""
        cur: Any = payload
        for part in self.key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return ""
        return str(cur)[:200]

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"window_seconds": self.window_seconds, "max_items": self.max_items}
        if self.key:
            out["key"] = self.key
        return out


def parse_digest(raw: Any) -> DigestConfig:
    if not isinstance(raw, dict):
        raise InvalidDigest("digest must be an object")
    unknown = set(raw) - {"window", "window_seconds", "max_items", "key"}
    if unknown:
        raise InvalidDigest(f"unknown keys in digest: {', '.join(sorted(unknown))}")
    window_raw = raw.get("window", raw.get("window_seconds"))
    if window_raw is None:
        raise InvalidDigest("digest needs a 'window', e.g. \"10m\"")
    try:
        window = parse_duration(window_raw)
    except InvalidSteps as exc:
        raise InvalidDigest(f"digest window: {exc}") from exc
    if not 1 <= window <= MAX_WINDOW_SECONDS:
        raise InvalidDigest("digest window must be between 1 second and 7 days")
    max_items = raw.get("max_items", DEFAULT_MAX_ITEMS)
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 2 <= max_items <= MAX_ITEMS_LIMIT:
        raise InvalidDigest(f"digest max_items must be a whole number from 2 to {MAX_ITEMS_LIMIT}")
    key = raw.get("key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise InvalidDigest("digest key must be a payload field name")
    return DigestConfig(window_seconds=window, max_items=max_items, key=key.strip() if key else None)
