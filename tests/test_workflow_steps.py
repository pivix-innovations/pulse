from __future__ import annotations

import pytest

from workflows.registry import WorkflowDefinition
from workflows.steps import (
    InvalidSteps,
    PayloadCheck,
    check_payload,
    parse_duration,
    parse_steps,
    status_matches,
)


def step(id, channel, **extra):
    return {"id": id, "channel": channel, "subject": "s", "body": "b", "format": "text", **extra}


# ── durations ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, seconds", [(0, 0), (90, 90), ("30s", 30), ("10m", 600), ("2h", 7200), ("1d", 86400), (" 5M ", 300)])
def test_durations_accept_seconds_or_units(raw, seconds):
    assert parse_duration(raw) == seconds


@pytest.mark.parametrize("raw", [-1, "soon", "10", "1w", True, 31 * 86400, None, 1.5])
def test_bad_durations_are_rejected(raw):
    with pytest.raises(InvalidSteps):
        parse_duration(raw)


# ── parsing and validation ─────────────────────────────────────────────


def test_a_sequence_parses_and_round_trips():
    raw = [
        step("nudge", "in_app"),
        step("mail", "email", delay="10m", when={"status": "unread"}),
        step("text", "sms", delay="1h", when={"step": "nudge", "status": "not_delivered",
                                             "payload": [{"field": "plan", "op": "in", "value": ["pro", "team"]}]}),
    ]
    steps = parse_steps(raw)
    assert [s.id for s in steps] == ["nudge", "mail", "text"]
    assert [s.delay_seconds for s in steps] == [0, 600, 3600]
    assert steps[1].when.status == "not_delivered" and steps[1].when.step is None   # "unread" alias
    again = WorkflowDefinition.from_dict("w", "e", {"steps": raw}).as_dict()["steps"]
    assert parse_steps(again) == steps


def test_ids_default_to_their_position():
    steps = parse_steps([step_without_id("in_app"), step_without_id("email")])
    assert [s.id for s in steps] == ["step-1", "step-2"]


def step_without_id(channel):
    return {"channel": channel, "body": "b", "format": "text"}


@pytest.mark.parametrize("raw, message", [
    ([], "non-empty"),
    ("nope", "non-empty"),
    ([step("a", "email"), step("a", "sms")], "duplicate step id"),
    ([step("a", "email"), step("b", "email")], "more than one step"),
    ([step("A!", "email")], "lowercase"),
    ([step("a", "carrier_pigeon")], "carrier_pigeon"),
    ([{"id": "a", "channel": "email"}], "body"),
    ([step("a", "email", when={"status": "unread"})], "first"),
    ([step("a", "email"), step("b", "sms", when={"step": "zzz", "status": "unread"})], "not an earlier step"),
    ([step("a", "email"), step("b", "sms", when={"step": "b", "status": "unread"})], "not an earlier step"),
    ([step("a", "email"), step("b", "sms", when={"status": "bogus"})], "unknown status"),
    ([step("a", "email"), step("b", "sms", when={"step": "a"})], "status"),
    ([step("a", "email"), step("b", "sms", when={"payload": [{"field": "x", "op": "wat", "value": 1}]})], "operator"),
    ([step("a", "email"), step("b", "sms", when={"payload": [{"field": "x", "op": "in", "value": 1}]})], "list"),
    ([step("a", "email"), step("b", "sms", when={"colour": "red"})], "unknown keys"),
    ([step("a", "email", delay="soon")], "delay"),
    ([step(f"s{i}", c) for i, c in enumerate(["email", "sms", "push", "in_app"])] + [step("x", "email")], "more than one step"),
])
def test_malformed_sequences_are_rejected_with_a_reason(raw, message):
    with pytest.raises(InvalidSteps, match=message):
        parse_steps(raw)


def test_at_most_ten_steps():
    from domain.enums import ChannelType
    # only four channels exist, so build the oversized list to hit the cap check first
    raw = [step(f"s{i}", "email") for i in range(11)]
    with pytest.raises(InvalidSteps, match="at most 10"):
        parse_steps(raw)
    assert len(ChannelType) >= 4


# ── conditions ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("op, value, expected", [
    ("eq", "pro", True), ("eq", "free", False), ("ne", "free", True), ("ne", "pro", False),
    ("in", ["pro", "team"], True), ("in", ["free"], False), ("not_in", ["free"], True),
    ("contains", "r", True), ("contains", "z", False),
])
def test_payload_operators_on_a_string(op, value, expected):
    assert check_payload(PayloadCheck("plan", op, value), {"plan": "pro"}) is expected


@pytest.mark.parametrize("op, value, expected", [("gt", 5, True), ("gt", 10, False), ("gte", 10, True), ("lt", 11, True), ("lte", 9, False)])
def test_payload_comparisons_on_a_number(op, value, expected):
    assert check_payload(PayloadCheck("amount", op, value), {"amount": 10}) is expected


def test_nested_fields_and_missing_fields():
    payload = {"order": {"total": 80, "items": ["a", "b"]}}
    assert check_payload(PayloadCheck("order.total", "gt", 50), payload)
    assert check_payload(PayloadCheck("order.items", "contains", "b"), payload)
    assert check_payload(PayloadCheck("order.coupon", "exists", False), payload)
    assert check_payload(PayloadCheck("order.total", "exists", True), payload)
    assert not check_payload(PayloadCheck("order.coupon", "eq", "X"), payload)   # missing never equals
    assert check_payload(PayloadCheck("order.coupon", "ne", "X"), payload)
    assert not check_payload(PayloadCheck("order.total", "gt", "lots"), payload)  # incompatible types never match


@pytest.mark.parametrize("wanted, state, expected", [
    ("delivered", "delivered", True), ("delivered", "pending", False), ("delivered", "failed", False),
    ("not_delivered", "pending", True), ("not_delivered", "failed", True), ("not_delivered", "delivered", False),
    ("failed", "failed", True), ("failed", "pending", False),
    ("skipped", "skipped", True), ("skipped", "delivered", False),
    # depending on a step that never ran skips the dependent step too
    ("not_delivered", "skipped", False), ("delivered", "skipped", False), ("failed", "skipped", False),
])
def test_status_matching(wanted, state, expected):
    assert status_matches(wanted, state) is expected


def test_existing_definitions_are_not_sequences():
    d = WorkflowDefinition.from_dict("w", "e", {"channels": [{"channel": "email", "body": "b"}], "fallbacks": {}})
    assert not d.is_sequence and "steps" not in d.as_dict()
