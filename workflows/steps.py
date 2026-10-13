"""
Multi-step workflows (E2): an ordered sequence of sends with delays and
conditions, e.g. in-app -> wait 10 minutes -> email if the in-app message
is still unread.

A step is a channel template plus:
  delay  how long after the previous step ran (seconds, or "30s"/"10m"/"2h"/"1d")
  when   an optional condition, evaluated when the step is due:
           {"step": "<earlier step id>",            # default: the previous step
            "status": "delivered" | "not_delivered" | "failed" | "skipped",
            "payload": [{"field": "plan", "op": "eq", "value": "pro"}]}
         status and payload checks must all hold. "unread" and "read" are
         accepted as aliases for not_delivered and delivered.

A step that depends on a skipped step is skipped too (so a chain stops once
anyone has read the message), unless the condition asks for `skipped`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from domain.enums import ChannelType
from workflows.channel_template import ChannelTemplate, decode_channel_template

MAX_STEPS = 10
MAX_DELAY_SECONDS = 30 * 24 * 3600

STATUSES = ("delivered", "not_delivered", "failed", "skipped")
_ALIASES = {"read": "delivered", "unread": "not_delivered"}
OPS = ("eq", "ne", "in", "not_in", "exists", "gt", "gte", "lt", "lte", "contains")

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_DURATION = re.compile(r"^(\d+)\s*([smhd])$")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class InvalidSteps(ValueError):
    """The steps section of a workflow definition is malformed."""


def parse_duration(raw: Any) -> int:
    if isinstance(raw, bool):
        raise InvalidSteps("delay must be a number of seconds or a duration like '10m'")
    if isinstance(raw, int):
        seconds = raw
    elif isinstance(raw, str) and (m := _DURATION.match(raw.strip().lower())):
        seconds = int(m.group(1)) * _UNITS[m.group(2)]
    else:
        raise InvalidSteps(f"delay {raw!r} must be a number of seconds or a duration like '10m'")
    if not 0 <= seconds <= MAX_DELAY_SECONDS:
        raise InvalidSteps(f"delay must be between 0 and {MAX_DELAY_SECONDS // 86400} days")
    return seconds


@dataclass(frozen=True, slots=True)
class PayloadCheck:
    field: str
    op: str
    value: Any = None


@dataclass(frozen=True, slots=True)
class Condition:
    step: str | None = None          # None = the previous step
    status: str | None = None
    payload: tuple[PayloadCheck, ...] = ()


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    template: ChannelTemplate
    delay_seconds: int = 0
    when: Condition | None = None

    @property
    def channel(self) -> ChannelType:
        return self.template.channel


# ── parsing ────────────────────────────────────────────────────────────


def _parse_check(raw: Any) -> PayloadCheck:
    if not isinstance(raw, dict) or not isinstance(raw.get("field"), str) or not raw["field"]:
        raise InvalidSteps("each payload check needs a 'field'")
    op = raw.get("op", "eq")
    if op not in OPS:
        raise InvalidSteps(f"unknown payload operator '{op}' (use one of {', '.join(OPS)})")
    if op in ("in", "not_in") and not isinstance(raw.get("value"), list):
        raise InvalidSteps(f"operator '{op}' needs a list value")
    if op not in ("exists",) and "value" not in raw:
        raise InvalidSteps(f"operator '{op}' needs a value")
    return PayloadCheck(field=raw["field"], op=op, value=raw.get("value", True))


def _parse_condition(raw: Any) -> Condition:
    if not isinstance(raw, dict):
        raise InvalidSteps("'when' must be an object")
    unknown = set(raw) - {"step", "status", "payload"}
    if unknown:
        raise InvalidSteps(f"unknown keys in 'when': {', '.join(sorted(unknown))}")
    status = raw.get("status")
    if status is not None:
        status = _ALIASES.get(status, status)
        if status not in STATUSES:
            raise InvalidSteps(f"unknown status '{raw['status']}' (use one of {', '.join(STATUSES)})")
    step = raw.get("step")
    if step is not None and not isinstance(step, str):
        raise InvalidSteps("'when.step' must be a step id")
    payload = raw.get("payload", [])
    if not isinstance(payload, list):
        raise InvalidSteps("'when.payload' must be a list of checks")
    if step is not None and status is None:
        raise InvalidSteps("'when.step' needs a 'status' to test")
    return Condition(step=step, status=status, payload=tuple(_parse_check(c) for c in payload))


def parse_steps(raw: list[Any]) -> tuple[Step, ...]:
    if not isinstance(raw, list) or not raw:
        raise InvalidSteps("steps must be a non-empty list")
    if len(raw) > MAX_STEPS:
        raise InvalidSteps(f"a workflow can have at most {MAX_STEPS} steps")
    steps: list[Step] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise InvalidSteps(f"step {i + 1} must be an object")
        sid = item.get("id", f"step-{i + 1}")
        if not isinstance(sid, str) or not _ID.match(sid):
            raise InvalidSteps(f"step id {sid!r} must be lowercase letters, digits, '-' or '_' (max 40)")
        try:
            template = decode_channel_template(item)
        except KeyError as exc:
            raise InvalidSteps(f"step '{sid}' is missing {exc.args[0]!r}") from exc
        except ValueError as exc:
            raise InvalidSteps(f"step '{sid}': {exc}") from exc
        try:
            delay = parse_duration(item.get("delay", item.get("delay_seconds", 0)))
            when = _parse_condition(item["when"]) if item.get("when") is not None else None
        except InvalidSteps as exc:
            raise InvalidSteps(f"step '{sid}': {exc}") from exc
        steps.append(Step(id=sid, template=template, delay_seconds=delay, when=when))
    validate_steps(steps)
    return tuple(steps)


def validate_steps(steps: list[Step] | tuple[Step, ...]) -> None:
    seen: dict[str, int] = {}
    channels: set[ChannelType] = set()
    for i, step in enumerate(steps):
        if step.id in seen:
            raise InvalidSteps(f"duplicate step id '{step.id}'")
        if step.channel in channels:
            # One delivery attempt exists per (notification, recipient, channel).
            raise InvalidSteps(f"channel '{step.channel.value}' is used by more than one step")
        channels.add(step.channel)
        cond = step.when
        if cond is not None and cond.status is not None:
            ref = cond.step or (steps[i - 1].id if i > 0 else None)
            if ref is None:
                raise InvalidSteps(f"step '{step.id}' is first, so its condition has no earlier step to test")
            if ref not in seen:
                raise InvalidSteps(f"step '{step.id}' tests '{ref}', which is not an earlier step")
        seen[step.id] = i


def encode_steps(steps: tuple[Step, ...]) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        item: dict[str, Any] = {"id": s.id, "channel": s.template.channel.value}
        if s.template.template:
            item["template"] = s.template.template
        else:
            item.update(subject=s.template.subject, body=s.template.body, format=s.template.format.value)
        item["delay_seconds"] = s.delay_seconds
        if s.when is not None:
            when: dict[str, Any] = {}
            if s.when.step:
                when["step"] = s.when.step
            if s.when.status:
                when["status"] = s.when.status
            if s.when.payload:
                when["payload"] = [{"field": c.field, "op": c.op, "value": c.value} for c in s.when.payload]
            item["when"] = when
        out.append(item)
    return out


# ── evaluation ─────────────────────────────────────────────────────────


def _lookup(payload: dict[str, Any], path: str) -> tuple[bool, Any]:
    cur: Any = payload
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def check_payload(check: PayloadCheck, payload: dict[str, Any]) -> bool:
    found, value = _lookup(payload, check.field)
    if check.op == "exists":
        return found == bool(check.value)
    if not found:
        return check.op in ("ne", "not_in")
    try:
        match check.op:
            case "eq":
                return value == check.value
            case "ne":
                return value != check.value
            case "in":
                return value in check.value
            case "not_in":
                return value not in check.value
            case "gt":
                return value > check.value
            case "gte":
                return value >= check.value
            case "lt":
                return value < check.value
            case "lte":
                return value <= check.value
            case "contains":
                return check.value in value
    except TypeError:
        return False   # comparing incompatible types never matches
    return False


def status_matches(wanted: str, state: str) -> bool:
    """`state` is what a step ended up as: delivered, pending, failed or skipped."""
    if wanted == "skipped":
        return state == "skipped"
    if state == "skipped":
        return False       # depending on a skipped step skips this one too
    if wanted == "delivered":
        return state == "delivered"
    if wanted == "failed":
        return state == "failed"
    return state in ("pending", "failed")   # not_delivered
