"""API key scopes: what a machine credential is allowed to do."""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Scope(str, Enum):
    INGEST = "ingest"   # POST /events: producers sending events
    READ = "read"       # GET endpoints: notifications, metrics, lists, configuration
    CLIENT = "client"   # end-user apps: SSE stream, ack, device registration, preferences
    ADMIN = "admin"     # implies everything, and is the only scope that changes configuration


ALL_SCOPES: frozenset[str] = frozenset(s.value for s in Scope)


def satisfies(held: Iterable[str], accepted: Iterable[str]) -> bool:
    """True if `held` includes any of `accepted`; `admin` satisfies everything."""
    held = set(held)
    return Scope.ADMIN.value in held or bool(held & set(accepted))
